"""Only temporary fake games. No Steam installation or real executable is used."""
from pathlib import Path
import json
import base64
import os
import shutil
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import skate_install as setup
from runtime_paths import load_paths


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.runs = HERE / 'test-runs'; self.runs.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='fake game ünicode 漢字 ', dir=self.runs)).resolve()
        self.game = self.root / 'Steam Library/steamapps/common/Dragonwilds ü'
        self.package = self.root / 'extracted release α'
        self.write(self.game, setup.WIN64 + '/RSDragonwilds-Win64-Shipping.exe', b'FAKE TEST GAME')
        (self.game / 'RSDragonwilds/Content/Paks').mkdir(parents=True)
        self.write(self.game.parent.parent, 'appmanifest_1374490.acf', b'"AppState" { "buildid" "123456" }')
        self.runtime = {
            'worker': 'bin/worker.exe', 'transport_library': 'bin/transport.dll',
            'lua_transport_library': 'bin/lua.dll', 'render_library': 'bin/render.dll',
            'map_export': 'exporter/MapExport.exe', 'gamepad_library': 'bin/SDL3.dll',
            'mount_icon': 'art/icon.png', 'python_runtime': 'python/python.exe',
            'relay_launcher': 'app/skate_launcher.py'}
        for name in set(self.runtime.values()) | {'app/runtime_paths.py', 'app/skate_preflight.py', 'app/skate_install.py',
                'host/main.lua', 'host/bridge.lua', 'loader/UE4SS.dll', 'loader/dwmapi.dll', 'loader/settings.ini', 'loader/mods.txt', 'loader/UEHelpers.lua'}:
            self.write(self.package, name, ('FAKE PAYLOAD ' + name).encode())
        self.spec = {'schema': 1, 'runtime': self.runtime,
                     'host_files': [{'source': 'host/main.lua', 'destination': 'main.lua'},
                                    {'source': 'host/bridge.lua', 'destination': 'bridge.lua'}],
                     'loader_files': [{'source': 'loader/UE4SS.dll', 'destination': 'ue4ss/UE4SS.dll'},
                                      {'source': 'loader/dwmapi.dll', 'destination': 'dwmapi.dll'},
                                      {'source': 'loader/UEHelpers.lua', 'destination': 'ue4ss/Mods/shared/UEHelpers/UEHelpers.lua'},
                                      {'source': 'loader/settings.ini', 'destination': 'ue4ss/UE4SS-settings.ini'},
                                      {'source': 'loader/mods.txt', 'destination': 'ue4ss/Mods/mods.txt'}]}
        self.manifest()

    def tearDown(self):
        # The only recursive cleanup is this explicitly checked test-owned folder.
        target = self.root.resolve(); parent = self.runs.resolve()
        assert target.is_relative_to(parent) and target != parent and target.name.startswith('fake game ')
        shutil.rmtree(target)

    @staticmethod
    def write(root, name, data):
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); return path

    def manifest(self, version='test-only-1'):
        files = [{'path': p.relative_to(self.package).as_posix(), 'bytes': p.stat().st_size, 'sha256': setup.digest(p)}
                 for p in sorted(self.package.rglob('*')) if p.is_file() and p.name != 'release-manifest.json']
        self.document = {'schema': 1, 'product': setup.PRODUCT, 'version': version, 'files': files, 'install': self.spec,
                         'compatibility': {'steam_build': '123456',
                            'game_exe_sha256': setup.digest(self.game / setup.WIN64 / 'RSDragonwilds-Win64-Shipping.exe'),
                            'ue4ss_sha256': setup.digest(self.package / 'loader/UE4SS.dll')}}
        self.write(self.package, 'release-manifest.json', setup.encoded(self.document))

    def installed_payload(self):
        return {p.relative_to(self.game).as_posix(): p.read_bytes() for p in self.game.rglob('*')
                if p.is_file() and not p.is_relative_to(self.game / setup.DATA)}

    def test_plan_is_read_only_and_install_relocates_unicode_paths(self):
        relocated = self.root / 'relocated release 雪 with spaces'
        assert self.package.resolve().is_relative_to(self.root) and relocated.resolve().is_relative_to(self.root)
        self.package.rename(relocated)
        self.package = relocated
        before = self.installed_payload()
        plan = setup.plan_install(self.package, self.game)
        self.assertEqual(plan.summary()['version'], 'test-only-1')
        self.assertEqual(before, self.installed_payload())
        self.assertFalse((self.game / setup.DATA).exists())
        result = setup.install(self.package, self.game)
        self.assertEqual(result['status'], 'installed')
        paths = load_paths(self.game / setup.PACKAGE / 'app')
        self.assertEqual(paths['assets'], self.game / setup.DATA / 'assets')
        self.assertEqual(paths['worker'], self.game / setup.PACKAGE / 'bin/worker.exe')
        lua = (self.game / setup.MOD / 'Scripts/skate_config.lua').read_text(encoding='utf8')
        self.assertIn(self.game.as_posix(), lua)
        self.assertIn('python_runtime=', lua)
        self.assertNotIn('building_collision=', lua)
        self.assertNotIn(str(self.package), lua)
        self.assertTrue((self.game / setup.MOD / 'enabled.txt').is_file())
        receipt = setup.load_receipt(self.game)
        for row in receipt['files']:
            self.assertEqual(setup.digest(self.game / row['path']), row['sha256'])

    def test_optional_building_paths_are_relocated_and_game_pin_reaches_lua(self):
        self.runtime.update(building_reader_library='bin/skate_buildings.dll',
                            building_export='building-exporter/DragonwildsMapExport.exe',
                            building_rule='config/building-collision-rule.json')
        for name in ('bin/skate_buildings.dll','building-exporter/DragonwildsMapExport.exe','config/building-collision-rule.json'):
            self.write(self.package,name,('FAKE '+name).encode())
        self.manifest();setup.install(self.package,self.game)
        paths=load_paths(self.game/setup.PACKAGE/'app')
        self.assertEqual(paths['building_reader_library'],self.game/setup.PACKAGE/'bin/skate_buildings.dll')
        self.assertEqual(paths['building_export'],self.game/setup.PACKAGE/'building-exporter/DragonwildsMapExport.exe')
        self.assertEqual(paths['building_rule'],self.game/setup.PACKAGE/'config/building-collision-rule.json')
        lua=(self.game/setup.MOD/'Scripts/skate_config.lua').read_text(encoding='utf8')
        self.assertIn('building_reader_library='+json.dumps(paths['building_reader_library'].as_posix(),ensure_ascii=False),lua)
        self.assertIn('game_exe_sha256='+json.dumps(self.document['compatibility']['game_exe_sha256']),lua)
        self.assertNotIn('building_collision_enabled',lua)
        self.assertIn('building_collision=true',lua)
        self.assertNotIn('capture_native_building_probe',lua)
        self.assertNotIn('building_export=',lua)

    def test_reader_alone_does_not_enable_production_building_collision(self):
        self.runtime['building_reader_library']='bin/skate_buildings.dll'
        self.write(self.package,'bin/skate_buildings.dll',b'FAKE optional reader')
        self.manifest();setup.install(self.package,self.game)
        lua=(self.game/setup.MOD/'Scripts/skate_config.lua').read_text(encoding='utf8')
        self.assertIn('building_reader_library=',lua)
        self.assertNotIn('building_collision=',lua)

    def test_missing_or_wrong_type_building_runtime_is_rejected_before_install(self):
        self.runtime['building_reader_library']='bin/absent.dll';self.manifest()
        with self.assertRaisesRegex(setup.InstallError,'Runtime payload is missing'):
            setup.plan_install(self.package,self.game)
        self.runtime['building_reader_library']='bin/reader.exe'
        self.write(self.package,'bin/reader.exe',b'wrong type');self.manifest()
        with self.assertRaisesRegex(setup.InstallError,'building reader must be a DLL'):
            setup.plan_install(self.package,self.game)
        self.runtime.pop('building_reader_library');self.runtime['building_export']='building-exporter/tool.dll'
        self.write(self.package,'building-exporter/tool.dll',b'wrong type');self.manifest()
        with self.assertRaisesRegex(setup.InstallError,'self-contained EXE'):
            setup.plan_install(self.package,self.game)
        self.assertFalse((self.game/setup.DATA).exists())

    def test_reused_loader_unrelated_mods_and_shared_config_are_preserved(self):
        for source, dest in [('loader/UE4SS.dll','ue4ss/UE4SS.dll'), ('loader/dwmapi.dll','dwmapi.dll')]:
            self.write(self.game, setup.WIN64 + '/' + dest, (self.package / source).read_bytes())
        foreign = self.write(self.game, setup.WIN64 + '/ue4ss/Mods/OtherMod/Scripts/main.lua', b'foreign mod')
        config = self.write(self.game, setup.WIN64 + '/ue4ss/UE4SS-settings.ini', b'custom config')
        mods = self.write(self.game, setup.WIN64 + '/ue4ss/Mods/mods.txt', b'OtherMod : 1\n')
        setup.install(self.package, self.game); result = setup.uninstall(self.game)
        self.assertEqual(foreign.read_bytes(), b'foreign mod')
        self.assertEqual(config.read_bytes(), b'custom config')
        self.assertEqual(mods.read_bytes(), b'OtherMod : 1\n')
        self.assertTrue((self.game / setup.WIN64 / 'ue4ss/UE4SS.dll').is_file())
        self.assertEqual(len(result['preserved_shared']), 5)

    def test_fresh_install_can_write_first_setup_status_when_adapter_is_enabled(self):
        mailbox = self.game / setup.MAILBOX
        self.assertFalse(mailbox.exists())
        seen = []
        def first_launch(index, name):
            if name != setup.MOD + '/enabled.txt':
                return
            paths = load_paths(self.game / setup.PACKAGE / 'app')
            self.assertEqual(paths['mailbox'], mailbox)
            # The Lua entrypoint uses io.open, which cannot create its parent.
            # No launcher, asset import or other setup command ran beforehand.
            with (paths['mailbox'] / 'setup-status.json').open('xb') as stream:
                stream.write(b'{"status":"capturing"}')
            seen.append(name)
        setup.install(self.package, self.game, checkpoint=first_launch)
        self.assertEqual(seen, [setup.MOD + '/enabled.txt'])
        status = mailbox / 'setup-status.json'
        self.assertEqual(status.read_bytes(), b'{"status":"capturing"}')
        setup.install(self.package, self.game)
        setup.uninstall(self.game)
        self.assertEqual(status.read_bytes(), b'{"status":"capturing"}')

    def test_private_mailbox_file_blocks_plan_and_install_before_payload_mutation(self):
        mailbox = self.write(self.game, setup.MAILBOX, b'existing private file')
        before = self.installed_payload()
        for action in (setup.plan_install, setup.install):
            with self.subTest(action=action.__name__), self.assertRaisesRegex(setup.InstallError, 'private mailbox directory'):
                action(self.package, self.game)
        self.assertEqual(before, self.installed_payload())
        self.assertEqual(mailbox.read_bytes(), b'existing private file')
        self.assertFalse((self.game / setup.STATE).exists())

    def test_failed_install_preserves_private_mailbox_first_run_data(self):
        before = self.installed_payload()
        status = self.game / setup.MAILBOX / 'setup-status.json'
        def fail_after_enable(index, name):
            if name == setup.MOD + '/enabled.txt':
                status.write_bytes(b'first-run diagnostics')
                raise RuntimeError('simulated failure after adapter enable')
        with self.assertRaisesRegex(RuntimeError, 'simulated failure'):
            setup.install(self.package, self.game, checkpoint=fail_after_enable)
        self.assertEqual(before, self.installed_payload())
        self.assertIsNone(setup.load_receipt(self.game))
        self.assertEqual(status.read_bytes(), b'first-run diagnostics')

    def test_loader_mismatch_refuses_without_mutation(self):
        self.write(self.game, setup.WIN64 + '/dwmapi.dll', b'foreign proxy')
        before = self.installed_payload()
        with self.assertRaisesRegex(setup.InstallError, 'never replaces'):
            setup.install(self.package, self.game)
        self.assertEqual(before, self.installed_payload())
        self.assertFalse((self.game / setup.DATA).exists())

    def test_external_loader_prerequisites_missing_wrong_exact_and_uninstall_preservation(self):
        names={'loader/UE4SS.dll':'ue4ss/UE4SS.dll','loader/dwmapi.dll':'dwmapi.dll',
               'loader/UEHelpers.lua':'ue4ss/Mods/shared/UEHelpers/UEHelpers.lua'}
        payload={destination:(self.package/source).read_bytes() for source,destination in names.items()}
        self.spec['loader_files']=[row for row in self.spec['loader_files'] if row['source'] not in names]
        self.spec['external_prerequisites']=[{'destination':name,'sha256':setup.sha(data),'bytes':len(data)} for name,data in payload.items()]
        self.document['files']=[row for row in self.document['files'] if row['path'] not in names]
        for source in names:(self.package/source).unlink()
        self.write(self.package,'release-manifest.json',setup.encoded(self.document))
        before=self.installed_payload()
        with self.assertRaisesRegex(setup.InstallError,'prerequisite first'):
            setup.install(self.package,self.game)
        self.assertEqual(before,self.installed_payload());self.assertFalse((self.game/setup.DATA).exists())
        for name,data in payload.items():self.write(self.game,setup.WIN64+'/'+name,data)
        helper=setup.WIN64+'/ue4ss/Mods/shared/UEHelpers/UEHelpers.lua'
        self.write(self.game,helper,b'changed prerequisite')
        before=self.installed_payload()
        with self.assertRaisesRegex(setup.InstallError,'prerequisite first'):
            setup.plan_install(self.package,self.game)
        self.assertEqual(before,self.installed_payload())
        self.write(self.game,helper,payload['ue4ss/Mods/shared/UEHelpers/UEHelpers.lua'])
        plan=setup.plan_install(self.package,self.game)
        self.assertFalse(any(item.path in setup.LOADER_BINARIES|{setup.UEHELPERS} for item in plan.files))
        setup.install(self.package,self.game)
        receipt=setup.load_receipt(self.game)
        self.assertEqual(sum(row['status']=='external_prerequisite' for row in receipt['shared_files']),3)
        self.assertFalse(any(row['path'] in setup.LOADER_BINARIES|{setup.UEHELPERS} for row in receipt['files']))
        setup.uninstall(self.game)
        for name,data in payload.items():self.assertEqual((self.game/setup.WIN64/name).read_bytes(),data)

    def test_unowned_file_collision_even_if_identical(self):
        self.write(self.game, setup.MOD + '/Scripts/main.lua', (self.package / 'host/main.lua').read_bytes())
        with self.assertRaisesRegex(setup.InstallError, 'Unowned file collision'):
            setup.install(self.package, self.game)

    def test_upgrade_backs_up_owned_files_and_removes_obsolete_script(self):
        setup.install(self.package, self.game)
        old = (self.game / setup.MOD / 'Scripts/main.lua').read_bytes()
        self.write(self.package, 'host/main.lua', b'new known release')
        self.spec['host_files'] = self.spec['host_files'][:1]
        self.manifest('test-only-2')
        result = setup.install(self.package, self.game)
        self.assertFalse((self.game / setup.MOD / 'Scripts/bridge.lua').exists())
        folder = self.game / setup.STATE / 'transactions' / result['transaction']
        journal = json.loads((folder / 'journal.json').read_text())
        row = next(r for r in journal['operations'] if r['path'] == setup.MOD + '/Scripts/main.lua')
        self.assertEqual((folder / row['backup']).read_bytes(), old)
        self.assertEqual(journal['status'], 'committed')

    def test_update_refuses_modified_owned_file(self):
        setup.install(self.package, self.game)
        self.write(self.game, setup.MOD + '/Scripts/main.lua', b'user edited')
        with self.assertRaisesRegex(setup.InstallError, 'modified'):
            setup.install(self.package, self.game)

    def test_uninstall_preserves_modified_shared_and_private_assets_saves(self):
        setup.install(self.package, self.game)
        changed = self.write(self.game, setup.MOD + '/Scripts/bridge.lua', b'user edited')
        asset = self.write(self.game, setup.DATA + '/assets/private/skater.glb', b'private asset')
        cache = self.write(self.game, setup.DATA + '/map-data/cache.bin', b'private cache')
        save = self.write(self.game, 'RSDragonwilds/Saved/SaveGames/world.sav', b'saved world')
        foreign = self.write(self.game, setup.PACKAGE + '/foreign.txt', b'foreign file')
        result = setup.uninstall(self.game)
        self.assertEqual(result['preserved_modified'], [setup.MOD + '/Scripts/bridge.lua'])
        for path in (changed, asset, cache, save, foreign): self.assertTrue(path.is_file())
        self.assertFalse((self.game / setup.MOD / 'enabled.txt').exists())
        self.assertFalse((self.game / setup.MOD / 'Scripts/main.lua').exists())

    def test_failed_install_rolls_back_every_written_file(self):
        before = self.installed_payload()
        def fail(index, name):
            if index == 4: raise RuntimeError('simulated locked destination')
        with self.assertRaisesRegex(RuntimeError, 'simulated'):
            setup.install(self.package, self.game, checkpoint=fail)
        self.assertEqual(before, self.installed_payload())
        self.assertIsNone(setup.load_receipt(self.game))
        journals = list((self.game / setup.STATE / 'transactions').glob('*/journal.json'))
        self.assertEqual(json.loads(journals[0].read_text())['status'], 'rolled_back')

    def test_failed_upgrade_and_uninstall_restore_exact_receipt(self):
        setup.install(self.package, self.game)
        before = self.installed_payload(); receipt = (self.game / setup.RECEIPT).read_bytes()
        self.write(self.package, 'host/main.lua', b'new release'); self.manifest('test-only-2')
        def fail(index, name):
            if index == 1: raise RuntimeError('injected')
        with self.assertRaises(RuntimeError): setup.install(self.package, self.game, checkpoint=fail)
        self.assertEqual(before, self.installed_payload()); self.assertEqual(receipt, (self.game / setup.RECEIPT).read_bytes())
        with self.assertRaises(RuntimeError): setup.uninstall(self.game, checkpoint=fail)
        self.assertEqual(before, self.installed_payload()); self.assertEqual(receipt, (self.game / setup.RECEIPT).read_bytes())

    def test_explicit_crash_recovery_and_unknown_edits_are_preserved(self):
        before = self.installed_payload()
        def fail(index, name): raise RuntimeError('crash simulation')
        with patch.object(setup, 'rollback', side_effect=setup.InstallError('crash before rollback')):
            with self.assertRaisesRegex(setup.InstallError, 'requires recovery'):
                setup.install(self.package, self.game, checkpoint=fail)
        self.assertNotEqual(before, self.installed_payload())
        self.assertEqual(len(setup.recover(self.game)['recovered']), 1)
        self.assertEqual(before, self.installed_payload())
        def changed(index, name):
            (self.game / name).write_bytes(b'external edit'); raise RuntimeError('stop')
        with self.assertRaisesRegex(setup.InstallError, 'independently changed'):
            setup.install(self.package, self.game, checkpoint=changed)
        externally_changed = self.installed_payload()
        with self.assertRaisesRegex(setup.InstallError, 'independently changed'):
            setup.recover(self.game)
        self.assertEqual(externally_changed, self.installed_payload())

    def test_install_retry_recovers_before_ownership_checks(self):
        def fail(index, name): raise RuntimeError('crash simulation')
        for upgrade in (False, True):
            with self.subTest(upgrade=upgrade):
                if upgrade:
                    self.write(self.package, 'host/main.lua', b'next release'); self.manifest('test-only-2')
                with patch.object(setup, 'rollback', side_effect=setup.InstallError('process stopped')):
                    with self.assertRaisesRegex(setup.InstallError, 'requires recovery'):
                        setup.install(self.package, self.game, checkpoint=fail)
                self.assertEqual(setup.install(self.package, self.game)['status'], 'installed')

    def test_prototype_enabled_by_file_or_mod_list_blocks_install(self):
        marker = self.write(self.game, setup.WIN64 + '/ue4ss/Mods/DragonwildsSkateProbe/enabled.txt', b'')
        with self.assertRaisesRegex(setup.InstallError, 'Disable the prototype'): setup.install(self.package, self.game)
        marker.unlink()
        self.write(self.game, setup.WIN64 + '/ue4ss/Mods/mods.txt', b'# Other : 1\nDragonwildsSkateProbe : 1\n')
        with self.assertRaisesRegex(setup.InstallError, 'Disable the prototype'): setup.install(self.package, self.game)

    def test_incomplete_or_tampered_payload_never_installs(self):
        self.spec['loader_files'] = self.spec['loader_files'][1:]; self.manifest()
        with self.assertRaisesRegex(setup.InstallError, 'must both be specified'): setup.install(self.package, self.game)
        self.spec['runtime'].pop('worker'); self.manifest()
        with self.assertRaisesRegex(setup.InstallError, 'must all be supplied'): setup.install(self.package, self.game)
        self.assertFalse((self.game / setup.PACKAGE).exists())

    def test_optional_board_texture_requires_actual_payload(self):
        self.spec['runtime']['board_deck_texture'] = 'art/board.png'; self.manifest()
        with self.assertRaisesRegex(setup.InstallError, 'Runtime payload is missing'): setup.install(self.package, self.game)
        self.write(self.package, 'art/board.png', b'numeric test image'); self.manifest()
        setup.install(self.package, self.game)
        self.assertEqual(load_paths(self.game / setup.PACKAGE / 'app')['board_deck_texture'], self.game / setup.PACKAGE / 'art/board.png')

    def test_path_escape_alias_and_forged_receipt_are_rejected(self):
        for name in ('../outside', 'a/../outside', 'C:/outside', 'a\\outside', '/outside', 'a//b', 'CON.txt', 'a.', 'a ', 'x:y'):
            with self.subTest(name=name), self.assertRaises(setup.InstallError): setup.safe(self.game, name)
        setup.install(self.package, self.game)
        receipt = setup.load_receipt(self.game)
        receipt['files'][0]['path'] = 'RSDragonwilds/Saved/SaveGames/world.sav'
        self.write(self.game, setup.RECEIPT, setup.encoded(receipt))
        with self.assertRaisesRegex(setup.InstallError, 'Invalid owned file'): setup.uninstall(self.game)

    def test_symlink_target_is_rejected_even_within_game(self):
        target = self.game / 'safe-other'; target.mkdir()
        link = self.game / setup.PACKAGE
        try: os.symlink(target, link, target_is_directory=True)
        except OSError as error:
            if os.name != 'nt': self.skipTest('Host does not permit test links: ' + str(error))
            # Junction creation needs no symlink privilege. Both paths were
            # created under this exact test-owned folder; no removal command.
            assert link.parent.resolve().is_relative_to(self.root) and target.resolve().is_relative_to(self.root)
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            script = "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path " + quote(link) + ' -Value ' + quote(target) + ' | Out-Null'
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                base64.b64encode(script.encode('utf-16le')).decode()], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        with self.assertRaisesRegex(setup.InstallError, 'Symlinks and junctions'): setup.install(self.package, self.game)

    def test_wrong_game_or_build_is_rejected(self):
        self.write(self.game, setup.WIN64 + '/RSDragonwilds-Win64-Shipping.exe', b'changed')
        with self.assertRaisesRegex(setup.InstallError, 'executable'): setup.install(self.package, self.game)
        self.assertFalse((self.game / setup.DATA).exists())

    def test_runtime_in_use_and_concurrent_setup_are_refused(self):
        setup.install(self.package, self.game)
        before = self.installed_payload()
        with patch.object(setup.sys, 'executable', str(self.game / setup.PACKAGE / 'python/python.exe')):
            with self.assertRaisesRegex(setup.InstallError, 'extracted release Python'): setup.uninstall(self.game)
        with setup.install_lock(self.game):
            with self.assertRaisesRegex(setup.InstallError, 'Another setup'): 
                with setup.install_lock(self.game): self.fail('acquired a second lock')
        self.assertEqual(before, self.installed_payload())


if __name__ == '__main__':
    unittest.main(verbosity=2)
