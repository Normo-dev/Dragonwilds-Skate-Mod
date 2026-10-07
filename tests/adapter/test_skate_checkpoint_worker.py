"""One-job checkpoint helper: synthetic cache data and private process fixtures."""
from contextlib import ExitStack
from pathlib import Path
import copy
import io
import json
import os
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

import skate_checkpoint_worker as worker
import skate_cache_retention as retention
import test_skate_cache_retention as fixtures


class CheckpointWorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RetentionTests()
        self.fixture.setUp()
        self.row = self.fixture.accept(1, publish=False)
        self.request = self.request_for(self.row)

    def tearDown(self):
        self.fixture.tearDown()

    def request_for(self, row, op='remember'):
        result = copy.deepcopy(row['response'])
        if op == 'promote':
            result['collision_revision'] = row['descriptor']['revision']
        return {'op': op, 'root': str(self.fixture.root), 'worker': str(self.fixture.worker),
                'descriptor': {k: row['descriptor'][k] for k in ('mode', 'world_session', 'revision')},
                'cached': copy.deepcopy(row['cached']), 'result': result, 'parent_pid': 123}

    def invoke(self, request=None, *, raw=None, existing=True, dead=False):
        output, diagnostics = io.StringIO(), io.StringIO()
        raw = json.dumps(request if request is not None else self.request) + '\n' if raw is None else raw
        stream = io.BytesIO(raw) if isinstance(raw, bytes) else io.StringIO(raw)
        with patch('skate_process.Singleton') as singleton, patch('skate_process.ParentProcess') as parent:
            singleton.return_value.__enter__.return_value.acquired = not existing
            parent.return_value.__enter__.return_value.exited.return_value = dead
            code = worker.checkpoint_child(stream, output, diagnostics)
        self.assertEqual(code, 0)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        response = json.loads(lines[0])
        self.assertEqual(set(response), {'ok', 'retained', 'error'})
        self.assertLess(len(lines[0]), 3200)
        return response, diagnostics.getvalue()

    def test_fresh_native_zero_checkpoint_uses_actual_store_and_catalog(self):
        response, diagnostics = self.invoke()
        self.assertEqual(response, {'ok': True, 'retained': True, 'error': None})
        self.assertEqual(diagnostics, '')
        self.assertTrue(self.row['pointer'].is_file())
        catalog = retention._load(self.fixture.root)
        self.assertEqual(len(catalog['records']), 1)
        self.assertTrue(all(path.read_bytes() == b'preserved' for path in self.fixture.sentinels))

    def test_accepted_revision_promotes_actual_second_checkpoint(self):
        self.invoke()
        second = self.fixture.accept(2, publish=False)
        response, _ = self.invoke(self.request_for(second, 'promote'))
        self.assertTrue(response['retained'])
        saved = json.loads(second['pointer'].read_text())
        self.assertEqual(saved['scene_fingerprint'], second['cached']['report']['scene_fingerprint'])
        self.assertEqual(len(retention._load(self.fixture.root)['records']), 2)

    def test_catalog_failure_is_explicit_and_does_not_erase_valid_pointer(self):
        with patch.object(retention, 'record_accepted', return_value=False):
            response, _ = self.invoke()
        self.assertTrue(response['ok'])
        self.assertFalse(response['retained'])
        self.assertIn('not confirmed', response['error'])
        self.assertTrue(self.row['pointer'].is_file())

    def test_identity_revision_and_reduced_payload_validation_before_store(self):
        cases = []
        def altered(change):
            value = copy.deepcopy(self.request); change(value); cases.append(value)
        altered(lambda v: v['result'].__setitem__('ok', False))
        altered(lambda v: v['result'].__setitem__('collision_revision', 1))
        altered(lambda v: (v.__setitem__('op', 'promote'), v['result'].__setitem__('collision_revision', 2)))
        altered(lambda v: v['cached']['report'].__setitem__('world_session', 'other-save'))
        altered(lambda v: v['result']['world'].__setitem__('scene_fingerprint', 'e' * 64))
        altered(lambda v: v['descriptor'].__setitem__('generation', 'unreduced-field'))
        altered(lambda v: v['result']['world'].__setitem__('scene_checkpoint', None))
        altered(lambda v: v['result']['world']['scene_checkpoint'].__setitem__('format', 'accepted_incremental_v1'))
        altered(lambda v: v['result']['world']['scene_checkpoint'].__setitem__('durable', False))
        altered(lambda v: v['result']['world']['scene_checkpoint']['rail_cache'].__setitem__('ready', False))
        altered(lambda v: v.__setitem__('parent_pid', True))
        altered(lambda v: v.__setitem__('parent_pid', os.getpid()))
        with patch('skate_warm_world.WarmWorldStore') as store:
            for value in cases:
                with self.subTest(value=value):
                    response, _ = self.invoke(value)
                    self.assertFalse(response['ok'])
                    self.assertFalse(response['retained'])
            store.assert_not_called()
        self.assertFalse(self.row['pointer'].exists())

    def test_bounded_strict_json_rejects_duplicates_nonfinite_and_oversize(self):
        duplicate = json.dumps(self.request).replace('"op": "remember"', '"op": "remember", "op": "promote"')
        nonfinite = json.dumps(self.request).replace('"parent_pid": 123', '"parent_pid": NaN')
        raw = [b'x' * (worker.MAX_REQUEST_BYTES + 1), duplicate, nonfinite,
               '{' * 2000, '\ud800', b'\xff\n']
        with patch('skate_warm_world.WarmWorldStore') as store:
            for value in raw:
                with self.subTest(raw_type=type(value).__name__):
                    response, _ = self.invoke(raw=value)
                    self.assertFalse(response['ok'])
            store.assert_not_called()

    def test_canonical_paths_and_cache_containment_are_required(self):
        cases = []
        for key, value in [('root', '.'), ('worker', str(self.fixture.worker.parent / '..' / self.fixture.worker.parent.name / self.fixture.worker.name))]:
            request = copy.deepcopy(self.request); request[key] = value; cases.append(request)
        escaped = copy.deepcopy(self.request)
        escaped['cached']['scene_overlay'] = str(self.fixture.worker)
        cases.append(escaped)
        escaped = copy.deepcopy(self.request)
        escaped['result']['world']['scene_checkpoint']['manifest_file'] = str(self.fixture.base)
        cases.append(escaped)
        with patch('skate_warm_world.WarmWorldStore') as store:
            for request in cases:
                response, _ = self.invoke(request)
                self.assertFalse(response['ok'])
            store.assert_not_called()

    def test_no_launcher_or_dead_parent_cannot_publish(self):
        with patch('skate_warm_world.WarmWorldStore') as store:
            for existing, dead in [(False, False), (True, True)]:
                response, _ = self.invoke(existing=existing, dead=dead)
                self.assertFalse(response['ok'])
            store.assert_not_called()
        self.assertFalse(self.row['pointer'].exists())

    def test_stdout_diagnostics_are_redirected_and_one_job_only(self):
        calls = []
        class Store:
            def __init__(self, root, native):
                print('constructor diagnostic')
            def remember(self, *args):
                print('checkpoint diagnostic')
                calls.append(args)
                return True
        line = json.dumps(self.request) + '\n'
        with patch('skate_warm_world.WarmWorldStore', Store):
            response, diagnostics = self.invoke(raw=line + line)
        self.assertTrue(response['retained'])
        self.assertEqual(len(calls), 1)
        self.assertIn('constructor diagnostic', diagnostics)
        self.assertIn('checkpoint diagnostic', diagnostics)

    def test_store_errors_and_nonboolean_status_stay_bounded(self):
        for returned, failure in [(None, None), (True, ValueError('x' * 4000))]:
            with patch('skate_warm_world.WarmWorldStore') as store:
                store.return_value.remember.return_value = returned
                store.return_value.remember.side_effect = failure
                response, _ = self.invoke()
            self.assertFalse(response['ok'])
            self.assertLessEqual(len(response['error']), worker.MAX_ERROR_CHARS)

    @unittest.skipUnless(os.name == 'nt', 'Windows managed child lifetime')
    def test_actual_separate_child_stdin_handshake_with_private_singleton(self):
        from skate_process import ChildJob, Singleton
        name = 'Local\\DragonwildsSkate.CheckpointTest.' + uuid.uuid4().hex
        program = (
            'import sys\n'
            f'sys.path.insert(0, {str(Path(__file__).parent.resolve())!r})\n'
            'from unittest.mock import patch\n'
            'from skate_process import Singleton\n'
            'from skate_checkpoint_worker import checkpoint_child\n'
            f'with patch("skate_process.Singleton", lambda: Singleton({name!r})):\n'
            '    raise SystemExit(checkpoint_child(sys.stdin.buffer,sys.stdout))\n'
        )
        request = copy.deepcopy(self.request); request['parent_pid'] = os.getpid()
        with Singleton(name) as keeper, ChildJob() as job:
            self.assertTrue(keeper.acquired)
            process = subprocess.Popen([sys.executable, '-c', program], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            try:
                job.assign(process)  # The child is waiting for its only request.
                out, err = process.communicate(json.dumps(request) + '\n', timeout=20)
                self.assertEqual(process.returncode, 0, err)
                self.assertEqual(json.loads(out), {'ok': True, 'retained': True, 'error': None})
                self.assertTrue(self.row['pointer'].exists())
            finally:
                if process.poll() is None:
                    job.close(); process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
