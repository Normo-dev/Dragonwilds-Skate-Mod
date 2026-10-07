"""Synthetic owned-cache retirement; no installed data, game, or native inputs."""
from pathlib import Path
import copy
import hashlib
import io
import json
import struct
import tempfile
import unittest
from unittest.mock import patch

import skate_cache_retention as retention
from skate_exposed_cache import digest
from skate_warm_world import WarmWorldStore, initialize_world
from test_skate_exposed_cache import make_rail_cache


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='retention-fixture-', dir=Path(__file__).parent)
        self.root = Path(self.temp.name).resolve()
        self.worker = self.root / 'worker.exe'; self.worker.write_bytes(b'worker-one')
        self.base = self.root / 'world/manifest.json'; self.base.parent.mkdir()
        self.base.write_text(json.dumps({'source_fingerprint': 'b' * 64}))
        self.sentinels = []
        for name in ['world/geometry.bin', 'prepared/old-overlay/raw.bin', 'observed-worlds/save.json', 'assets/source.bin']:
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'preserved')
            self.sentinels.append(path)

    def tearDown(self):
        self.temp.cleanup()

    def overlay(self, number):
        folder = self.root / 'prepared' / str(number); folder.mkdir(exist_ok=True)
        files = []
        for name in ['geometry.f64', 'instances.bin']:
            path = folder / name; path.write_bytes(str(number).encode())
            files.append({'path': name, 'bytes': path.stat().st_size, 'sha256': digest(path)})
        value = {'magic': 'S3O1', 'schema': 1, 'completeness': {'complete': True}, 'base_fingerprint': 'b' * 64,
                 'packages': ['world'], 'instance_packages': ['world'], 'geometry': [], 'files': files,
                 'geometry_file': files[0], 'instance_file': files[1], 'default_shape_complexity': 1}
        identity = {'base': value['base_fingerprint'], **{key: value[key] for key in
                    ['packages', 'instance_packages', 'geometry', 'files', 'default_shape_complexity']}}
        value['source_fingerprint'] = hashlib.sha256(retention.encoded(identity)).hexdigest()
        path = folder / 'manifest.json'; path.write_text(json.dumps(value)); return path, value['source_fingerprint']

    def derived(self, scene, number=0, algorithm='a'):
        folder = self.root / 'exposed-edges-v1' / (algorithm * 64) / ('b' * 64)
        (folder / 'payloads').mkdir(parents=True, exist_ok=True)
        cells = []
        for key, height in enumerate([10, number + 100] if scene else [10]):
            raw = struct.pack('<4sIQ6d', b'DWC1', 1, 1, key * 3200, 0, height, key * 3200 + 100, 0, height)
            sha = hashlib.sha256(raw).hexdigest(); path = folder / 'payloads' / (sha + '.edges'); path.write_bytes(raw)
            lo = [key * 3200, 0, 0]; hi = [(key + 1) * 3200, 3200, 3200]
            cells.append({'key': [key, 0, 0], 'min': lo, 'max': hi,
                          'dependencies': [{'min': [v - 100 for v in lo], 'max': [v + 100 for v in hi]}],
                          'path': 'payloads/' + path.name, 'sha256': sha, 'bytes': len(raw), 'segments': 1})
        value = {'magic': 'DWE1', 'schema': 1, 'complete': True, 'base_fingerprint': 'b' * 64,
                 'scene_fingerprint': scene, 'algorithm_sha256': algorithm * 64, 'cells': cells, 'segments': len(cells),
                 'parameters': {'cell_size_cm': 3200, 'halo_cm': 100, 'truck_distance_cm': 28, 'tolerance_cm': 1e-5}}
        path = folder / ((scene or 'base') + '.json'); path.write_text(json.dumps(value))
        rails = make_rail_cache(path)
        declaration = {'schema': 1, 'durable': True, 'format': 'exposed_edges_v1', 'scene_fingerprint': scene,
                       'manifest_file': str(path), 'manifest_sha256': digest(path),
                       'algorithm_sha256': algorithm * 64, 'rail_cache': rails}
        return path, declaration, [folder / row['path'] for row in cells]

    def accept(self, number, save='one', algorithm='a', worker=None, publish=True):
        if worker is not None:
            self.worker.write_bytes(worker)
        overlay, scene = self.overlay(number)
        marker, declaration, cells = self.derived(scene, number, algorithm)
        store = WarmWorldStore(self.root, self.worker)
        descriptor = {'mode': 'whole_world', 'world_session': save, 'revision': number, 'generation': 'test'}
        cached = {'world_manifest': str(self.base), 'scene_overlay': str(overlay), 'report': {
                  'world_session': save, 'world': 'World /Game/Test', 'source_fingerprint': 'b' * 64,
                  'scene_fingerprint': scene}}
        response = {'ok': True, 'collision_revision': 0, 'world': {'scene_fingerprint': scene, 'scene_checkpoint': declaration}}
        if publish:
            store.remember(descriptor, cached, response)
        pointer = store._path(store._identity(descriptor, cached))
        return {'marker': marker, 'rails': Path(declaration['rail_cache']['path']), 'cells': cells,
                'pointer': pointer, 'store': store, 'descriptor': descriptor, 'cached': cached, 'response': response}

    def test_three_accepts_keep_base_current_previous_and_shared_cells_only(self):
        base, declaration, base_cells = self.derived(None)
        old, previous, current = [self.accept(i) for i in range(1, 4)]
        result = retention.collect(self.root)
        self.assertEqual(result['status'], 'complete')
        self.assertGreater(result['bytes'], 0)
        for path in [old['marker'], old['rails'], old['cells'][1]]:
            self.assertFalse(path.exists(), path)
        for path in [base, Path(declaration['rail_cache']['path']), *base_cells, *previous['cells'],
                     previous['marker'], previous['rails'], current['marker'], current['rails'], current['pointer']]:
            self.assertTrue(path.exists(), path)
        self.assertTrue(all(path.read_bytes() == b'preserved' for path in self.sentinels))
        self.assertEqual(len(retention._load(self.root)['records']), 2)

    def test_unknown_manifest_and_crash_before_acceptance_protect_shared_payload(self):
        old = self.accept(1); self.accept(2); self.accept(3)
        unknown = self.accept(4, publish=False)
        value = json.loads(unknown['marker'].read_text())
        value['cells'][1] = copy.deepcopy(json.loads(old['marker'].read_text())['cells'][1])
        unknown['marker'].write_text(json.dumps(value)); rail = make_rail_cache(unknown['marker'])
        result = retention.collect(self.root)
        self.assertEqual(result['status'], 'complete')
        self.assertFalse(old['marker'].exists())
        self.assertTrue(old['cells'][1].exists())
        self.assertTrue(unknown['marker'].exists()); self.assertTrue(Path(rail['path']).exists())

    def test_incomplete_or_corrupt_namespace_never_deletes(self):
        for corrupt in [False, True]:
            with self.subTest(corrupt=corrupt):
                old = self.accept(1); self.accept(2); self.accept(3)
                if corrupt:
                    old['marker'].write_bytes(old['marker'].read_bytes() + b' ')
                else:
                    (old['marker'].parent / 'base.incomplete.jsonl').write_text('unfinished')
                result = retention.collect(self.root)
                self.assertEqual(result['removed'], 0)
                self.assertTrue(old['marker'].exists()); self.assertTrue(old['rails'].exists())
                (old['marker'].parent / 'base.incomplete.jsonl').unlink(missing_ok=True)

    def test_two_saves_share_protected_outputs(self):
        old = self.accept(1, save='one'); other = self.accept(1, save='two')
        self.accept(2, save='one'); self.accept(3, save='one')
        self.assertEqual(retention.collect(self.root)['status'], 'complete')
        self.assertTrue(old['marker'].exists()); self.assertTrue(old['rails'].exists())
        self.assertTrue(other['pointer'].exists())

    def test_save_family_crosses_worker_and_algorithm_versions(self):
        old = self.accept(1, worker=b'old-worker', algorithm='a')
        previous = self.accept(2, worker=b'middle-worker', algorithm='c')
        current = self.accept(3, worker=b'current-worker', algorithm='c')
        self.assertEqual(retention.collect(self.root)['status'], 'complete')
        self.assertFalse(old['marker'].exists()); self.assertFalse(old['pointer'].exists())
        self.assertTrue(previous['pointer'].exists()); self.assertTrue(current['pointer'].exists())
        self.assertEqual(len(retention._load(self.root)['families']), 1)

    def test_same_scene_does_not_rotate_away_rollback(self):
        old = self.accept(1); self.accept(2); self.accept(2)
        with patch.object(retention, '_manifest', side_effect=AssertionError('No retired data needs a scan')):
            self.assertEqual(retention.collect(self.root)['status'], 'retained')
        self.assertTrue(old['marker'].exists())

    def test_same_scene_worker_update_keeps_pointer_ownership_until_retirement(self):
        first = self.accept(1, worker=b'first')
        second = self.accept(1, worker=b'second')
        self.accept(2); self.accept(3)
        self.assertEqual(retention.collect(self.root)['status'], 'complete')
        self.assertFalse(first['pointer'].exists())
        self.assertFalse(first['marker'].exists())
        self.assertTrue(second['pointer'].exists(), 'Latest worker pointer now names scene three')

    def test_missing_current_manifest_protects_shared_namespace(self):
        old = self.accept(1); self.accept(2); current = self.accept(3)
        current['marker'].unlink()
        result = retention.collect(self.root)
        self.assertEqual(result['removed'], 0)
        self.assertTrue(old['marker'].exists()); self.assertTrue(old['cells'][1].exists())

    def test_corrupt_catalog_and_budget_expiry_preserve_everything(self):
        old = self.accept(1); self.accept(2); self.accept(3)
        self.assertEqual(retention.collect(self.root, budget_seconds=-1)['status'], 'deferred')
        path = self.root / retention.NAME; value = json.loads(path.read_text())
        value['sha256'] = '0' * 64; path.write_text(json.dumps(value))
        self.assertEqual(retention.collect(self.root)['status'], 'skipped')
        self.assertTrue(old['marker'].exists()); self.assertTrue(old['cells'][1].exists())

    def test_interrupted_unlink_has_durable_ownership_and_resumes(self):
        old = self.accept(1); self.accept(2); self.accept(3)
        unlink = Path.unlink
        def interrupted(path, *args, **kwargs):
            if path == old['rails']:
                raise OSError('Synthetic interrupted deletion')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', interrupted):
            self.assertEqual(retention.collect(self.root)['status'], 'skipped')
        self.assertFalse(old['marker'].exists())
        self.assertTrue(retention._load(self.root)['pending'])
        self.assertEqual(retention.collect(self.root)['status'], 'complete')
        self.assertFalse(old['rails'].exists()); self.assertFalse(old['cells'][1].exists())

    def test_wrong_path_catalog_cannot_delete_raw_file(self):
        self.accept(1); self.accept(2); self.accept(3)
        data = retention._load(self.root)
        data['pending'] = [{'path': 'world/geometry.bin', 'sha256': digest(self.sentinels[0]), 'bytes': 9, 'kind': 'cell'}]
        retention._write(self.root, data)
        self.assertEqual(retention.collect(self.root)['status'], 'skipped')
        self.assertEqual(self.sentinels[0].read_bytes(), b'preserved')

    def test_fresh_init_records_native_checkpoint_without_revision_match(self):
        value = self.accept(1, publish=False)
        result = initialize_world(lambda _: value['response'], {'op': 'init_world'}, value['descriptor'],
                                  value['cached'], value['store'])
        self.assertEqual(result['collision_revision'], 0)
        saved = json.loads(value['pointer'].read_text())
        self.assertEqual(saved['accepted_checkpoint']['sha256'], digest(value['marker']))
        self.assertEqual(len(retention._load(self.root)['records']), 1)

    def test_record_failure_never_blocks_valid_warm_pointer(self):
        value = self.accept(1, publish=False)
        with patch.object(retention, '_write', side_effect=OSError('Synthetic catalog error')):
            self.assertIs(value['store'].remember(value['descriptor'], value['cached'], value['response']), False)
            response = {**value['response'], 'collision_revision': value['descriptor']['revision']}
            self.assertIs(value['store'].promote(value['descriptor'], value['cached'], response), False)
        self.assertEqual(value['store'].select(value['descriptor'], value['cached']), Path(value['cached']['scene_overlay']))

    def test_orphan_check_allows_game_but_blocks_unknown_or_exact_helper(self):
        paths = {'map_data': self.root, 'worker': self.worker, 'game_root': self.root / 'game'}
        with patch('skate_process.process_images', return_value=[]) as images, \
                patch.object(retention, 'collect', return_value={'status': 'fixture'}) as collect:
            self.assertEqual(retention.launch_maintenance(paths)['status'], 'fixture')
            self.assertNotIn('rsdragonwilds-win64-shipping.exe', images.call_args.args[0])
            collect.assert_called_once_with(self.root)
        for image in [str(self.worker), None]:
            with patch('skate_process.process_images', return_value=[(123, 'worker.exe', image)]), \
                    patch.object(retention, 'collect', side_effect=AssertionError('An orphan still owns caches')):
                self.assertEqual(retention.launch_maintenance(paths)['status'], 'skipped')

    def test_child_requires_existing_launcher_and_stops_with_parent(self):
        request = {'op': 'collect', 'root': str(self.root), 'parent_pid': 123, 'budget_seconds': 8}
        for existing, dead in [(False, False), (True, True)]:
            with self.subTest(existing=existing, dead=dead):
                output = io.StringIO()
                with patch('skate_process.Singleton') as singleton, patch('skate_process.ParentProcess') as parent:
                    singleton.return_value.__enter__.return_value.acquired = not existing
                    parent.return_value.__enter__.return_value.exited.return_value = dead
                    retention.maintenance_child(io.StringIO(json.dumps(request) + '\n'), output)
                self.assertEqual(json.loads(output.getvalue())['status'], 'skipped')


if __name__ == '__main__':
    unittest.main()
