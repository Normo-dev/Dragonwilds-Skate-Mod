"""Supplement transactions use only small fake packages/games; no game input."""
import contextlib,copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import skate_building_setup as s
from skate_preflight import digest,map_preparation_identity,PreflightError
from skate_building_scene import BuildingSceneGate
from skate_process import blocking_processes

class BuildingSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='building setup fixture ')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.game=self.root/'steamapps/common/fake game';self.package=self.game/'DragonwildsSkate'
        self.app=self.package/'app';self.map=self.game/'DragonwildsSkateData/map-data'
        self.app.mkdir(parents=True);self.map.mkdir(parents=True)
        self.paths={'root':self.app,'game_root':self.game,'map_data':self.map,
                    'map_export':self.package/'exporter/DragonwildsMapExport.exe',
                    'building_export':self.package/'building-exporter/DragonwildsMapExport.exe',
                    'building_reader_library':self.package/'bin/skate_buildings.dll',
                    'building_rule':self.package/'config/building-collision-rule.json'}
        game=self.write(self.game,'RSDragonwilds/Binaries/Win64/RSDragonwilds-Win64-Shipping.exe',b'fake game')
        loader=self.write(self.game,'RSDragonwilds/Binaries/Win64/ue4ss/UE4SS.dll',b'fake loader')
        (self.game/'RSDragonwilds/Content/Paks').mkdir(parents=True)
        self.write(self.game.parent.parent,'appmanifest_1374490.acf',b'"buildid" "123"')
        self.compat={'steam_build':'123','game_exe_sha256':digest(game),'ue4ss_sha256':digest(loader)}
        for name in ('authored_policy','skate_authored_gate','skate_map_cache','skate_spline_collision','skate_world_export'):
            self.write(self.app,name+'.py',b'# inert identity fixture')
        for key in ('map_export','building_export','building_reader_library'):
            self.write(self.package,self.paths[key].relative_to(self.package),('inert '+key).encode())
        self.dep=self.write(self.package,'building-exporter/Parser.dll',b'inert parser')
        inventory={'schema':1,'files':[self.record(p,self.paths['building_export'].parent) for p in sorted(self.paths['building_export'].parent.iterdir())]}
        self.write(self.package,s.INVENTORY,inventory)
        self.rule={'schema':'S3BUILDINGRULE1','verified':True,'game_exe_sha256':self.compat['game_exe_sha256'],
                   'selected_tag':'BuildingMesh','active_profile':'PlacedFixture','inactive_profile':'PreviewFixture',
                   'transform_policy':'entity_local_then_piece_ftransform','evidence':['fixture only']}
        self.write(self.package,'config/building-collision-rule.json',self.rule)
        self.refresh_manifest()
        mapping=self.write(self.map,'Mappings.usmap',b'fake owned mappings')
        self.mapping_sha=digest(mapping);self.fingerprint='ab'*32
        geometry=self.write(self.map,'world/geometry.bin',bytes(72))
        instances=self.write(self.map,'world/instances.bin',bytes(152))
        gr=self.record(geometry,geometry.parent);ir=self.record(instances,instances.parent)
        world={'magic':'S3W1','schema':1,'completeness':{'complete':True},'source_fingerprint':self.fingerprint,
               'files':[gr,ir],'geometry_file':gr,'geometry_triangle_count':1,'instance_file':ir,
               'instance_count':1,'instance_stride':152,'terrain':[]}
        self.write(self.map,'world/manifest.json',world)
        rail=self.write(self.map,'world/world.lips',b'keep old original grind cache')
        prep=map_preparation_identity(self.app,self.paths,self.compat)
        self.write(self.map,'compact-world-base.json',{'schema':1,'manifest':'world/manifest.json',
            'source_fingerprint':self.fingerprint,'preparation':prep,'grind_file':dict(self.record(rail,self.map),worker_sha256='ef'*32)})
        self.write(self.map,'setup-state.json',{'ready':True,'completed':['terrain','complete-world-index'],
            'identity':{'schema':1,'product':'DragonwildsSkateSetup','target':str(self.map),
                        'compatibility':self.compat,'mapping_sha256':self.mapping_sha,'preparation':prep}})
        self.catalogue={'magic':'S3BL1','schema':1,'complete':True,'errors':[],'mapping_sha256':self.mapping_sha,
                        'data':{'/Game/Floor.Floor':{}},'derived':{'/Game/Derived.Derived':{}}}
        self.before={p:p.read_bytes() for p in self.map.rglob('*') if p.is_file()}
        self.calls=[]
        self.maintenance=patch.object(s,'offline_maintenance',lambda _:contextlib.nullcontext())
        self.maintenance.start();self.addCleanup(self.maintenance.stop)
    @staticmethod
    def write(root,name,value):
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(value if isinstance(value,bytes) else json.dumps(value).encode());return p
    @staticmethod
    def record(path,root):return {'path':path.relative_to(root).as_posix(),'bytes':path.stat().st_size,'sha256':digest(path)}
    def refresh_manifest(self):
        files=[self.record(p,self.package) for p in sorted(self.package.rglob('*')) if p.is_file() and p.name!='release-manifest.json']
        self.manifest={'schema':1,'product':'DragonwildsSkate','compatibility':self.compat,'files':files,
                       'install':{'runtime':{k:v.relative_to(self.package).as_posix() for k,v in self.paths.items() if k in ('map_export','building_export','building_reader_library','building_rule')}}}
        self.write(self.package,'release-manifest.json',self.manifest)
    def runner(self,exe,args,log):
        self.calls.append((exe,args))
        self.assertEqual(exe,self.paths['building_export']);self.assertEqual(args[-1],'buildings')
        self.assertEqual(args[2],self.map/'Mappings.usmap')
        self.assertNotEqual(args[1],self.map)
        self.write(args[1],'building-catalogue.json',self.catalogue)
    def prepare(self):return s.prepare_buildings(self.paths,self.manifest,runner=self.runner)
    def untouched(self):
        for p,raw in self.before.items():self.assertEqual(p.read_bytes(),raw,str(p))
    def verify(self):return s.verify_building_supplement(self.paths,self.manifest,self.fingerprint)

    def test_prepare_once_reuses_and_preserves_base_grind_and_setup_identity(self):
        self.assertEqual(self.prepare()['status'],'prepared');self.untouched()
        pins=self.verify();self.assertEqual(pins['supplement_base_fingerprint'],self.fingerprint)
        self.assertEqual(pins['rule_sha256'],digest(self.paths['building_rule']))
        self.assertEqual(pins['game_exe_sha256'],self.compat['game_exe_sha256'])
        BuildingSceneGate(**pins,policy={})
        self.assertEqual(self.prepare()['status'],'already_prepared')
        self.assertEqual(len(self.calls),1);self.untouched()

    def test_export_failure_never_publishes_and_retry_does_not_rebuild_map(self):
        def failed(*_):raise RuntimeError('injected exporter failure')
        with self.assertRaisesRegex(RuntimeError,'injected'):
            s.prepare_buildings(self.paths,self.manifest,runner=failed)
        self.assertFalse((self.map/s.MARKER).exists());self.untouched()
        self.prepare();self.verify();self.untouched()

    def test_wrong_mapping_or_incomplete_export_is_not_published(self):
        for change in ({'mapping_sha256':'cc'*32},{'complete':False},{'errors':['missing data']},{'data':{}}):
            with self.subTest(change=change):
                previous=copy.deepcopy(self.catalogue);self.catalogue.update(change)
                with self.assertRaises(PreflightError):self.prepare()
                self.catalogue=previous;self.assertFalse((self.map/s.MARKER).exists());self.untouched()

    def test_mapping_changed_during_export_is_not_published(self):
        def changed(exe,args,log):self.runner(exe,args,log);(self.map/'Mappings.usmap').write_bytes(b'changed')
        with self.assertRaisesRegex(PreflightError,'mappings changed'):
            s.prepare_buildings(self.paths,self.manifest,runner=changed)
        self.assertFalse((self.map/s.MARKER).exists())
        self.assertEqual((self.map/'compact-world-base.json').read_bytes(),self.before[self.map/'compact-world-base.json'])

    def test_exact_exporter_dependencies_and_no_extra_files(self):
        self.dep.write_bytes(b'tampered parser')
        with self.assertRaisesRegex(PreflightError,'dependency is missing or changed'):self.prepare()
        self.dep.write_bytes(b'inert parser');extra=self.write(self.dep.parent,'unlisted.dll',b'extra')
        with self.assertRaisesRegex(PreflightError,'additional files'):self.prepare()
        extra.unlink();self.assertEqual(self.calls,[]);self.untouched()

    def test_runtime_override_and_base_exporter_replacement_are_rejected(self):
        changed={**self.paths,'building_export':self.paths['map_export']}
        with self.assertRaisesRegex(PreflightError,'outside its release pin'):
            s.prepare_buildings(changed,self.manifest,runner=self.runner)
        self.assertEqual(self.calls,[]);self.untouched()

    def test_rule_requires_manifest_pin_and_matching_verified_game(self):
        for change in ({'verified':False},{'game_exe_sha256':'00'*32},{'transform_policy':'invented'},{'evidence':[]}):
            with self.subTest(change=change):
                self.write(self.package,'config/building-collision-rule.json',{**self.rule,**change});self.refresh_manifest()
                with self.assertRaisesRegex(PreflightError,'rule is not verified'):self.prepare()
        self.assertEqual(self.calls,[]);self.untouched()

    def test_missing_supplement_requests_only_building_setup(self):
        with self.assertRaisesRegex(PreflightError,'prepare-buildings offline'):self.verify()
        self.untouched()

    def test_every_marker_binding_is_checked(self):
        self.prepare();file=self.map/s.MARKER;original=file.read_bytes();marker=json.loads(original)
        for field in ('base_fingerprint','game_exe_sha256','mapping_sha256','exporter_inventory_sha256','rule_sha256'):
            self.write(self.map,s.MARKER,{**marker,field:'00'*32})
            with self.subTest(field=field),self.assertRaisesRegex(PreflightError,'different inputs'):self.verify()
        file.write_bytes(original);self.verify()

    def test_catalogue_tamper_or_escape_is_rejected(self):
        self.prepare();pins=self.verify();pins['catalogue_path'].write_bytes(b'tampered')
        with self.assertRaisesRegex(PreflightError,'catalogue is missing or changed'):self.verify()
        marker=json.loads((self.map/s.MARKER).read_text());marker['catalogue']['path']='../foreign.json'
        self.write(self.map,s.MARKER,marker)
        with self.assertRaisesRegex(PreflightError,'content-addressed path'):self.verify()

    def test_verified_supplement_can_be_imported_without_exporting(self):
        self.prepare();source=self.root/'owned supplement';source.mkdir()
        marker=json.loads((self.map/s.MARKER).read_text())
        self.write(source,s.MARKER,marker)
        self.write(source,marker['catalogue']['path'],(self.map/marker['catalogue']['path']).read_bytes())
        (self.map/s.MARKER).unlink()
        result=s.prepare_buildings(self.paths,self.manifest,source=source,runner=lambda *_:self.fail('import exported again'))
        self.assertEqual(result['status'],'prepared');self.verify();self.untouched()

    def test_raw_or_mismatched_import_is_rejected(self):
        source=self.root/'foreign';source.mkdir();self.write(source,'building-catalogue.json',self.catalogue)
        with self.assertRaisesRegex(PreflightError,'Imported verified'):
            s.prepare_buildings(self.paths,self.manifest,source=source,runner=self.runner)
        self.write(source,s.MARKER,{'schema':1,'product':s.PRODUCT})
        with self.assertRaisesRegex(PreflightError,'different pinned inputs'):
            s.prepare_buildings(self.paths,self.manifest,source=source,runner=self.runner)
        self.assertFalse((self.map/s.MARKER).exists());self.untouched()

    def test_existing_unknown_marker_is_preserved(self):
        self.write(self.map,s.MARKER,{'product':'someone else'})
        with self.assertRaisesRegex(PreflightError,'unknown building supplement'):self.prepare()
        self.assertEqual(json.loads((self.map/s.MARKER).read_text()),{'product':'someone else'})
        self.untouched()

    def test_failed_refresh_preserves_published_marker(self):
        self.prepare();before=(self.map/s.MARKER).read_bytes()
        self.rule['evidence'].append('new verified rule');self.write(self.package,'config/building-collision-rule.json',self.rule);self.refresh_manifest()
        with self.assertRaisesRegex(RuntimeError,'failed'):
            s.prepare_buildings(self.paths,self.manifest,runner=lambda *_:(_ for _ in ()).throw(RuntimeError('failed')))
        self.assertEqual((self.map/s.MARKER).read_bytes(),before);self.untouched()

    def test_supplement_exporter_is_in_offline_process_guard(self):
        exe=self.paths['building_export']
        result=blocking_processes(self.paths,images=[(456,exe.name,str(exe))])
        self.assertEqual(result,[{'pid':456,'name':exe.name,'identity_readable':True}])

if __name__=='__main__':unittest.main()
