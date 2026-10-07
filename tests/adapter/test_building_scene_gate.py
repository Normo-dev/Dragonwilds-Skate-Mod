"""Numeric, private fixtures for actual whole-world building preparation."""
from pathlib import Path
import copy,gc,hashlib,json,os,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
import test_skate_building_metadata as metadata_fixtures
from skate_building_scene import BuildingSceneGate,digest
from skate_observed_world import ObservedWorldState
import skate_cache_worker as worker

WORK=Path(__file__).parent

class BuildingSceneTests(unittest.TestCase):
 def setUp(self):
  temp_root=Path(os.environ.get('S3_TEST_TMP',str(WORK)));temp_root.mkdir(parents=True,exist_ok=True)
  self.temp=tempfile.TemporaryDirectory(prefix='building-scene-',dir=temp_root);self.root=Path(self.temp.name)
  self.capture,self.catalogue,self.rule,self.game,self.policy=metadata_fixtures.BuildingMetadataTests().collision_fixture()
  self.capture['world_session']='fixture-save'
  mapping=self.root/'Mappings.usmap';mapping.write_bytes(b'explicit private numeric mapping fixture')
  self.mapping_sha=hashlib.sha256(mapping.read_bytes()).hexdigest();self.catalogue['mapping_sha256']=self.mapping_sha
  self.cat=self.root/'building-catalogue.json';self.cat.write_text(json.dumps(self.catalogue))
  self.rules=self.root/'building-rule.json';self.rules.write_text(json.dumps(self.rule))
  self.request={'world_session':'fixture-save','generation':'fixture','revision':1,'anchor':[0,0,0],'center':[0,0,0]}
  self.scene={'generation':'fixture','revision':1,'world':self.capture['world'],'objects':[],'packages':['/Game/Level.Level'],
      'complete':True,'errors':[],'physicsSettings':{'engine_default':1},
      'query_policy':{'channel':17,'enabled':3,'responses':[2]*32},'building_state':copy.deepcopy(self.capture)}
  self.base='12'*32;self.gate=self.make_gate(self.base)
  self.observed=ObservedWorldState(self.root/'observed')
 def make_gate(self,base):
  return BuildingSceneGate(catalogue_path=self.cat,rule_path=self.rules,game_exe_sha256=self.game,
   catalogue_sha256=hashlib.sha256(self.cat.read_bytes()).hexdigest(),rule_sha256=hashlib.sha256(self.rules.read_bytes()).hexdigest(),
   policy=self.policy,supplement_base_fingerprint=base,mapping_path=self.root/'Mappings.usmap',supplement_mapping_sha256=self.mapping_sha)
 def tearDown(self):
  gc.collect();self.temp.cleanup()
 def stage(self,scene=None):
  prepared,evidence=self.gate.prepare(self.request,scene or self.scene,self.base,observed=self.observed)
  merged,pending=self.observed.preview(prepared,self.request['world_session'],self.base)
  pending,report=self.gate.validate_observed(self.request,merged,pending,evidence)
  return merged,pending,report
 def retained(self):
  scene=copy.deepcopy(self.scene);scene.pop('building_state');scene['building_inventory']='retain_verified';return scene
 def test_initial_verified_then_restart_retained_and_deletion(self):
  merged,pending,report=self.stage();self.assertEqual(report['status'],'fresh_verified');self.assertEqual(report['included_entities'],1)
  self.observed.commit(pending);self.observed=ObservedWorldState(self.root/'observed')
  retained,pending,report=self.stage(self.retained());self.assertEqual(report['status'],'retained_verified')
  self.assertEqual(merged['objects'],retained['objects'])
  self.scene['building_state']['pieces']=[];self.scene['building_state']['inventory_piece_ids']=[99]
  self.scene['building_state']['inventory']=[r for r in self.scene['building_state']['inventory'] if r['id']==99]
  deleted,pending,report=self.stage();self.assertEqual(report['included_entities'],0);self.assertEqual(deleted['objects'],[])
  self.observed.commit(pending);again,_,_=self.stage(self.retained());self.assertEqual(again['objects'],[])
 def test_initial_missing_unverified_wrong_save_and_unowned_namespace_rejected(self):
  with self.assertRaisesRegex(ValueError,'No previously verified'):self.stage(self.retained())
  scene=self.retained();scene.pop('building_inventory')
  with self.assertRaisesRegex(ValueError,'explicit retain_verified'):self.stage(scene)
  for change in ('schema','session','reserved','query'):
   with self.subTest(change=change):
    scene=copy.deepcopy(self.scene)
    if change=='schema':scene['building_state']['schema']='S3BUILDINGSTATE1'
    if change=='session':scene['building_state']['world_session']='different-save'
    if change=='reserved':scene['packages'].append('/SkateRuntime/Buildings/foreign.Foreign')
    if change=='query':scene['query_policy']['channel']=2
    with self.assertRaises(ValueError):self.stage(scene)
  self.assertFalse((self.root/'observed').exists())
 def unload(self,scene=None):
  scene=copy.deepcopy(scene or self.scene);capture=scene['building_state']
  capture['pieces']=[];capture['native_missing_piece_ids']=capture['inventory_piece_ids'][:];return scene
 def test_unloaded_known_piece_retained_but_global_deletion_removes_it(self):
  initial,pending,_=self.stage();self.observed.commit(pending)
  # Restart the preparation process, preserving only the verified disk state.
  self.observed=ObservedWorldState(self.root/'observed')
  unloaded,pending,report=self.stage(self.unload())
  self.assertEqual(unloaded['objects'],initial['objects']);self.assertEqual(report['retained_unloaded_piece_count'],1)
  self.assertEqual(report['unseen_unloaded_piece_count'],0);self.observed.commit(pending)
  deleted=self.unload();capture=deleted['building_state'];capture['inventory_piece_ids']=[99]
  capture['inventory']=[r for r in capture['inventory'] if r['id']==99];capture['native_missing_piece_ids']=[99]
  merged,pending,report=self.stage(deleted);self.assertEqual(merged['objects'],[]);self.assertEqual(report['piece_count'],0)
 def test_first_unseen_unloaded_piece_is_explicitly_uncovered(self):
  merged,pending,report=self.stage(self.unload());self.assertEqual(merged['objects'],[])
  self.assertEqual(report['loaded_piece_count'],0);self.assertEqual(report['unseen_unloaded_piece_count'],1)
  self.assertEqual(report['retained_unloaded_piece_count'],0)
 def test_loaded_disabled_piece_replaces_old_geometry_and_unloaded_changes_invalidate(self):
  for change in ('disabled','moved','manager'):
   with self.subTest(change=change):
    _,pending,_=self.stage();self.observed.commit(pending)
    if change=='disabled':
     update=copy.deepcopy(self.scene);update['building_state']['pieces'][0]['collision_enabled']=False
    else:
     update=self.unload()
     if change=='moved':update['building_state']['inventory'][0]['location']=[100,200,300]
     else:update['building_state']['managers'][0]['owner_collision']=False
    merged,pending,report=self.stage(update);self.assertEqual(merged['objects'],[])
    if change!='disabled':self.assertEqual(report['invalidated_unloaded_piece_count'],1)
 def test_unloaded_evidence_tamper_and_between_preview_change_rejected(self):
  _,pending,_=self.stage();self.observed.commit(pending)
  saved=pending[1];original=saved.read_bytes();state=json.loads(original)
  state['building_collision_evidence']['piece_descriptors']['12']='00'*32
  saved.write_text(json.dumps(state))
  with self.assertRaisesRegex(ValueError,'integrity'):self.stage(self.unload())
  saved.write_bytes(original)
  prepared,evidence=self.gate.prepare(self.request,self.unload(),self.base,observed=self.observed)
  saved.write_bytes(original+b' ')
  merged,pending=self.observed.preview(prepared,self.request['world_session'],self.base)
  with self.assertRaisesRegex(ValueError,'observations changed'):self.gate.validate_observed(self.request,merged,pending,evidence)
 def delta(self):
  scene=copy.deepcopy(self.scene);capture=scene['building_state']
  capture.update(capture_mode='delta',native_piece_ids=[12],retained_loaded_piece_ids=[12],pieces=[])
  return scene
 def test_selective_loaded_retention_requires_prior_matching_evidence(self):
  with self.assertRaisesRegex(ValueError,'no prior native evidence'):self.stage(self.delta())
  initial,pending,_=self.stage();self.observed.commit(pending)
  merged,pending,report=self.stage(self.delta());self.assertEqual(merged['objects'],initial['objects'])
  self.assertEqual(report['loaded_piece_count'],0);self.assertEqual(report['retained_loaded_piece_count'],1)
  self.observed.commit(pending)
  for kind in ('location','ghost','policy'):
   update=self.delta()
   if kind=='location':update['building_state']['inventory'][0]['location']=[1,2,3]
   elif kind=='ghost':update['building_state']['inventory'][0]['ghosted']=True
   else:update['building_state']['managers'][0]['component']['enabled']=0
   with self.assertRaisesRegex(ValueError,'matching prior native evidence'):self.stage(update)
 def test_baseline_is_derived_from_committed_evidence_and_keeps_native_census(self):
  _,pending,_=self.stage();self.observed.commit(pending);self.gate.publish_baseline(pending[2],self.root/'mailbox')
  baseline=json.loads((self.root/'mailbox/building-baseline.json').read_bytes())
  self.assertEqual(baseline['schema'],'S3BUILDINGBASELINE1');self.assertEqual(baseline['native_piece_ids'],[12])
  self.assertEqual(set(baseline['descriptors']),{'12'});self.assertEqual(baseline['game_exe_sha256'],self.game)
 def test_file_capture_identity_and_private_path_resolution(self):
  scene={**self.scene};scene.pop('building_state');scene.update(building_state_file='building-state2-1-2-3.json',building_capture_id='1-2-3')
  capture={**self.capture,'capture_id':'1-2-3'};(self.root/scene['building_state_file']).write_text(json.dumps(capture))
  result=worker.resolve_building_state(scene,self.root);self.assertEqual(result['building_state']['capture_id'],'1-2-3')
  scene['building_capture_id']='1-2-4'
  with self.assertRaisesRegex(ValueError,'replaced'):worker.resolve_building_state(scene,self.root)
  scene['building_state_file']='../building-state2-1-2-3.json'
  with self.assertRaisesRegex(ValueError,'basename'):worker.resolve_building_state(scene,self.root)
 def test_separate_building_exporter_only_for_missing_building_geometry(self):
  from types import SimpleNamespace
  normal='/Game/Rock.Rock';building='/Plugin/Floor.Floor';missing={normal,building};calls=[]
  scene={'objects':[{'mesh':normal},{'mesh':building,'collision_source':'verified_lightweight_building_entity'}]}
  def run(args,**kwargs):
   calls.append(args);missing.difference_update(json.loads(Path(args[-2]).read_bytes()));return SimpleNamespace(returncode=0)
  paths={'map_export':self.root/'base/Map.exe','building_export':self.root/'supplement/Map.exe'}
  with patch.object(worker,'BUILT',self.root),patch.object(worker,'load_paths',return_value=paths),patch.object(worker,'game_paks',return_value=self.root),patch.object(worker,'missing_meshes',side_effect=lambda _:sorted(missing)),patch.object(worker.subprocess,'run',side_effect=run):
   worker.ensure_meshes(scene)
  self.assertEqual([call[0]for call in calls],[str(paths['map_export']),str(paths['building_export'])]);self.assertFalse(missing)
 def test_pinned_inputs_and_base_reject_changes(self):
  with self.assertRaisesRegex(ValueError,'different base'):self.gate.prepare(self.request,self.scene,'13'*32)
  self.cat.write_text(self.cat.read_text()+' ')
  with self.assertRaisesRegex(ValueError,'input changed'):self.stage()
  self.cat.write_text(json.dumps(self.catalogue));self.rule['game_exe_sha256']='14'*32;self.rules.write_text(json.dumps(self.rule))
  with self.assertRaisesRegex(ValueError,'independently pinned game'):self.make_gate(self.base)
 def test_saved_rows_and_provenance_tamper_rejected(self):
  _,pending,_=self.stage();self.observed.commit(pending);saved=pending[1];before=saved.read_bytes()
  for edit in ('row','rule','missing','extra'):
   with self.subTest(edit=edit):
    data=json.loads(before);record=data['building_collision_evidence'];package=record['package']
    if edit=='row':data['packages'][package][0]['transform']['Translation']['X']+=10
    if edit=='rule':record['binding']['rule_sha256']='aa'*32
    if edit=='missing':data.pop('building_collision_evidence')
    if edit=='extra':data['packages']['/SkateRuntime/Buildings/foreign.Foreign']=[]
    saved.write_text(json.dumps(data))
    with self.assertRaises(ValueError):self.stage(self.retained())
 def test_failed_export_does_not_commit_new_inventory(self):
  _,pending,_=self.stage()
  # Until the caller's export succeeds, both rows and evidence remain absent.
  self.assertFalse(pending[1].exists());self.assertEqual(self.observed.loaded,{})
 def test_real_preparer_promotes_before_meshes_exports_and_persists(self):
  from test_skate_world_export import fixture
  from skate_world_export import validate_export
  world,output,_=fixture(self.root)
  geometry=self.root/'static/geometry';mesh='/Game/Floor.Floor'
  (geometry/(worker.cache.mesh_id(mesh)+'.json')).write_text(json.dumps({'path':mesh,'agg':{
      'BoxElems':[{'X':200,'Y':200,'Z':20,'Center':{},'Rotation':{}}]}}))
  built=self.root/'prepared';built.mkdir()
  try:
   with patch.object(worker,'BUILT',built),patch.object(worker.cache,'ROOT',self.root):
    preparer=worker.WholeWorldPreparer(world,require_authored=False)
    preparer.load_base(self.scene);preparer.building_gate=self.make_gate(preparer.base['source_fingerprint'])
    original=worker.ensure_meshes;seen=[]
    def discover(scene):
     self.assertTrue(any(o.get('building_piece_id')==12 for o in scene['objects']));seen.append(True);original(scene)
    with patch.object(worker,'ensure_meshes',side_effect=discover):result=preparer.prepare(self.request,self.scene)
    self.assertEqual(seen,[True]);self.assertEqual(result['report']['building_collision']['status'],'fresh_verified')
    self.assertEqual(preparer.overlays.validate(Path(result['scene_overlay']),preparer.base)['instance_count'],1)
    saved=next((self.root/'observed-worlds').glob('*.json'));self.assertIn('building_collision_evidence',json.loads(saved.read_text()))
    result2=preparer.prepare(self.request,self.retained());self.assertEqual(result2['report']['building_collision']['status'],'retained_verified')
    self.assertTrue(result2['report']['reused_scene_overlay'])
    bad=copy.deepcopy(self.scene);bad['building_state']['schema']='S3BUILDINGSTATE1';prior=saved.read_bytes()
    with patch.object(worker,'ensure_meshes',side_effect=AssertionError('Unverified building must fail before export')):
     with self.assertRaises(ValueError):preparer.prepare(self.request,bad)
    self.assertEqual(saved.read_bytes(),prior)
    preparer=None
  finally:world=None;gc.collect()
if __name__=='__main__':unittest.main()
