"""Isolated log/pipe tests: no native executable, game input or production IPC."""
import ast
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from skate_loading_progress import LoadingProgress, NativeReplyReader


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / 'native.log'
        self.log.write_text('WORLD_EXPOSED_PROGRESS cells=99 rebuilt=99 reused=0 total=100\n')
        self.now = 10.
        self.progress = LoadingProgress(self.log, clock=lambda: self.now)

    def append(self, line):
        with self.log.open('a') as stream:
            stream.write(line + '\n')

    def test_real_counts_and_unchanged_heartbeat(self):
        p = self.progress
        p.begin('new', 4)
        self.assertNotIn('completed', p.snapshot())
        p.native('world_scene')
        self.assertNotIn('completed', p.snapshot())  # Old log count is excluded.
        self.append('WORLD_EXPOSED_PROGRESS cells=25 rebuilt=20 reused=5 total=100')
        self.now += .5
        s = p.snapshot()
        self.assertEqual((s['completed'], s['total'], s['percent']), (25, 100, 25.))
        self.now += 60
        heartbeat = p.snapshot()
        self.assertEqual(heartbeat['completed'], 25)
        self.assertEqual(heartbeat['elapsed_seconds'], 60)
        self.append('DWS_CACHE_PHASE operation=scene phase=joining_grind_paths state=start elapsed_ms=60000')
        self.now += .5
        s = p.snapshot()
        self.assertEqual(s['phase'], 'joining_grind_paths')
        self.assertNotIn('percent', s)
        self.assertNotIn('completed', s)

    def test_unknown_total_has_no_percentage_and_phases_do_not_fake_completion(self):
        p = self.progress
        p.begin('test', 1)
        p.native('init_world')
        self.append('WORLD_EXPOSED_PROGRESS cells=3 rebuilt=1 reused=2')
        self.assertEqual(p.snapshot()['completed'], 3)
        self.assertNotIn('percent', p.snapshot())
        self.append('DWS_CACHE_PHASE operation=init phase=ready state=end elapsed_ms=42')
        self.now += .5
        self.assertEqual(p.snapshot()['phase'], 'ready')
        self.assertNotIn('percent', p.snapshot())
        self.assertIsNotNone(p.snapshot())  # Only the response grants readiness.
        p.finish()
        self.assertIsNone(p.snapshot())

    def test_cached_load_phase_passes_label_whitelist_and_clears_on_ready(self):
        p = self.progress
        p.begin('cached-world', 1)
        p.native('init_world')
        self.append('WORLD_EXPOSED_PROGRESS kind=load cells=40 rebuilt=0 reused=40 total=100')
        value = p.snapshot()
        self.assertEqual((value['phase'], value['label']),
                         ('loading_cached_edges', 'Loading saved grind edges'))
        self.assertEqual((value['completed'], value['total'], value['percent']), (40, 100, 40.))
        self.append('DWS_CACHE_PHASE operation=init phase=loading_cached_grind_data state=start elapsed_ms=10')
        self.now += .5
        value = p.snapshot()
        self.assertEqual(value['phase'], 'loading_cached_grind_data')
        self.assertNotIn('completed', value)
        p.finish()
        self.assertIsNone(p.snapshot())

    def test_rotation_truncation_new_generation_and_wrong_operation(self):
        p = self.progress
        p.begin('prewarm-host-generation', 0, prewarm=True)
        self.assertEqual(p.snapshot()['generation'], 'prewarm-host-generation')
        p.native('init_world')
        self.append('WORLD_EXPOSED_PROGRESS cells=1 rebuilt=1 reused=0 total=10')
        self.assertEqual(p.snapshot()['percent'], 10.)
        self.log.rename(self.log.with_suffix('.old'))
        self.log.write_text('')
        self.now += .5
        self.assertNotIn('completed', p.snapshot())
        self.append('DWS_CACHE_PHASE operation=scene phase=saving_grind_data state=start elapsed_ms=5')
        self.now += .5
        self.assertEqual(p.snapshot()['phase'], 'collision_load')
        self.append('WORLD_EXPOSED_PROGRESS cells=2 rebuilt=2 reused=0 total=10')
        self.now += .5
        self.assertEqual(p.snapshot()['completed'], 2)
        self.log.write_text('')
        self.now += .5
        self.assertNotIn('completed', p.snapshot())
        p.begin('next', 9)
        self.assertEqual(p.snapshot()['elapsed_seconds'], 0)
        self.assertNotIn('completed', p.snapshot())

    def test_poll_rate_and_sanitized_fields(self):
        p = self.progress
        p.begin('test', 7)
        p.native('world_scene')
        with patch.object(p.reader, 'poll', wraps=p.reader.poll) as poll:
            for _ in range(100):
                p.snapshot()
            self.assertEqual(poll.call_count, 1)
            self.now += .5
            p.snapshot()
            self.assertEqual(poll.call_count, 2)
        self.append('WORLD_EXPOSED_PROGRESS cells=100 rebuilt=100 reused=0 total=99')
        self.append('DWS_CACHE_PHASE operation=scene phase=private_path state=start elapsed_ms=0')
        self.now += .5
        self.assertNotIn('completed', p.snapshot())
        self.assertNotIn('started', p.snapshot())
        json.dumps(p.snapshot(), allow_nan=False)

    def test_actual_relay_status_callbacks_clear_on_accept_error_and_stop(self):
        tree = ast.parse((Path(__file__).parent / 'skate_relay.py').read_text())
        run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
        names = ('publish_status', 'loading_progress', 'status_heartbeat')
        definitions = [n for n in run.body if isinstance(n, ast.FunctionDef) and n.name in names]
        setup = ast.parse('''
def factory():
    status_value = {}
    last_loading_heartbeat = last_status_heartbeat = 0.
    status_serial = 0
    input_state = {'controller_name': None}
    selected_camera = summon_state = entry_status = None
''').body[0]
        setup.body += definitions
        setup.body += ast.parse('return publish_status, loading_progress, status_heartbeat').body
        module = ast.fix_missing_locations(ast.Module(body=[setup], type_ignores=[]))
        records = []
        stop = Path(self.temp.name) / 'stop'
        scope = {'loading': self.progress, 'stop_file': stop,
                 'time': SimpleNamespace(monotonic=lambda: self.now),
                 'publish': lambda name, data: records.append(dict(data))}
        exec(compile(module, '<actual relay status callbacks>', 'exec'), scope)
        publish_status, heartbeat, ordinary = scope['factory']()
        self.progress.begin('host-before-native-generation', 0, prewarm=True)
        heartbeat()
        self.assertEqual(records[-1]['generation'], 'host-before-native-generation')
        self.now += 3600
        heartbeat()
        self.assertEqual(records[-1]['progress']['elapsed_seconds'], 3600)
        publish_status({'status': 'ready', 'generation': 'host-before-native-generation'})
        ordinary(False, False)
        self.assertIsNone(records[-1]['progress'])
        self.progress.begin('host', 3)
        publish_status({'status': 'error', 'generation': 'host', 'failed_revision': 3})
        self.assertIsNone(records[-1]['progress'])
        self.progress.begin('host', 4)
        stop.write_text('isolated fixture')
        with self.assertRaisesRegex(RuntimeError, 'stopped'):
            heartbeat()
        self.assertIsNone(self.progress.snapshot())

    def test_actual_dispatch_publishes_loading_for_retry_of_current_generation(self):
        tree = ast.parse((Path(__file__).parent / 'skate_relay.py').read_text())
        run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
        dispatch = next(n for n in ast.walk(run) if isinstance(n, ast.If)
                        and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                                and isinstance(child.func.value, ast.Name) and child.func.value.id == 'loading'
                                and child.func.attr == 'begin' for stmt in n.body for child in ast.walk(stmt))
                        and 'queued_descriptor is not None' in ast.unparse(n.test))
        module = ast.fix_missing_locations(ast.Module(body=[dispatch], type_ignores=[]))
        for generation in ('host', 'previous-host'):
            records = [{'status':'error', 'generation':'host', 'failed_revision':3}]
            scope = {'background':SimpleNamespace(blocked=False), 'cache_future':None,
                     'queued_descriptor':{'generation':'host', 'revision':4}, 'pending_revision':None,
                     'loading':self.progress, 'generation':generation,
                     'executor':SimpleNamespace(submit=lambda *args: 'new cache future'),
                     'prepare_cached':lambda _: None, 'publish_status':records.append}
            exec(compile(module, '<actual relay cache dispatch>', 'exec'), scope)
            self.assertEqual(records[-1], {'status':'loading', 'generation':'host'})
            self.assertEqual(self.progress.snapshot()['revision'], 4)
            self.assertEqual(scope['cache_future'], 'new cache future')


class ReplyTests(unittest.TestCase):
    def request_fixture(self, stream):
        # Execute the actual nested relay request function, without starting
        # relay inputs, processes or shared memory.
        tree = ast.parse((Path(__file__).parent / 'skate_relay.py').read_text())
        run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
        function = next(n for n in run.body if isinstance(n, ast.FunctionDef) and n.name == 'request')
        replies = NativeReplyReader()
        self.addCleanup(replies.close)
        stdin = io.StringIO()
        process = type('Process', (), {'stdin': stdin, 'stdout': stream})()
        loading = type('Loading', (), {'native': lambda _, op: None})()
        scope = {'process': process, 'replies': replies, 'loading': loading, 'json': json,
                 'loading_progress': lambda: None}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<relay request>', 'exec'), scope)
        return scope, replies, stdin

    def test_blocking_init_callback_main_thread_and_tick_reads_directly(self):
        main = threading.get_ident()
        event = threading.Event()
        reads = []
        class Stream:
            def readline(self):
                reads.append(threading.get_ident())
                if len(reads) == 1:
                    if not event.wait(3):
                        raise RuntimeError('test callback was not invoked')
                return '{"ok":true}\n'
        scope, replies, stdin = self.request_fixture(Stream())
        callbacks = []
        def progress():
            callbacks.append(threading.get_ident())
            with self.assertRaisesRegex(RuntimeError, 'loading reply is pending'):
                scope['request']({'op': 'pose'})
            event.set()
        scope['loading_progress'] = progress
        self.assertTrue(scope['request']({'op': 'init_world'})['ok'])
        self.assertEqual(callbacks, [main])
        self.assertNotEqual(reads[0], main)
        self.assertIsNone(replies.pending)
        self.assertTrue(scope['request']({'op': 'tick'})['ok'])
        self.assertEqual(reads[1], main)
        self.assertEqual([json.loads(s)['op'] for s in stdin.getvalue().splitlines()], ['init_world', 'tick'])

    def test_cancel_keeps_reply_owned_until_process_pipe_exits(self):
        event = threading.Event()
        class Stream:
            def readline(self):
                event.wait(3)
                return ''
        scope, replies, stdin = self.request_fixture(Stream())
        def cancel():
            raise RuntimeError('requested stop')
        scope['loading_progress'] = cancel
        try:
            with self.assertRaisesRegex(RuntimeError, 'requested stop'):
                scope['request']({'op': 'prepare_world'})
            self.assertIsNotNone(replies.pending)
            with self.assertRaisesRegex(RuntimeError, 'loading reply is pending'):
                scope['request']({'op': 'shutdown'})
            self.assertEqual(len(stdin.getvalue().splitlines()), 1)
        finally:
            event.set()


if __name__ == '__main__':
    unittest.main()
