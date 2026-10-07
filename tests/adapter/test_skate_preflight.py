"""Installation gates, including relocation, tampering and compatibility failures."""
from pathlib import Path
import json
import struct
import tempfile
import unittest

from skate_preflight import (PreflightError, check_assets, check_game, check_map,
                            check_package, contained, digest, map_preparation_identity)


class InstallationGates(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skate install test ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
        return path

    def test_package_hash_and_size_gate(self):
        code = self.write('app/example.py', b'original')
        manifest = {'schema': 1, 'product': 'DragonwildsSkate', 'files': [
            {'path': 'app/example.py', 'bytes': code.stat().st_size, 'sha256': digest(code)}]}
        self.write('release-manifest.json', manifest)
        self.assertEqual(check_package(self.root), manifest)
        code.write_bytes(b'modified')
        with self.assertRaisesRegex(PreflightError, 'changed'):
            check_package(self.root)
        code.unlink()
        with self.assertRaisesRegex(PreflightError, 'missing'):
            check_package(self.root)

    def test_exporter_dependency_change_invalidates_map_preparation(self):
        exe = self.write('exporter/exporter.exe', b'unchanged apphost')
        dll = self.write('exporter/parser.dll', b'original parser')
        app = Path(__file__).resolve().parent
        paths = {'map_export': exe}
        before = map_preparation_identity(app, paths, {'steam_build': 'fixture'})
        dll.write_bytes(b'updated parser')
        after = map_preparation_identity(app, paths, {'steam_build': 'fixture'})
        self.assertNotEqual(before['exporter_inventory_sha256'], after['exporter_inventory_sha256'])
        self.assertEqual(before['modules'], after['modules'])

    def test_package_relative_path_boundary(self):
        for name in ['../outside', '/outside', 'C:/outside', 'bin\\worker.exe', 'bin/../worker', 'bin/./worker']:
            with self.subTest(name=name), self.assertRaises(PreflightError):
                contained(self.root, name)
        self.assertEqual(contained(self.root, 'artwork/a board.png'), self.root / 'artwork/a board.png')

    def test_loader_and_game_data_versions_are_both_required(self):
        game = self.root / 'steamapps/common/Dragonwilds'
        exe = self.write('steamapps/common/Dragonwilds/RSDragonwilds/Binaries/Win64/RSDragonwilds-Win64-Shipping.exe', b'game')
        loader = self.write('steamapps/common/Dragonwilds/RSDragonwilds/Binaries/Win64/ue4ss/UE4SS.dll', b'loader')
        (game / 'RSDragonwilds/Content/Paks').mkdir(parents=True)
        acf = self.write('steamapps/appmanifest_1374490.acf', b'"AppState" { "buildid" "25632050" }')
        compatibility = {'steam_build': '25632050', 'game_exe_sha256': digest(exe), 'ue4ss_sha256': digest(loader)}
        self.assertEqual(check_game(game, compatibility)['steam_build'], '25632050')
        acf.write_text('"buildid" "25632051"')
        with self.assertRaisesRegex(PreflightError, 'game data'):
            check_game(game, compatibility)
        acf.write_text('"buildid" "25632050"')
        loader.write_bytes(b'other loader')
        with self.assertRaisesRegex(PreflightError, 'UE4SS'):
            check_game(game, compatibility)

    def test_prepared_asset_manifest_and_glb(self):
        manifest = {'version': 1, 'character_scene': 'private/skater.glb',
                    'initial_animation': 'IDLE', 'action_graph': 'private/action.stategraph',
                    'motion_graph': 'private/motion.stategraph'}
        self.write('private/game.json', manifest)
        self.write('private/skater.glb', struct.pack('<III', 0x46546c67, 2, 12))
        self.write('private/action.stategraph', b'action')
        self.write('private/motion.stategraph', b'motion')
        self.write('private/stock/physics-skeletons.json', {})
        self.write('private/stock/skater-collections.json', {})
        self.assertEqual(check_assets(self.root)['manifest_version'], 1)
        manifest['action_graph'] = '../other.stategraph'
        self.write('private/game.json', manifest)
        with self.assertRaises(PreflightError):
            check_assets(self.root)
        manifest['action_graph'] = 'private/action.stategraph'
        self.write('private/game.json', manifest)
        self.write('private/skater.glb', b'truncated')
        with self.assertRaisesRegex(PreflightError, 'GLB'):
            check_assets(self.root)

    def test_map_published_identity_and_binary_integrity(self):
        data = self.write('world/geometry.bin', bytes(72))
        record = {'path': 'geometry.bin', 'bytes': 72, 'sha256': digest(data)}
        inst = self.write('world/instances.bin', bytes(152))
        inst_record = {'path': 'instances.bin', 'bytes': 152, 'sha256': digest(inst)}
        fingerprint = 'ab' * 32
        world = {'magic': 'S3W1', 'schema': 1, 'completeness': {'complete': True},
            'source_fingerprint': fingerprint, 'files': [record, inst_record],
            'geometry_file': record, 'geometry_triangle_count': 1,
            'instance_file': inst_record, 'instance_count': 1, 'instance_stride': 152, 'terrain': []}
        self.write('world/manifest.json', world)
        pointer = {'schema': 1, 'manifest': 'world/manifest.json', 'source_fingerprint': fingerprint}
        rails = self.write('world/world.lips', b'inert rail fixture')
        pointer['grind_file'] = {'path': 'world/world.lips', 'bytes': rails.stat().st_size,
                                'sha256': digest(rails), 'worker_sha256': 'ef' * 32}
        pointer['preparation'] = {'compatibility': {'steam_build': 'fixture'}}
        catalogue = self.write('building-catalogue.json', {'magic':'S3BL1','schema':1,'complete':True,'errors':[],
            'mapping_sha256':'ee'*32,'data':{'fixture':{}},'derived':{'fixture':{}}})
        pointer['building_catalogue'] = {'path':catalogue.name,'bytes':catalogue.stat().st_size,'sha256':digest(catalogue)}
        self.write('compact-world-base.json', pointer)
        self.assertEqual(check_map(self.root)['map_fingerprint'], fingerprint)
        self.assertEqual(check_map(self.root,require_buildings=True)['map_fingerprint'],fingerprint)
        original_catalogue = catalogue.read_bytes()
        catalogue.write_bytes(b'changed')
        with self.assertRaisesRegex(PreflightError, 'Building metadata is missing or changed'):
            check_map(self.root,require_buildings=True)
        catalogue.write_bytes(original_catalogue)
        with self.assertRaisesRegex(PreflightError, 'different game build'):
            check_map(self.root, expected_preparation={'compatibility': {'steam_build': 'changed'}})
        with self.assertRaisesRegex(PreflightError, 'verification with this worker'):
            check_map(self.root, worker_sha256='ff' * 32)
        rails.write_bytes(b'changed rail fixture')
        with self.assertRaisesRegex(PreflightError, 'missing or changed'):
            check_map(self.root)
        rails.write_bytes(b'inert rail fixture')
        data.write_bytes(b'x' * 72)
        with self.assertRaisesRegex(PreflightError, 'incomplete or changed'):
            check_map(self.root)
        data.write_bytes(bytes(72))
        pointer['source_fingerprint'] = 'cd' * 32
        self.write('compact-world-base.json', pointer)
        with self.assertRaisesRegex(PreflightError, 'fingerprint mismatch'):
            check_map(self.root)


if __name__ == '__main__':
    unittest.main()
