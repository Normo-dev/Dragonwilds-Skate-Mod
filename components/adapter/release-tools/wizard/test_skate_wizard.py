"""Fake game only: no real game, tool execution, Steam launch or installation."""
from pathlib import Path
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent.parent)]
import skate_wizard as wizard
import skate_install as install
import test_skate_install as fixture_module


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture_module.InstallTests(); self.f.setUp()
        self.guard = patch('skate_process.offline_maintenance', lambda _: contextlib.nullcontext())
        self.guard.start()
        self.tools = patch.object(wizard, 'get_tool_archive', return_value=self.f.root / 'verified-tool.zip')
        self.download = self.tools.start()

    def tearDown(self):
        self.tools.stop(); self.guard.stop(); self.f.tearDown()

    def request(self, action, **extra):
        return wizard.run_request({'action': action, 'game': str(self.f.game), **extra}, self.f.package)

    def loader_zip(self):
        files = [r for r in self.f.spec['loader_files'] if not r['destination'].endswith(('.ini', '.txt'))]
        self.f.spec['external_prerequisites'] = [dict(destination=r['destination'],
            bytes=(self.f.package / r['source']).stat().st_size,
            sha256=install.digest(self.f.package / r['source'])) for r in files]
        self.f.spec['loader_files'] = [r for r in self.f.spec['loader_files'] if r not in files]
        self.f.manifest()
        path = self.f.root / 'official fixture ü.zip'
        with zipfile.ZipFile(path, 'w') as z:
            for r in files: z.writestr(r['destination'], (self.f.package / r['source']).read_bytes())
            z.writestr('../../unrelated.txt', b'NEVER EXTRACT THIS')
        return path

    def test_loader_import_install_update_uninstall_preserves_shared_and_private(self):
        archive = self.loader_zip()
        self.assertEqual(self.request('check')['status'], 'Action needed')
        self.request('import-loader', loader_zip=str(archive))
        self.assertFalse((self.f.root / 'unrelated.txt').exists())
        self.assertEqual(self.request('check')['status'], 'Ready to install or update')
        self.assertEqual(self.request('install')['details']['status'], 'installed')
        private = self.f.game / install.DATA / 'assets/private-file'
        private.parent.mkdir(); private.write_bytes(b'PRIVATE GAME DATA')
        self.f.manifest('new-version')
        self.request('install')
        self.assertEqual(self.request('uninstall')['details']['status'], 'uninstalled')
        self.assertEqual(private.read_bytes(), b'PRIVATE GAME DATA')
        for row in self.f.spec['external_prerequisites']:
            self.assertEqual(install.digest(self.f.game / install.WIN64 / row['destination']), row['sha256'])

    def test_loader_wrong_existing_refused_and_rollback_preserves_unknown_files(self):
        archive = self.loader_zip()
        target = self.f.game / install.WIN64 / 'dwmapi.dll'; target.write_bytes(b'FOREIGN')
        with self.assertRaisesRegex(ValueError, 'different shared loader'):
            self.request('import-loader', loader_zip=str(archive))
        self.assertEqual(target.read_bytes(), b'FOREIGN')
        target.unlink()  # Exact fixture file created by this test, under checked fake root.
        original = install.transaction
        def fail_after_first(*args, **kwargs):
            def checkpoint(*_): raise RuntimeError('fixture interruption')
            return original(*args, **kwargs, checkpoint=checkpoint)
        with patch.object(install, 'transaction', fail_after_first), self.assertRaisesRegex(RuntimeError, 'fixture interruption'):
            self.request('import-loader', loader_zip=str(archive))
        self.assertFalse(target.exists())
        self.request('import-loader', loader_zip=str(archive))
        self.assertTrue(target.is_file())

    def test_loader_changed_archive_member_is_refused_before_mutation(self):
        archive = self.loader_zip()
        with zipfile.ZipFile(archive, 'a') as z: z.writestr('dwmapi.dll', b'ALTERED')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.request('import-loader', loader_zip=str(archive))
        self.assertFalse((self.f.game / install.WIN64 / 'dwmapi.dll').exists())

    def test_subprocess_is_argument_vector_and_errors_are_preserved(self):
        args = [r'C:\new folder 漢字\python.exe', r'C:\new folder\app.py', 'import-assets', r'C:\own & $(data) `quote\assets']
        result = subprocess.CompletedProcess(args, 0, b'OK')
        with patch('subprocess.run', return_value=result) as run:
            self.assertEqual(wizard.launch_command(args), 'OK')
            self.assertEqual(run.call_args.args[0], args)
            self.assertNotIn('shell', run.call_args.kwargs)
        with patch('subprocess.run', return_value=subprocess.CompletedProcess(args, 1, b'not ready')):
            with self.assertRaisesRegex(ValueError, 'not ready'): wizard.launch_command(args)

    def test_external_tool_runs_inside_private_log_directory(self):
        job = self.f.root / 'private conversion job'; job.mkdir()
        log = job / 'extract.log'
        args = [str(job / 'xdvdfs.exe'), 'unpack', str(self.f.root / 'own.iso'), str(job / 'extracted')]
        with patch('subprocess.run', return_value=subprocess.CompletedProcess(args, 0)) as run:
            self.assertEqual(wizard.launch_command(args, log=log), str(log))
            self.assertEqual(run.call_args.kwargs['cwd'], str(job.resolve()))
            self.assertNotIn('shell', run.call_args.kwargs)

    def test_auto_resume_does_not_change_inputs_or_add_shell_arguments(self):
        install.install(self.f.package, self.f.game)
        # Add the corresponding script to the fake release's manifest, as real
        # releases do. No fixture executable is ever started.
        self.f.write(self.f.package, 'app/skate_setup.py', b'FAKE SETUP')
        self.f.manifest(); install.install(self.f.package, self.f.game)
        root = self.f.game / install.PACKAGE
        with patch.object(wizard, 'launch_command', return_value='verified') as launch:
            self.request('prepare-map')
            self.assertEqual(launch.call_args.args[0], [root / 'python/python.exe', root / 'app/skate_setup.py', 'prepare-map'])
            marker = self.f.game / install.DATA / 'map-data/setup-state.json'
            marker.parent.mkdir(); marker.write_text('{}')
            self.request('prepare-map')
            self.assertEqual(launch.call_args.args[0][-1], '--resume')
            self.assertEqual(marker.read_text(), '{}')

    def test_convert_requires_explicit_tools_and_never_reuses_output(self):
        install.install(self.f.package, self.f.game)
        with self.assertRaisesRegex(ValueError, 'Confirm'):
            self.request('convert', source='anything')
        extracted = self.f.root / 'own extracted ü'; extracted.mkdir()
        source = extracted / 'default.xex'; source.write_bytes(b'OWN INPUT')
        (extracted / 'data').mkdir()
        calls = []
        def run(args, log):
            calls.append(list(map(Path, args[::2])))
            output = Path(args[-1]); self.assertFalse(output.exists())
            self.assertFalse(output.with_name(output.name + '.partial').exists())
            (output / 'assets').mkdir(parents=True); (output / 'assets/owned.dat').write_bytes(b'OWN CONVERTED')
        with patch.object(wizard, 'tool_bytes', return_value=b'FAKE NEVER EXECUTED'):
            first = wizard.private_conversion(self.f.game, source, '', '', run)
            second = wizard.private_conversion(self.f.game, source, '', '', run)
        self.assertNotEqual(first, second)
        self.assertEqual((first / 'owned.dat').read_bytes(), b'OWN CONVERTED')
        self.assertEqual(source.read_bytes(), b'OWN INPUT')
        self.assertTrue(first.is_relative_to(self.f.game / install.DATA / 'conversion'))

    def test_iso_pipeline_checks_extracted_layout_before_conversion(self):
        iso = self.f.root / 'own disc.iso'; iso.write_bytes(b'OWN ISO')
        calls = []
        def incomplete(args, log): calls.append(args)
        with patch.object(wizard, 'tool_bytes', return_value=b'FAKE'), self.assertRaisesRegex(ValueError, 'did not produce'):
            wizard.private_conversion(self.f.game, iso, '', '', incomplete)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 'unpack')
        self.assertEqual(calls[0][2], iso)
        self.assertEqual(Path(calls[0][3]).name, 'extracted')
        self.assertEqual(iso.read_bytes(), b'OWN ISO')

    def test_iso_only_fetches_required_tools_then_extracts_and_converts_privately(self):
        iso = self.f.root / 'own disc.iso'; iso.write_bytes(b'OWN ISO')
        calls = []
        def run(args, log):
            calls.append(args)
            if args[1] == 'unpack':
                self.assertEqual(args[2], iso)
                destination = Path(args[3])
                (destination / 'data').mkdir(parents=True)
                (destination / 'default.xex').write_bytes(b'OWN EXTRACTED')
            else:
                destination = Path(args[-1])
                self.assertEqual(args[1], '--xex')
                self.assertEqual(Path(args[2]).read_bytes(), b'OWN EXTRACTED')
                (destination / 'assets').mkdir(parents=True)
        with patch.object(wizard, 'tool_bytes', return_value=b'FAKE NEVER EXECUTED'):
            result = wizard.private_conversion(self.f.game, iso, '', '', run)
        self.assertEqual([call.args[1] for call in self.download.call_args_list], ['converter', 'extractor'])
        self.assertEqual(len(calls), 2)
        self.assertTrue(result.is_relative_to(self.f.game / install.DATA / 'conversion'))
        self.assertEqual(iso.read_bytes(), b'OWN ISO')

    def test_slow_iso_and_conversion_emit_elapsed_heartbeat_without_percentage(self):
        iso = self.f.root / 'own disc.iso'; iso.write_bytes(b'OWN ISO')
        events = []; heartbeats = {phase: threading.Event() for phase in ('extracting-iso', 'converting-assets')}
        def emit(value):
            events.append(value)
            phase = value.get('phase')
            if phase in heartbeats and value.get('changed') is False:
                heartbeats[phase].set()
        original = wizard.action_progress
        def frequent(*args, **kwargs):
            return original(*args, interval=0.01, **kwargs)
        def run(args, log):
            phase = 'extracting-iso' if args[1] == 'unpack' else 'converting-assets'
            self.assertTrue(heartbeats[phase].wait(1), 'Heartbeat must continue while the external tool blocks')
            if args[1] == 'unpack':
                output = Path(args[3]); (output / 'data').mkdir(parents=True)
                (output / 'default.xex').write_bytes(b'OWN EXTRACTED')
            else:
                (Path(args[-1]) / 'assets').mkdir(parents=True)
        with patch.object(wizard, 'tool_bytes', return_value=b'FAKE'), patch.object(wizard, 'emit', emit), \
             patch.object(wizard, 'action_progress', frequent):
            result = wizard.private_conversion(self.f.game, iso, '', '', run)
        self.assertTrue(result.is_dir())
        for phase in heartbeats:
            progress = [v for v in events if v.get('phase') == phase]
            self.assertGreaterEqual(len(progress), 2)
            self.assertTrue(all('elapsed_seconds' in v and 'percent' not in v and 'ok' not in v for v in progress))
        self.assertFalse(any(t.name == 'setup-progress' for t in threading.enumerate()))

    def test_main_real_result_follows_stopped_progress_even_after_native_ready(self):
        import io
        events = []
        def run(_):
            source = lambda: {'phase':'ready', 'label':'Finishing cache preparation', 'state':'end'}
            with wizard.action_progress(wizard.emit, 'checking', 'Checking', source=source):
                raise ValueError('Actual verification failed')
        stdin = type('Stdin', (), {'buffer': io.BytesIO(b'{"action":"prepare-map","game":"fixture"}')})()
        with patch.object(wizard, 'run_request', run), patch.object(wizard, 'emit', events.append), patch.object(sys, 'stdin', stdin):
            self.assertEqual(wizard.main(), 1)
        self.assertEqual(events[0]['phase'], 'ready')
        self.assertFalse(events[-1]['ok'])
        self.assertEqual(events[-1]['error'], 'Actual verification failed')
        self.assertTrue(all('ok' not in v for v in events[:-1]))
        self.assertFalse(any(t.name == 'setup-progress' for t in threading.enumerate()))

    @unittest.skipUnless(shutil.which('powershell.exe'), 'Windows PowerShell is required for the setup UI')
    def test_ui_real_progress_functions_use_counts_and_preserve_final_result(self):
        # Extract only the real display functions. No form, child setup process,
        # Steam launch or installation is started by this test.
        command = r'''
$parseErrors=$null
$tree=[System.Management.Automation.Language.Parser]::ParseFile($env:WIZARD_PROGRESS_SOURCE,[ref]$null,[ref]$parseErrors)
if ($parseErrors.Count) {throw 'Wizard parse failed'}
foreach ($name in @('Get-ElapsedText','Set-ProgressState')) {
    $node=$tree.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
    . ([scriptblock]::Create($node.Extent.Text))
}
$progress=[pscustomobject]@{Style='Blocks';Value=0}
$script:gotResult=$false;$script:phaseKey='';$script:phaseClock=$null;$script:phaseText=''
$script:messages=New-Object System.Collections.Generic.List[string]
function Add-Log([string]$text) {$script:messages.Add($text)}
Set-ProgressState ([pscustomobject]@{phase='preparing_exposed_cells';label='Preparing';message='Preparing 99 / 100';completed=99999;total=100000;changed=$true})
if ($progress.Style -ne 'Continuous' -or $progress.Value -ne 999) {throw 'Incomplete counts displayed as finished'}
$firstClock=$script:phaseClock
Set-ProgressState ([pscustomobject]@{phase='preparing_exposed_cells';label='Preparing';message='Preparing 99 / 100';completed=99999;total=100000;changed=$false})
if ($script:messages.Count -ne 1 -or $script:phaseClock -ne $firstClock) {throw 'Heartbeat repeated log or reset stage time'}
Set-ProgressState ([pscustomobject]@{phase='joining_grind_paths';label='Joining';message='Joining';changed=$true})
if ($progress.Style -ne 'Marquee' -or $progress.Value -ne 0 -or $script:phaseClock -eq $firstClock) {throw 'New stage retained stale counts or clock'}
Set-ProgressState ([pscustomobject]@{phase='ready';label='Verifying';message='Verifying';completed='100';total=100;changed=$true})
if ($progress.Style -ne 'Marquee') {throw 'String counts became a percentage'}
$script:gotResult=$true;$script:phaseText='Action needed';$progress.Style='Blocks';$progress.Value=0
Set-ProgressState ([pscustomobject]@{phase='ready';label='Finishing';message='Finishing';completed=100;total=100;changed=$true})
if ($script:phaseText -ne 'Action needed' -or $progress.Value -ne 0) {throw 'Telemetry replaced the final result'}
if ((Get-ElapsedText 3663) -ne '1h 01m 03s') {throw 'Elapsed time formatting failed'}
'Progress display verified'
'''
        env = dict(os.environ, WIZARD_PROGRESS_SOURCE=str(HERE / 'setup-wizard.ps1'))
        result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command], env=env,
                                capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Progress display verified', result.stdout)

    def test_manual_archives_skip_download_and_bad_source_never_downloads(self):
        iso = self.f.root / 'own disc.iso'; iso.write_bytes(b'OWN ISO')
        with patch.object(wizard, 'tool_bytes', return_value=b'FAKE'), self.assertRaisesRegex(ValueError, 'did not produce'):
            wizard.private_conversion(self.f.game, iso, 'converter.zip', 'extractor.zip', lambda *a, **k: None)
        self.download.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'Choose your own'):
            wizard.private_conversion(self.f.game, self.f.root / 'missing.iso', '', '')
        self.download.assert_not_called()

    def test_foreign_conversion_root_is_preserved(self):
        source = self.f.root / 'default.xex'; source.write_bytes(b'OWN'); (self.f.root / 'data').mkdir()
        foreign = self.f.game / install.DATA / 'conversion/valuable'
        foreign.parent.mkdir(parents=True); foreign.write_bytes(b'KEEP')
        with patch.object(wizard, 'tool_bytes', return_value=b'FAKE'), self.assertRaisesRegex(ValueError, 'ownership'):
            wizard.private_conversion(self.f.game, source, '', '', lambda *_:None)
        self.assertEqual(foreign.read_bytes(), b'KEEP')

    def test_tool_hash_and_archive_member_are_both_checked(self):
        archive = self.f.root / 'tool.zip'
        with zipfile.ZipFile(archive, 'w') as z:z.writestr('artifacts/extract-xiso.exe', b'FAKE')
        with self.assertRaisesRegex(ValueError, 'SHA-256'): wizard.tool_bytes(archive, 'extractor')
        with patch.dict(wizard.TOOLS, {'extractor': {'sha256':install.digest(archive), 'member':'artifacts/extract-xiso.exe'}}):
            self.assertEqual(wizard.tool_bytes(archive, 'extractor'), b'FAKE')

    def test_request_rejects_arbitrary_command_and_extra_fields(self):
        for request in [{'action':'shell','game':'x'}, {'action':'check','game':'x','command':'anything'},
                        {'action':'check','game':'x\0bad'}, {'action':'convert','game':'x','confirm_tools':'yes'}]:
            with self.assertRaises(ValueError): wizard.request_value(request)

    def test_game_launch_is_explicit_and_play_requires_readiness(self):
        install.install(self.f.package, self.f.game)
        with patch('os.startfile') as launch, patch('skate_preflight.check_installation', side_effect=ValueError('not prepared')):
            with self.assertRaisesRegex(ValueError, 'not prepared'): self.request('play')
            launch.assert_not_called()
            self.request('capture'); launch.assert_called_once_with('steam://rungameid/1374490')

    def test_steam_update_preserves_repair_access_but_blocks_game_reading(self):
        import skate_setup
        install.install(self.f.package, self.f.game)
        exe = self.f.game / install.WIN64 / 'RSDragonwilds-Win64-Shipping.exe'
        exe.write_bytes(b'FAKE UPDATED GAME')
        for action in ('install', 'import-loader', 'play', 'capture', 'prepare-map', 'prepare-buildings', 'verify'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'executable does not match'):
                self.request(action)
        self.request('recover')
        with patch.object(wizard, 'launch_command', return_value='stopped') as launch:
            self.request('stop')
            self.assertEqual(launch.call_args.args[0][-1], 'stop')
        target = self.f.game / install.DATA / 'map-data'
        target.mkdir()
        marker = {'identity': {'schema':1, 'product':'DragonwildsSkateSetup', 'target':str(target)},
                  'ready':False, 'completed':['partial']}
        (target / 'setup-state.json').write_text(json.dumps(marker))
        (target / 'valuable-private-data').write_bytes(b'PRESERVE ALL BYTES')
        # The real API retains its own offline lock; only that process guard is
        # replaced because the fixture is not an actual running game/helper.
        with patch.object(skate_setup, 'offline_maintenance', lambda _:contextlib.nullcontext()):
            result = self.request('archive-map')['details']
        self.assertFalse(target.exists())
        self.assertEqual((Path(result['backup']) / 'valuable-private-data').read_bytes(), b'PRESERVE ALL BYTES')
        self.request('uninstall')
        self.assertEqual(exe.read_bytes(), b'FAKE UPDATED GAME')


if __name__ == '__main__': unittest.main()
