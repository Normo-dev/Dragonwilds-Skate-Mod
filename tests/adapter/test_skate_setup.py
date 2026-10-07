"""Exercise staged setup failure/retry and ownership with private temporary data."""
from pathlib import Path
import json
import os
import shutil
import tempfile
import unittest
import time
from unittest.mock import patch
from subprocess import CompletedProcess

import skate_setup as setup
from test_authored_policy import fixture as policy_fixture, CONFIG as SYNTHETIC_CONFIG

ROOT = Path(__file__).resolve().parent


class SetupTests(unittest.TestCase):
    def test_refuses_existing_data(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='setup-check-') as temporary:
            target = Path(temporary) / 'owned data'
            target.mkdir()
            (target / 'foreign.txt').write_text('keep')
            with self.assertRaisesRegex(ValueError, 'already contains'):
                setup.fresh_directory(target)
            self.assertEqual((target / 'foreign.txt').read_text(), 'keep')

    def test_map_failure_is_not_ready_and_resume_uses_same_inputs(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='setup-check-') as temporary:
            folder = Path(temporary) / '路径 & map $(literal)'
            folder.mkdir()
            target = folder / 'map-data'
            exporter = folder / 'exporter/exporter.exe'
            exporter.parent.mkdir()
            exporter.write_bytes(b'inert test exporter')
            mappings = folder / 'Mappings.usmap'
            mappings.write_bytes(b'\xc4\x30\x00' + b'inert fixture' * 2)
            probe = folder / 'probe.json'
            data = policy_fixture()
            data.update(schema='S3COLLISIONPOLICY1', complete=True, errors=[], physicsSettings={'engine_default': 1})
            data['setup_provenance'] = {'game_root': str(folder / 'game'), 'game_build': 'fixture',
                'dump_attempted': True, 'dump_outcome': 'returned', 'usmap_requested_at_unix': time.time()}
            setup.save(probe, data)
            # Public numeric fixture: no captured game configuration/probe files.
            configs = folder / 'synthetic-config'
            configs.mkdir()
            (configs / 'BaseEngine.ini').write_text('')
            (configs / 'DefaultEngine.ini').write_text(SYNTHETIC_CONFIG)
            paths = {'map_data': target, 'game_root': folder / 'game', 'map_export': exporter}
            calls = []
            fail = [True]
            def runner(exe, args, log):
                self.assertEqual(exe, exporter)
                calls.append(args)
                if args[-1] == 'config':
                    Path(args[1]).mkdir()
                    for name in ('BaseEngine.ini', 'DefaultEngine.ini'):
                        shutil.copy2(configs / name, Path(args[1]) / name)
                if args[-1] == 'terrain' and fail[0]:
                    fail[0] = False
                    raise RuntimeError('injected export failure')
                if args[-1] == 'buildings':
                    setup.save(target / 'building-catalogue.json', {'magic':'S3BL1','schema':1,'complete':True,
                        'errors':[], 'mapping_sha256':setup.digest(mappings), 'data':{'fixture':{}}, 'derived':{'fixture':{}}})
            def world():
                setup.save(target / 'compact-world-base.json', {'test_fixture_only': True})
            archive_identity=lambda _:[{'path':'fixture.pak','sha256':'00'*32,'bytes':1}]
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                setup.prepare_map(paths, {'steam_build': 'fixture'}, mappings, probe, runner=runner, build_world=world,archive_identity=archive_identity)
            state = json.loads((target / 'setup-state.json').read_text())
            self.assertFalse(state['ready'])
            self.assertFalse((target / 'compact-world-base.json').exists())
            data['pawn'] = 'Another transient pawn after reconnect'
            data['setup_provenance']['captured_at_unix'] = time.time()
            setup.save(probe, data)
            result = setup.prepare_map(paths, {'steam_build': 'fixture'}, mappings, probe, resume=True, runner=runner, build_world=world,archive_identity=archive_identity)
            self.assertTrue(result['ready'])
            self.assertEqual(sum(args[-1] == 'config' for args in calls), 1)
            self.assertEqual(sum(args[-1] == 'terrain' for args in calls), 2)
            mappings.write_bytes(b'\xc4\x30\x00' + b'changed mapping input' * 2)
            with self.assertRaisesRegex(ValueError, 'different inputs'):
                setup.prepare_map(paths, {'steam_build': 'fixture'}, mappings, probe, resume=True, runner=runner, build_world=world,archive_identity=archive_identity)

    def test_mapping_discovery_rejects_stale_wrong_game_and_ambiguous_dumps(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='mapping-check-') as temporary:
            base = Path(temporary).resolve()
            paths = {'game_root': base / 'game', 'mailbox': base / 'mailbox'}
            paths['mailbox'].mkdir()
            directory = paths['game_root'] / 'RSDragonwilds/Binaries/Win64/ue4ss'
            directory.mkdir(parents=True)
            now = time.time()
            probe = {'setup_provenance': {'game_root': str(paths['game_root']), 'game_build': 'fixture',
                'dump_attempted': True, 'dump_outcome': 'returned', 'usmap_requested_at_unix': now}}
            file = paths['mailbox'] / 'collision-policy-probe.json'
            setup.save(file, probe)
            mapping = directory / 'RSDragonwilds-fixture.usmap'
            mapping.write_bytes(b'\xc4\x30\x00' + bytes(20))
            os.utime(mapping, (now - 20, now - 20))
            with self.assertRaisesRegex(ValueError, 'one fresh'):
                setup.discover_inputs(paths, {'steam_build': 'fixture'})
            os.utime(mapping, (now, now))
            self.assertEqual(setup.discover_inputs(paths, {'steam_build': 'fixture'}), (mapping, file))
            second = directory / 'RSDragonwilds-another.usmap'
            second.write_bytes(mapping.read_bytes())
            with self.assertRaisesRegex(ValueError, 'one fresh'):
                setup.discover_inputs(paths, {'steam_build': 'fixture'})
            self.assertEqual(setup.discover_inputs(paths, {'steam_build': 'fixture'}, mapping), (mapping, file))
            with self.assertRaisesRegex(ValueError, 'this game build'):
                setup.discover_inputs(paths, {'steam_build': 'changed'}, mapping)

    def test_policy_identity_ignores_only_capture_metadata(self):
        left = {'schema': 'fixture', 'collision': {'channel': 17}, 'pawn': 'A', 'world': 'A'}
        right = {**left, 'pawn': 'B', 'world': 'B', 'setup_provenance': {'when': 123}}
        self.assertEqual(setup.probe_identity(left), setup.probe_identity(right))
        right['collision'] = {'channel': 18}
        self.assertNotEqual(setup.probe_identity(left), setup.probe_identity(right))

    def test_grind_preparation_requires_matching_success_and_no_session(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='lips-contract-') as temporary:
            base = Path(temporary)
            manifest, log = base / 'manifest.json', base / 'log.txt'
            fingerprint = 'ab' * 32
            setup.save(manifest, {'source_fingerprint': fingerprint})
            reply = {'ok': True, 'status': 'prepared', 'session_loaded': False,
                     'source_fingerprint': fingerprint}
            def result(value):
                return CompletedProcess([], 0, json.dumps(value), '')
            with patch.object(setup.subprocess, 'run', return_value=result(reply)) as run:
                self.assertEqual(setup.prepare_world_lips('worker.exe', manifest, log), reply)
                request = json.loads(run.call_args.kwargs['input'])
                self.assertEqual(request, {'op': 'prepare_world', 'manifest': str(manifest.resolve()), 'exposed_edges': False})
            for bad in [{**reply, 'ok': False}, {**reply, 'source_fingerprint': 'cd'*32},
                        {**reply, 'session_loaded': True}]:
                with patch.object(setup.subprocess, 'run', return_value=result(bad)):
                    with self.assertRaisesRegex(RuntimeError, 'preparation failed'):
                        setup.prepare_world_lips('worker.exe', manifest, log)
            with patch.object(setup.subprocess, 'run', return_value=result({'ok': False, 'error': 'Edge cache is incomplete'})):
                with self.assertRaisesRegex(RuntimeError, 'Edge cache is incomplete'):
                    setup.prepare_world_lips('worker.exe', manifest, log)
                self.assertIn('Edge cache is incomplete', log.read_text())

    def test_refresh_probe_preserves_old_capture_and_refuses_unknown_files(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='probe-refresh-') as temporary:
            folder = Path(temporary).resolve()
            paths = {'game_root': folder / 'game', 'mailbox': folder / 'mailbox'}
            paths['mailbox'].mkdir()
            provenance = {'game_root': str(paths['game_root']), 'game_build': 'fixture'}
            file = paths['mailbox'] / 'collision-policy-probe.json'
            probe = {'schema': 'S3COLLISIONPOLICY1', 'setup_provenance': provenance}
            setup.save(file, probe)
            status = paths['mailbox'] / 'setup-status.json'
            setup.save(status, {'schema': 'foreign'})
            with self.assertRaisesRegex(ValueError, 'unrecognized'):
                setup.refresh_probe(paths, {'steam_build': 'fixture'})
            self.assertEqual(json.loads(file.read_text()), probe)
            setup.save(status, {'schema': 'S3SETUP1', **provenance})
            result = setup.refresh_probe(paths, {'steam_build': 'fixture'})
            self.assertEqual(result['status'], 'capture_archived')
            self.assertFalse(file.exists())
            self.assertFalse(status.exists())
            self.assertEqual(json.loads((Path(result['backup']) / file.name).read_text()), probe)


if __name__ == '__main__':
    unittest.main()
