"""Public setup and warm-start lifecycle for derived edges; no game or native input."""
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock, patch
import copy
import hashlib
import json
import struct
import tempfile
import time
import unittest

import skate_exposed_cache as exposed
import skate_setup as setup
from skate_preflight import PreflightError, check_map, digest, map_preparation_identity
from skate_warm_world import WarmWorldStore, initialize_world, prewarm_world
from test_skate_exposed_cache import make_rail_cache


ROOT = Path(__file__).resolve().parent
BASE = 'b' * 64
ALGORITHM = 'a' * 64


class ExposedLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT, prefix='exposed-lifecycle-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.maps = self.root / 'map-data'
        self.maps.mkdir()
        self.worker = self.write(self.root / 'worker.exe', b'inert worker fixture')
        exporter = self.write(self.root / 'exporter/exporter.exe', b'inert exporter fixture')
        self.assets = self.root / 'assets'
        self.write(self.assets / 'private/game.json', {'version': 1, 'character_scene': 'private/skater.glb',
            'initial_animation': 'IDLE', 'action_graph': 'private/action.stategraph', 'motion_graph': 'private/motion.stategraph'})
        self.write(self.assets / 'private/skater.glb', struct.pack('<III', 0x46546c67, 2, 12))
        for name in ('action.stategraph', 'motion.stategraph', 'stock/physics-skeletons.json', 'stock/skater-collections.json'):
            self.write(self.assets / 'private' / name, b'{}')
        self.paths = {'map_data': self.maps, 'map_export': exporter, 'worker': self.worker,
            'assets': self.assets, 'game_root': self.root / 'game'}
        self.compatibility = {'steam_build': 'fixture'}
        self.mapping = self.write(self.root / 'Mappings.usmap', b'\xc4\x30\x00' + bytes(20))
        self.probe_value = {'schema': 'S3COLLISIONPOLICY1', 'complete': True, 'errors': [],
            'physicsSettings': {'engine_default': 1}, 'setup_provenance': {
                'game_root': str(self.paths['game_root']), 'game_build': 'fixture', 'dump_attempted': True,
                'dump_outcome': 'returned', 'usmap_requested_at_unix': time.time()}}
        self.probe = self.write(self.root / 'probe.json', self.probe_value)
        self.archives = [{'path': 'fixture.pak', 'bytes': 1, 'sha256': '0' * 64}]
        records = []
        for name, size in [('geometry.bin', 72), ('instances.bin', 152)]:
            file = self.write(self.maps / 'world' / name, bytes(size))
            records.append({'path': name, 'bytes': size, 'sha256': digest(file)})
        self.base_manifest = self.write(self.maps / 'world/manifest.json', {
            'magic': 'S3W1', 'schema': 1, 'completeness': {'complete': True}, 'source_fingerprint': BASE,
            'files': records, 'geometry_file': records[0], 'geometry_triangle_count': 1,
            'instance_file': records[1], 'instance_count': 1, 'instance_stride': 152, 'terrain': []})
        self.rails = self.write(self.maps / 'world/world.lips', b'unchanged original lip fixture')
        self.preparation = map_preparation_identity(ROOT, self.paths, self.compatibility)
        self.pointer = {'schema': 1, 'manifest': 'world/manifest.json', 'source_fingerprint': BASE,
            'preparation': self.preparation, 'grind_file': {'path': 'world/world.lips',
                'bytes': self.rails.stat().st_size, 'sha256': digest(self.rails), 'worker_sha256': digest(self.worker)}}
        self.pointer_file = self.write(self.maps / 'compact-world-base.json', self.pointer)
        self.state = {'identity': {'schema': 1, 'product': 'DragonwildsSkateSetup', 'target': str(self.maps),
            'compatibility': self.compatibility, 'mapping_sha256': digest(self.mapping),
            'probe_policy_sha256': setup.probe_identity(self.probe_value), 'exporter_sha256': digest(exporter),
            'preparation': self.preparation, 'game_archives': self.archives},
            'completed': ['collision-settings', 'collision-policy', 'terrain', 'objects-and-buildings', 'complete-world-index'],
            'ready': True, 'stage': 'ready'}
        self.write(self.maps / 'setup-state.json', self.state)
        (self.maps / 'setup-logs').mkdir()

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
        return path

    def edge_cache(self, scene=None):
        folder = self.maps / 'exposed-edges-v1' / ALGORITHM / BASE
        raw = struct.pack('<4sIQ6d', b'DWC1', 1, 1, 0, 0, 100, 100, 0, 100)
        sha = hashlib.sha256(raw).hexdigest()
        payload = self.write(folder / 'payloads' / (sha + '.edges'), raw)
        value = {'magic': 'DWE1', 'schema': 1, 'complete': True, 'base_fingerprint': BASE,
            'scene_fingerprint': scene, 'algorithm_sha256': ALGORITHM, 'segments': 1,
            'parameters': {'cell_size_cm': 3200, 'halo_cm': 100, 'truck_distance_cm': 28, 'tolerance_cm': 1e-5},
            'cells': [{'key': [0, 0, 0], 'min': [0, 0, 0], 'max': [3200, 3200, 3200],
                'dependencies': [{'min': [-100, -100, -100], 'max': [3300, 3300, 3300]}],
                'path': 'payloads/' + payload.name, 'bytes': len(raw), 'sha256': sha, 'segments': 1, 'census': {}}]}
        manifest = self.write(folder / ((scene or 'base') + '.json'), value)
        declaration = {'schema': 1, 'ready': True, 'manifest_file': str(manifest), 'manifest_sha256': digest(manifest),
            'base_fingerprint': BASE, 'scene_fingerprint': scene, 'algorithm_sha256': ALGORITHM, 'cells': 1, 'segments': 1,
            'rail_cache': make_rail_cache(manifest)}
        return manifest, payload, declaration

    def native_response(self):
        _, _, declaration = self.edge_cache()
        return {'ok': True, 'status': 'prepared', 'session_loaded': False, 'source_fingerprint': BASE,
            'rail_file': str(self.rails), 'exposed_edges': declaration}

    def resume(self, response):
        runner = Mock(side_effect=AssertionError('A derived cache repair must not export terrain or objects'))
        completed = CompletedProcess([], 0, json.dumps(response), '')
        with patch.object(setup.subprocess, 'run', return_value=completed) as native:
            result = setup.prepare_map(self.paths, self.compatibility, self.mapping, self.probe, resume=True,
                runner=runner, archive_identity=lambda _: self.archives)
        runner.assert_not_called()
        return result, native

    def test_ready_resume_repairs_derived_cache_once_without_reexport(self):
        original_geometry = (self.maps / 'world/geometry.bin').read_bytes()
        original_rails = self.rails.read_bytes()
        response = self.native_response()
        result, native = self.resume(response)
        self.assertTrue(result['ready'])
        self.assertEqual(native.call_count, 1)
        command = json.loads(native.call_args.kwargs['input'])
        self.assertIs(command['exposed_edges'], True)
        self.assertEqual(command['assets'], str(self.assets))
        self.assertEqual((self.maps / 'world/geometry.bin').read_bytes(), original_geometry)
        self.assertEqual(self.rails.read_bytes(), original_rails)
        self.assertEqual(check_map(self.maps, worker_sha256=digest(self.worker), require_exposed=True)['map_fingerprint'], BASE)
        _, second = self.resume(response)
        second.assert_not_called()

    def test_bad_derived_preparation_keeps_original_pointer_and_status(self):
        pointer_before = self.pointer_file.read_bytes()
        state_before = (self.maps / 'setup-state.json').read_bytes()
        response = self.native_response()
        response['exposed_edges']['manifest_sha256'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.resume(response)
        self.assertEqual(self.pointer_file.read_bytes(), pointer_before)
        self.assertEqual((self.maps / 'setup-state.json').read_bytes(), state_before)
        self.assertFalse((self.maps / 'grind-preparation.json').exists())

    def test_offline_ready_resume_repairs_corrupt_cell_payload(self):
        response = self.native_response()
        self.resume(response)
        manifest = json.loads(Path(response['exposed_edges']['manifest_file']).read_text())
        payload = Path(response['exposed_edges']['manifest_file']).parent / manifest['cells'][0]['path']
        payload.write_bytes(b'corruption')
        # Offline resume is the repair entry point; startup alone may defer this
        # payload verification to the native loader to avoid duplicate I/O.
        calls = []
        def rebuild(*args, **kwargs):
            calls.append(json.loads(kwargs['input']))
            repaired = self.native_response()
            return CompletedProcess([], 0, json.dumps(repaired), '')
        with patch.object(setup.subprocess, 'run', side_effect=rebuild):
            setup.prepare_map(self.paths, self.compatibility, self.mapping, self.probe, resume=True,
                runner=Mock(side_effect=AssertionError('Unexpected geometry export')),
                archive_identity=lambda _: self.archives)
        self.assertEqual(len(calls), 1, 'Offline setup accepted a corrupt cell without repairing it')
        exposed.check_record(self.maps, json.loads(self.pointer_file.read_text())['exposed_edges'],
            base=BASE, verify_payloads=True)

    def test_wrong_worker_base_and_manifest_pin_reject_preflight(self):
        self.resume(self.native_response())
        original = json.loads(self.pointer_file.read_text())
        for change in [{'worker_sha256': 'f' * 64}, {'base_fingerprint': 'c' * 64}, {'sha256': 'c' * 64}]:
            with self.subTest(change=change):
                modified = copy.deepcopy(original)
                modified['exposed_edges'].update(change)
                self.write(self.pointer_file, modified)
                with self.assertRaises(PreflightError):
                    check_map(self.maps, worker_sha256=digest(self.worker), require_exposed=True)

    def overlay(self, name, marker):
        folder = self.maps / 'prepared' / name
        records = []
        for name in ['geometry.f64', 'instances.bin']:
            file = self.write(folder / name, bytes([marker]))
            records.append({'path': name, 'bytes': 1, 'sha256': digest(file)})
        value = {'magic': 'S3O1', 'schema': 1, 'completeness': {'complete': True}, 'base_fingerprint': BASE,
            'packages': ['world'], 'instance_packages': ['world'], 'geometry': [], 'files': records,
            'geometry_file': records[0], 'instance_file': records[1], 'default_shape_complexity': 1}
        identity = {'base': BASE, **{k: value[k] for k in ['packages', 'instance_packages', 'geometry', 'files', 'default_shape_complexity']}}
        value['source_fingerprint'] = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return self.write(folder / 'manifest.json', value)

    def warm_fixture(self):
        old, current = self.overlay('old', 1), self.overlay('current', 2)
        store = WarmWorldStore(self.maps, self.worker)
        descriptor = {'mode': 'whole_world', 'world_session': 'save-one', 'revision': 2, 'generation': 'generation-one'}
        def report(path):
            return {'world_manifest': str(self.base_manifest), 'scene_overlay': str(path), 'report': {
                'world_session': descriptor['world_session'], 'world': 'World /Game/World', 'source_fingerprint': BASE,
                'scene_fingerprint': json.loads(path.read_text())['source_fingerprint']}}
        store.remember(descriptor, report(old))
        return store, descriptor, old, current, report(current)

    def test_derived_checkpoint_promotes_and_restarts_for_exact_scene(self):
        store, descriptor, _, current, cached = self.warm_fixture()
        marker, _, declaration = self.edge_cache(cached['report']['scene_fingerprint'])
        checkpoint = {**declaration, 'durable': True, 'format': 'exposed_edges_v1'}
        response = {'collision_revision': descriptor['revision'], 'world': {'scene_checkpoint': checkpoint}}
        self.assertTrue(store.promote(descriptor, cached, response))
        self.assertEqual(WarmWorldStore(self.maps, self.worker).select(descriptor, cached), current)
        marker.write_bytes(marker.read_bytes() + b' ')
        self.assertIsNone(WarmWorldStore(self.maps, self.worker).select(descriptor, cached))

    @patch('skate_warm_world.time.sleep')
    def test_accepted_scene_metadata_replaces_preloaded_ready_and_preserves_period(self, _sleep):
        store, descriptor, old, current, cached = self.warm_fixture()
        warmup = {**descriptor, 'world': 'World /Game/World', 'center': [0, 0, 0], 'anchor': [0, 0, 0], 'heading': 0}
        old_result = {'ok': True, 'status': 'ready', 'period': .016, 'world': {
            'scene_fingerprint': json.loads(old.read_text())['source_fingerprint'], 'exposed_edges': {'ready': True}},
            'collision_error': 'stale', 'collision_error_revision': 1}
        preloaded = prewarm_world(lambda _: old_result, warmup, store, self.base_manifest, self.rails, str(self.assets))
        accepted = {'ok': True, 'collision_revision': 2, 'collision_pending_revision': None,
            'world': {'scene_fingerprint': cached['report']['scene_fingerprint'], 'exposed_edges': {'ready': False}}}
        calls = []
        def request(command):
            calls.append(command['op'])
            return accepted if command['op'] == 'pose' else {'ok': True}
        command = {'op': 'init_world', 'manifest': str(self.base_manifest), 'scene_file': str(current),
            'assets': str(self.assets), 'anchor': [0, 0, 0]}
        result = initialize_world(request, command, descriptor, cached, store, preloaded=preloaded)
        self.assertEqual(calls, ['world_scene', 'pose'])
        self.assertEqual(result['period'], .016)
        self.assertIs(result['world']['exposed_edges']['ready'], False)
        self.assertEqual(result['world']['scene_fingerprint'], cached['report']['scene_fingerprint'])
        self.assertNotIn('collision_error', result)
        self.assertTrue(old_result['world']['exposed_edges']['ready'])


if __name__ == '__main__':
    unittest.main()
