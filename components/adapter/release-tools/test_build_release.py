"""Isolated allowlist/byte-identity tests; no real game or production output."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parent))
import build_release as b


def zip_bytes(files):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in files.items():z.writestr(name,data)
    return stream.getvalue()


class BuildReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='skate package \u96ea ')
        self.root=Path(self.temp.name)
    def tearDown(self):
        root=self.root.resolve();base=Path(tempfile.gettempdir()).resolve()
        assert root.is_relative_to(base) and root!=base and root.name.startswith('skate package ')
        self.temp.cleanup()
    def write(self,name,data=b'fixture'):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data,str) else data);return p
    def rec(self,name,data=b'fixture'):
        p=self.write(name,data);return {'path':str(p),'sha256':b.digest(p),'label':'verified-fixture/'+name}
    def textdir(self,name):
        self.write(name+'/LICENSE.txt','Fixture notice\n');return str(self.root/name)
    def recipe(self):
        for name in ['skate_launcher','skate_setup','skate_cache_worker','runtime_paths','skate_preflight']:
            self.write('app/'+name+'.py','import json\n' if name!='skate_launcher' else 'import runtime_paths, skate_preflight\n')
        self.write('lua/bridge.lua',"return require('leaf')\n")
        self.write('lua/leaf.lua',"local x=require('UEHelpers'); return {}\n")
        self.write('lua/bootstrap.lua',"local x=require('skate_config');return require('bridge')\n")
        worker={'LICENSE':b'GPL text','UPSTREAM-README.md':b'upstream license grant','BUILDING.md':b'build instructions',
            'DEPENDENCY-NOTICES.json':b'{}','src/main.rs':b'fn main(){}'}
        fingerprint=b.sha(json.dumps([{'path':'src/main.rs','sha256':b.sha(worker['src/main.rs'])}],sort_keys=True,separators=(',',':')).encode())
        worker['SCENE-CACHE-VERIFICATION.json']=b.encoded({'artifact_sha256':b.sha(b'MZ-worker'),'compiled_source_sha256':fingerprint})
        worker['SOURCE-PROVENANCE.json']=b.encoded({'files':[{'path':name,'sha256':b.sha(data)} for name,data in worker.items()]})
        wz=self.write('worker.zip',zip_bytes(worker));ez=self.write('exporter.zip',zip_bytes({'LICENSE':b'GPL','src/main.cs':b'class Program {}'}))
        self.write('exporter/DragonwildsMapExport.exe',b'MZ-exporter')
        ei=self.write('exporter-inventory.json',b.encoded({'schema':1,'files':[{'path':'DragonwildsMapExport.exe','bytes':11,'sha256':b.sha(b'MZ-exporter')}],
            'source_archive':{'sha256':b.digest(ez),'bytes':ez.stat().st_size}}))
        archives=[]
        for filename,contents,prefix in [('python-3.13.16-embed-amd64.zip',{'python.exe':b'MZ-python','python313.zip':zip_bytes({'os.pyc':b'code'}),'python313._pth':b'old','LICENSE.txt':b'PSF'},''),
            ('numpy-2.3.5.whl',{'numpy/__init__.py':b'# numpy','numpy/tests/private.py':b'not included','numpy-2.dist-info/LICENSE.txt':b'BSD'},'Lib/site-packages/'),
            ('pillow-12.3.0.whl',{'PIL/__init__.py':b'# pillow','pillow-12.dist-info/LICENSE.txt':b'MIT'},'Lib/site-packages/')]:
            p=self.write('downloads/'+filename,zip_bytes(contents));archives.append({'filename':filename,'sha256':b.digest(p),'url':'https://example.test/'+filename})
            for name,data in contents.items():self.write('python/'+prefix+name,data)
        sources=self.write('python-sources.json',b.encoded({'schema':1,'dependencies':archives}))
        helpers={}
        for key in ('ipc','render'):
            for name in ('Cargo.toml','Cargo.lock','src/lib.rs'):self.write('helpers/'+key+'/'+name,'# fixture\n')
            helpers[key]={'binary':self.rec(key+'.dll',b'MZ-'+key.encode()),'source_root':str(self.root/'helpers'/key),'notices_root':self.textdir('helper-notices/'+key)}
            records=[{'path':n,'sha256':b.sha(d)} for n,d in sorted(b.rust_sources(self.root/'helpers'/key).items())]
            helpers[key]['source_sha256']=b.sha(json.dumps(records,sort_keys=True,separators=(',',':')).encode())
        loader={name:self.rec('loader/'+name,b'MZ-loader' if name.endswith('.dll') else b'return {}') for name in
            ('dwmapi.dll','ue4ss/UE4SS.dll','ue4ss/Mods/shared/UEHelpers/UEHelpers.lua')}
        compat=self.write('compatibility.json',b.encoded({'steam_build':'123','game_exe_sha256':'a'*64,'ue4ss_sha256':loader['ue4ss/UE4SS.dll']['sha256']}))
        license=self.rec('LICENSE',b'GPL fixture')
        return {'schema':1,'version':'test-candidate','app_root':str(self.root/'app'),'lua_root':str(self.root/'lua'),'bootstrap':str(self.root/'lua/bootstrap.lua'),
            'installer':str(Path(b.__file__).with_name('skate_install.py')),'own_license':license,
            'worker':{'binary':self.rec('worker.exe',b'MZ-worker'),'source_zip':str(wz),'source_sha256':b.digest(wz)},'helpers':helpers,
            'exporter':{'root':str(self.root/'exporter'),'inventory':str(ei),'source_zip':str(ez),'source_sha256':b.digest(ez),'notices_root':self.textdir('exporter-notices')},
            'python':{'root':str(self.root/'python'),'sources':str(sources),'downloads':str(self.root/'downloads')},
            'sdl':{'binary':self.rec('SDL3.dll',b'MZ-sdl'),'notices_root':self.textdir('sdl-notices')},
            'artwork':{'mount_icon':self.rec('mount.png',b'PNG-icon'),'board_deck_texture':self.rec('deck.png',b'PNG-deck')},
            'loader':{'files':loader,'notices_root':self.textdir('loader-notices')},'compatibility':str(compat)}

    def test_complete_stage_bytes_closure_isolation_and_relocation(self):
        recipe=self.recipe();output=self.root/'stage with spaces \u96ea'
        result=b.build(recipe,output);self.assertFalse(result['published'])
        manifest=json.loads((output/'release-manifest.json').read_text())
        self.assertIn('board_deck_texture',manifest['install']['runtime'])
        self.assertIn('../app\n',(output/'python/python313._pth').read_text())
        self.assertFalse((output/'python/Lib/site-packages/numpy/tests').exists())
        self.assertEqual((output/'host/main.lua').read_bytes(),Path(recipe['bootstrap']).read_bytes())
        self.assertEqual(manifest['files'],sorted(manifest['files'],key=lambda r:r['path']))
        self.assertFalse(any('runtime.json'==Path(r['path']).name for r in manifest['files']))
        moved=self.root/'relocated different \u96ea';output.rename(moved)
        self.assertEqual(result['manifest_sha256'],b.audit_package(moved)['manifest_sha256'])
        with self.assertRaises(b.BuildError):b.build(recipe,moved)

    def test_optional_deck_not_required_or_invented(self):
        recipe=self.recipe();recipe['artwork'].pop('board_deck_texture');output=self.root/'stage'
        b.build(recipe,output);m=json.loads((output/'release-manifest.json').read_text())
        self.assertNotIn('board_deck_texture',m['install']['runtime'])

    def test_wizard_closure_and_exact_source_roundtrip(self):
        recipe=self.recipe()
        self.write('app/skate_wizard.py','import skate_install, skate_setup\n')
        self.write('wizard/test_skate_wizard.py','import unittest\n')
        recipe['wizard']={name:self.rec('wizard/'+name,body) for name,body in
            [('Setup.cmd',b'@echo off\r\n'),('setup-wizard.ps1',b'param([switch]$ValidateOnly)\n')]}
        recipe['readme']=self.rec('intro.md',b'# Dragonwilds Skate\nRun Setup.cmd\n')
        output=self.root/'guided';b.build(recipe,output)
        closure=json.loads((output/'notices/runtime-closure.json').read_text())
        self.assertIn('skate_install',closure['python']['edges']['skate_wizard'])
        self.assertEqual((output/'README.md').read_bytes(),b'# Dragonwilds Skate\nRun Setup.cmd\n')
        with zipfile.ZipFile(output/'sources/adapter-source.zip') as source:
            for name in recipe['wizard']:
                self.assertEqual((output/name).read_bytes(),source.read('wizard/'+name))
            self.assertEqual((output/'app/skate_wizard.py').read_bytes(),source.read('app/skate_wizard.py'))
        recipe['wizard']['extra.cmd']=recipe['wizard']['Setup.cmd']
        with self.assertRaisesRegex(b.BuildError,'exactly'):
            b.build(recipe,self.root/'unexpected-wizard-file')

    def test_optional_buildings_helper_and_separate_exporter_preserve_base_payload(self):
        recipe=self.recipe()
        baseline=self.root/'baseline';b.build(recipe,baseline)
        for name in ('Cargo.toml','Cargo.lock','src/lib.rs','src/loader_constants.rs'):
            self.write('helpers/buildings/'+name,'# building fixture\n')
        helper={'binary':self.rec('buildings.dll',b'MZ-buildings'),
                'source_root':str(self.root/'helpers/buildings'),
                'notices_root':self.textdir('helper-notices/buildings')}
        records=[{'path':n,'sha256':b.sha(d)} for n,d in sorted(b.rust_sources(helper['source_root']).items())]
        helper['source_sha256']=b.sha(json.dumps(records,sort_keys=True,separators=(',',':')).encode())
        recipe['helpers']['buildings']=helper
        recipe['building_exporter']=dict(recipe['exporter'])
        recipe['building_rule']=self.rec('building-rule.json',b.encoded({'schema':'S3BUILDINGRULE1','verified':True,'game_exe_sha256':'a'*64}))
        stage=self.root/'with buildings';b.build(recipe,stage)
        manifest=json.loads((stage/'release-manifest.json').read_text())
        runtime=manifest['install']['runtime']
        self.assertEqual(runtime['map_export'],'exporter/DragonwildsMapExport.exe')
        self.assertEqual(runtime['building_export'],'building-exporter/DragonwildsMapExport.exe')
        self.assertEqual(runtime['building_reader_library'],'bin/skate_buildings.dll')
        self.assertEqual(runtime['building_rule'],'config/building-collision-rule.json')
        for path in (baseline/'exporter').rglob('*'):
            if path.is_file():self.assertEqual(path.read_bytes(),(stage/'exporter'/path.relative_to(baseline/'exporter')).read_bytes())
        self.assertEqual((baseline/'notices/exporter-inventory.json').read_bytes(),(stage/'notices/exporter-inventory.json').read_bytes())
        with zipfile.ZipFile(stage/'sources/adapter-source.zip') as source:
            self.assertEqual(source.read('helpers/buildings/src/loader_constants.rs'),b'# building fixture\n')
            closure=json.loads(source.read('SOURCE-CLOSURE.json'))
            self.assertEqual([row['component'] for row in closure['helpers']],['ipc','render','buildings'])
        self.assertTrue((stage/'sources/building-exporter-source.zip').is_file())
        self.assertTrue((stage/'notices/building-exporter-inventory.json').is_file())
        self.assertFalse(any(p.name in ('building-catalogue.json','building-supplement.json') for p in stage.rglob('*')))
        recipe['building_rule']=self.rec('wrong-rule.json',b.encoded({'schema':'S3BUILDINGRULE1','verified':True,'game_exe_sha256':'b'*64}))
        with self.assertRaisesRegex(b.BuildError,'packaged game identity'):
            b.build(recipe,self.root/'wrong game rule')

    def test_supplement_exporter_source_binding_and_owned_data_exclusion(self):
        recipe=self.recipe();recipe['building_exporter']=dict(recipe['exporter'])
        other=self.write('other-exporter.zip',zip_bytes({'LICENSE':b'GPL','src/main.cs':b'changed'}))
        recipe['building_exporter'].update(source_zip=str(other),source_sha256=b.digest(other))
        with self.assertRaisesRegex(b.BuildError,'binary designation: building_exporter'):
            b.build(recipe,self.root/'rejected')
        for name in ('building-catalogue.json','building-supplement.json'):
            with self.assertRaises(b.BuildError):b.audit_zip(zip_bytes({name:b'owned game data'}),'source')

    def test_external_loader_mode_never_copies_prerequisite_bytes(self):
        recipe=self.recipe();recipe['loader']['mode']='external';output=self.root/'stage'
        b.build(recipe,output);m=json.loads((output/'release-manifest.json').read_text())
        self.assertEqual(m['install']['loader_files'],[])
        self.assertEqual(len(m['install']['external_prerequisites']),3)
        self.assertFalse((output/'loader').exists())
        for row in m['install']['external_prerequisites']:
            self.assertEqual(row['sha256'],recipe['loader']['files'][row['destination']]['sha256'])
        self.assertTrue((output/'notices/UE4SS-PREREQUISITE.json').is_file())

    def test_missing_payload_and_source_hash_fail_before_output(self):
        recipe=self.recipe();recipe['worker']['source_sha256']='f'*64
        with self.assertRaises(b.BuildError):b.build(recipe,self.root/'never-created')
        self.assertFalse((self.root/'never-created').exists())

    def test_worker_binary_cannot_be_paired_with_other_verified_source(self):
        recipe=self.recipe();recipe['worker']['binary']=self.rec('different-worker.exe',b'MZ-other-worker')
        with self.assertRaisesRegex(b.BuildError,'different executable'):b.build(recipe,self.root/'not-created')
        self.assertFalse((self.root/'not-created').exists())

    def test_worker_source_edit_cannot_reuse_previous_compiled_fingerprint(self):
        recipe=self.recipe();p=Path(recipe['worker']['source_zip'])
        with zipfile.ZipFile(p) as z:files={name:z.read(name) for name in z.namelist()}
        files['src/main.rs']=b'fn main(){println!("changed");}'
        records=json.loads(files['SOURCE-PROVENANCE.json']);next(row for row in records['files'] if row['path']=='src/main.rs')['sha256']=b.sha(files['src/main.rs'])
        files['SOURCE-PROVENANCE.json']=b.encoded(records);p.write_bytes(zip_bytes(files));recipe['worker']['source_sha256']=b.digest(p)
        with self.assertRaisesRegex(b.BuildError,'compiled source fingerprint'):b.build(recipe,self.root/'not-created')

    def test_helper_source_and_exporter_archive_bindings_reject_substitutions(self):
        recipe=self.recipe();self.write('helpers/ipc/src/lib.rs','changed source')
        with self.assertRaisesRegex(b.BuildError,'helper source differs'):b.build(recipe,self.root/'not-created')
        recipe=self.recipe();p=Path(recipe['exporter']['source_zip']);p.write_bytes(zip_bytes({'LICENSE':b'GPL','src/main.cs':b'class Different{}'}))
        recipe['exporter']['source_sha256']=b.digest(p)
        with self.assertRaisesRegex(b.BuildError,'binary designation'):b.build(recipe,self.root/'not-created')

    def test_denylist_traversal_and_case_aliases(self):
        for name in ['../secret','C:/secret','app/../secret','private/owned.glb','map-data/cache.bin','foo.usmap','notices/game.pak','runtime.json','x/oo2core_9_win64.dll','x/oodle-data-shared.dll','app/test_probe.py','host/skate_config.lua']:
            with self.subTest(name=name),self.assertRaises(b.BuildError):b.public_path(name)
        allow=b.Allowlist();allow.add('app/a.py',b'a','x','relative/source')
        with self.assertRaises(b.BuildError):allow.add('APP/A.py',b'b','x','relative/source')
        b.public_path('exporter/OodleSharp.dll');b.public_path('exporter/Oodle.NET.dll')

    def test_private_ascii_utf16_and_archive_paths_are_rejected(self):
        private='C:'+chr(92)+'Users'+chr(92)+'developer'+chr(92)+'Documents'+chr(92)+'project'
        for raw in (private.encode(),private.encode('utf-16le')):
            with self.assertRaises(b.BuildError):b.leak_check(raw,'fixture')
            with self.assertRaises(b.BuildError):b.leak_check(raw,'exporter/NAudio.Core.dll')
        with self.assertRaises(b.BuildError):b.audit_zip(zip_bytes({'docs/readme.md':private.encode()}),'source')
        with self.assertRaises(b.BuildError):b.audit_zip(zip_bytes({'maps/cache.npy':b'game bytes'}),'source')
        with self.assertRaises(b.BuildError):b.audit_zip(zip_bytes({'../escape':b'bad'}),'source')

    def test_manifest_tamper_extra_and_nested_zip_leaks_are_detected(self):
        recipe=self.recipe();output=self.root/'stage';b.build(recipe,output)
        path=output/'artwork/skateboard.png';path.write_bytes(b'tampered')
        with self.assertRaises(b.BuildError):b.audit_package(output)
        path.write_bytes(b'PNG-icon');(output/'foreign.txt').write_text('foreign')
        with self.assertRaises(b.BuildError):b.audit_package(output)
        (output/'foreign.txt').unlink()
        archive=output/'sources/adapter-source.zip';archive.write_bytes(zip_bytes({'secret.usmap':b'private'}))
        m=json.loads((output/'release-manifest.json').read_text())
        row=next(r for r in m['files'] if r['path']=='sources/adapter-source.zip');row.update(bytes=archive.stat().st_size,sha256=b.digest(archive))
        (output/'release-manifest.json').write_bytes(b.encoded(m))
        with self.assertRaises(b.BuildError):b.audit_package(output)

    def test_python_and_lua_closures_reject_unprovided_dynamic_dependencies(self):
        self.write('app/a.py',"import b\nsubprocess.run(['b.py'])\n")
        self.write('app/b.py','import json\n')
        self.assertEqual(b.python_closure(self.root/'app',('a',))['modules'],['a','b'])
        self.write('app/b.py','import unavailable_package\n')
        with self.assertRaises(b.BuildError):b.python_closure(self.root/'app',('a',))
        self.write('app/b.py','__import__(name)\n')
        with self.assertRaises(b.BuildError):b.python_closure(self.root/'app',('a',))
        self.assertEqual(b.lua_requires('-- require("ignored")\nlocal a=pcall(require,"json");require \'bridge\''),['json','bridge'])
        with self.assertRaises(b.BuildError):b.lua_requires('require(prefix.."module")')
        recipe=self.recipe();self.write('lua/leaf.lua',"require('mount_playtest')")
        with self.assertRaises(b.BuildError):b.lua_closure(recipe['lua_root'],recipe['bootstrap'])

    def test_verified_python_bytes_cannot_be_locally_substituted(self):
        recipe=self.recipe();self.write('python/Lib/site-packages/numpy/__init__.py','changed locally')
        with self.assertRaises(b.BuildError):b.add_python(b.Allowlist(),**{'root':recipe['python']['root'],'sources_manifest':recipe['python']['sources'],'downloads':recipe['python']['downloads']})

    @unittest.skipUnless(os.name=='nt','PowerShell helper orchestration is a Windows build path')
    def test_helper_build_preserves_paths_binds_compiler_and_restores_environment(self):
        self.write('Build-Helpers.ps1',b.HELPER_BUILD)
        self.write('SOURCE-CLOSURE.json',b.encoded({'helpers':[{'component':name} for name in ('ipc','render','buildings')]}))
        self.write('rustc-mock.ps1',"Write-Output 'rustc 1.96.0 (fixture)'; $global:LASTEXITCODE=0\n")
        self.write('cargo-mock.ps1',r'''$record=@{args=@($args);rustc=$env:RUSTC;flags=$env:CARGO_ENCODED_RUSTFLAGS;wrapper=$env:RUSTC_WRAPPER}
$record | ConvertTo-Json -Compress -Depth 3 | Add-Content -LiteralPath (Join-Path $PSScriptRoot 'calls.jsonl') -Encoding UTF8
$global:LASTEXITCODE=0
''')
        driver=self.write('driver.ps1',r'''$ErrorActionPreference='Stop'
$env:RUSTC='old compiler';$env:RUSTFLAGS='old flags';$env:RUSTC_WRAPPER='old wrapper'
& (Join-Path $PSScriptRoot 'Build-Helpers.ps1') -OutputDirectory (Join-Path $PSScriptRoot ('out space '+[char]0x96EA)) -CargoExecutable (Join-Path $PSScriptRoot 'cargo-mock.ps1') -RustcExecutable (Join-Path $PSScriptRoot 'rustc-mock.ps1') -Offline
if($env:RUSTC -ne 'old compiler' -or $env:RUSTFLAGS -ne 'old flags' -or $env:RUSTC_WRAPPER -ne 'old wrapper'){throw 'Environment not restored'}
''')
        result=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(driver)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        rows=[json.loads(line) for line in (self.root/'calls.jsonl').read_text(encoding='utf-8-sig').splitlines()]
        self.assertEqual(len(rows),3)
        for row,component in zip(rows,('ipc','render','buildings')):
            args=row['args'];self.assertEqual(args[:3],['build','--release','--locked']);self.assertEqual(args[-1],'--offline')
            self.assertEqual(Path(args[args.index('--manifest-path')+1]),self.root/'helpers'/component/'Cargo.toml')
            self.assertEqual(Path(args[args.index('--target-dir')+1]),self.root/'out space \u96ea'/component)
            self.assertEqual(Path(row['rustc']),self.root/'rustc-mock.ps1');self.assertFalse(row['wrapper'])
            self.assertEqual(len(row['flags'].split(chr(31))),3)


if __name__=='__main__':unittest.main()
