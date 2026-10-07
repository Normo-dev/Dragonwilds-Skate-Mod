"""Warm cache validation and readiness-gate regression tests; no game/input."""
from pathlib import Path
import copy,hashlib,json,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from skate_warm_world import WarmWorldStore,initialize_world,prewarm_world,digest


class WarmWorldTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='warm-world-',dir=Path(__file__).parent)
        self.root=Path(self.temp.name);self.worker=self.root/'worker.exe';self.worker.write_bytes(b'verified-worker')
        self.base=self.root/'base.json';self.base.write_text(json.dumps({'source_fingerprint':'b'*64}))
        self.store=WarmWorldStore(self.root,self.worker)
        self.descriptor={'mode':'whole_world','world_session':'save-one','revision':2,'generation':'current'}
        self.old=self.make_overlay('old',1);self.current=self.make_overlay('current',2)
        self.cached=self.response(self.current);self.store.remember(self.descriptor,self.response(self.old))
        self.command={'op':'init_world','manifest':str(self.base),'scene_file':str(self.current),'assets':'private','anchor':[0,0,0]}
        self.rails=self.root/'world.lips';self.rails.write_bytes(b'fixture-rails')
        self.warmup={**self.descriptor,'world':'World /Game/World','center':[100,200,300],
                     'anchor':[0,0,0],'heading':.5}

    def tearDown(self):self.temp.cleanup()

    def make_overlay(self,name,byte):
        folder=self.root/'prepared'/name;folder.mkdir(parents=True)
        records=[]
        for file in ['geometry.f64','instances.bin']:
            p=folder/file;p.write_bytes(bytes([byte]))
            records.append({'path':file,'bytes':1,'sha256':digest(p)})
        value={'magic':'S3O1','schema':1,'completeness':{'complete':True},'base_fingerprint':'b'*64,
               'packages':['world'],'instance_packages':['world'],'geometry':[],'files':records,
               'geometry_file':records[0],'instance_file':records[1],'default_shape_complexity':1}
        identity={'base':value['base_fingerprint'],**{k:value[k] for k in ['packages','instance_packages','geometry','files','default_shape_complexity']}}
        value['source_fingerprint']=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        p=folder/'manifest.json';p.write_text(json.dumps(value));return p

    def response(self,path):
        return {'world_manifest':str(self.base),'scene_overlay':str(path),'report':{
            'world_session':'save-one','world':'World /Game/World','source_fingerprint':'b'*64,
            'scene_fingerprint':json.loads(path.read_text())['source_fingerprint']}}

    def checkpoint(self,path=None):
        scene=json.loads((path or self.current).read_text())['source_fingerprint']
        folder=self.root/'prepared'/'accepted-scene-cache';folder.mkdir(exist_ok=True)
        marker=folder/(scene+'.json')
        marker.write_text(json.dumps({'schema':1,'base_fingerprint':'b'*64,'scene_fingerprint':scene,
            'algorithm':'a'*64,'payload':'d'*64,'identity':'e'*64,'candidates':1,'lips':1,'merged_file':'f'*64+'.rails'}))
        declaration={'schema':1,'durable':True,'format':'accepted_incremental_v1',
                     'scene_fingerprint':scene,'checkpoint_file':str(marker)}
        return marker,{'ok':True,'collision_revision':2,'world':{'scene_checkpoint':declaration}}

    def test_valid_snapshot(self):self.assertEqual(self.store.select(self.descriptor,self.cached),self.old)

    def test_other_save_or_world_is_not_used(self):
        for changes in [{'world_session':'save-two'},{'world':'World /Game/Other'},{'source_fingerprint':'c'*64}]:
            cached=copy.deepcopy(self.cached);cached['report'].update(changes)
            descriptor={**self.descriptor,'world_session':cached['report']['world_session']}
            self.assertIsNone(self.store.select(descriptor,cached))

    def test_changed_worker_or_base_is_not_used(self):
        self.worker.write_bytes(b'new-worker')
        self.assertIsNone(WarmWorldStore(self.root,self.worker).select(self.descriptor,self.cached))
        self.base.write_text('{"changed":true}')
        self.assertIsNone(self.store.select(self.descriptor,self.cached))

    def test_corrupt_manifest_or_payload_rejected(self):
        payload=self.old.parent/'instances.bin';payload.write_bytes(b'corruption')
        self.assertIsNone(self.store.select(self.descriptor,self.cached))
        payload.write_bytes(bytes([1]));self.old.write_text(self.old.read_text()+' ')
        self.assertIsNone(self.store.select(self.descriptor,self.cached))

    def test_pointer_escape_rejected(self):
        p=self.store._path(self.store._identity(self.descriptor,self.cached));value=json.loads(p.read_text())
        value['scene_overlay']='../escape/manifest.json';p.write_text(json.dumps(value))
        self.assertIsNone(self.store.select(self.descriptor,self.cached))

    def test_malformed_json_pointer_shapes_leave_fresh_path_available(self):
        pointer=self.store._path(self.store._identity(self.descriptor,self.cached))
        for value in [[],None,1,'not-an-object']:
            with self.subTest(value=value):
                pointer.write_text(json.dumps(value))
                self.assertIsNone(self.store.select(self.descriptor,self.cached))

    def test_malformed_json_overlay_shape_leaves_fresh_path_available(self):
        pointer=self.store._path(self.store._identity(self.descriptor,self.cached))
        saved=json.loads(pointer.read_text());self.old.write_text('[]')
        saved['scene_manifest_sha256']=digest(self.old);pointer.write_text(json.dumps(saved))
        self.assertIsNone(self.store.select(self.descriptor,self.cached))

    def test_malformed_overlay_completeness_leaves_fresh_path_available(self):
        pointer=self.store._path(self.store._identity(self.descriptor,self.cached))
        saved=json.loads(pointer.read_text());overlay=json.loads(self.old.read_text())
        overlay['completeness']=[];self.old.write_text(json.dumps(overlay))
        saved['scene_manifest_sha256']=digest(self.old);pointer.write_text(json.dumps(saved))
        self.assertIsNone(self.store.select(self.descriptor,self.cached))

    def test_content_fingerprint_rejected(self):
        value=json.loads(self.old.read_text());value['packages']=['different'];self.old.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'content identity'):self.store.remember(self.descriptor,self.response(self.old))

    def test_checkpoint_promotes_exact_scene_and_survives_new_store(self):
        marker,response=self.checkpoint()
        self.assertTrue(self.store.promote(self.descriptor,self.cached,response))
        restarted=WarmWorldStore(self.root,self.worker)
        self.assertEqual(restarted.select(self.descriptor,self.cached),self.current)
        pointer=json.loads(restarted._path(restarted._identity(self.descriptor,self.cached)).read_text())
        self.assertEqual(pointer['accepted_checkpoint']['sha256'],digest(marker))

    def test_wrong_revision_or_missing_checkpoint_does_not_promote(self):
        _,response=self.checkpoint()
        for revision in [None,0,1,3]:
            with self.subTest(revision=revision):
                altered={**response,'collision_revision':revision}
                self.assertFalse(self.store.promote(self.descriptor,self.cached,altered))
                self.assertEqual(self.store.select(self.descriptor,self.cached),self.old)
        self.assertFalse(self.store.promote(self.descriptor,self.cached,{'collision_revision':2,'world':{}}))

    def test_invalid_checkpoint_declaration_never_overwrites_pointer(self):
        for change in [{'schema':2},{'durable':False},{'durable':1},{'durable':None},
                       {'format':'pristine_base'},{'scene_fingerprint':'0'*64}]:
            with self.subTest(change=change):
                _,response=self.checkpoint();response['world']['scene_checkpoint'].update(change)
                with self.assertRaises(ValueError):self.store.promote(self.descriptor,self.cached,response)
                self.assertEqual(self.store.select(self.descriptor,self.cached),self.old)

    def test_invalid_checkpoint_marker_never_overwrites_pointer(self):
        for change in [{'schema':2},{'base_fingerprint':'a'*64},{'scene_fingerprint':'c'*64}]:
            with self.subTest(change=change):
                marker,response=self.checkpoint();value=json.loads(marker.read_text());value.update(change)
                marker.write_text(json.dumps(value))
                with self.assertRaises(ValueError):self.store.promote(self.descriptor,self.cached,response)
                self.assertEqual(self.store.select(self.descriptor,self.cached),self.old)

    def test_checkpoint_location_escape_rejected_even_with_matching_contents(self):
        marker,response=self.checkpoint();escaped=self.root/'prepared'/'outside-marker.json'
        escaped.write_bytes(marker.read_bytes());response['world']['scene_checkpoint']['checkpoint_file']=str(escaped)
        with self.assertRaisesRegex(ValueError,'escapes'):self.store.promote(self.descriptor,self.cached,response)
        self.assertEqual(self.store.select(self.descriptor,self.cached),self.old)

    def test_saved_checkpoint_checksum_and_location_tamper_rejected(self):
        marker,response=self.checkpoint();self.store.promote(self.descriptor,self.cached,response)
        original=marker.read_bytes();marker.write_bytes(original+b' ')
        self.assertIsNone(self.store.select(self.descriptor,self.cached))
        marker.write_bytes(original)
        pointer=self.store._path(self.store._identity(self.descriptor,self.cached));value=json.loads(pointer.read_text())
        outside=self.root/'elsewhere.json';outside.write_bytes(original)
        value['accepted_checkpoint']['path']='elsewhere.json';pointer.write_text(json.dumps(value))
        self.assertIsNone(self.store.select(self.descriptor,self.cached))

    def test_saved_marker_identity_tamper_rejected_even_with_updated_checksum(self):
        marker,response=self.checkpoint();self.store.promote(self.descriptor,self.cached,response)
        value=json.loads(marker.read_text());value['base_fingerprint']='0'*64;marker.write_text(json.dumps(value))
        pointer=self.store._path(self.store._identity(self.descriptor,self.cached));saved=json.loads(pointer.read_text())
        saved['accepted_checkpoint']['sha256']=digest(marker);pointer.write_text(json.dumps(saved))
        self.assertIsNone(self.store.select(self.descriptor,self.cached))

    def test_malformed_json_checkpoint_shape_leaves_fresh_path_available(self):
        marker,response=self.checkpoint();self.store.promote(self.descriptor,self.cached,response)
        marker.write_text('[]')
        pointer=self.store._path(self.store._identity(self.descriptor,self.cached));saved=json.loads(pointer.read_text())
        saved['accepted_checkpoint']['sha256']=digest(marker);pointer.write_text(json.dumps(saved))
        self.assertIsNone(self.store.select(self.descriptor,self.cached))

    def test_overlay_payload_escape_rejected(self):
        outside=self.root/'prepared'/'foreign.bin';outside.write_bytes(b'F')
        value=json.loads(self.old.read_text());record=value['files'][0]
        record.update(path='../foreign.bin',sha256=digest(outside));value['geometry_file']=record
        self.old.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'payload escapes'):
            self.store.remember(self.descriptor,self.response(self.old))

    def test_prewarm_only_issues_cache_required_init_and_declares_no_readiness(self):
        calls=[]
        def request(command):calls.append(command);return {'ok':True,'period':.016,'tick':0}
        preloaded=prewarm_world(request,self.warmup,self.store,self.base,self.rails,'private')
        self.assertEqual([c['op'] for c in calls],['init_world'])
        command=calls[0]
        self.assertIs(command['scene_cache_required'],True)
        self.assertEqual(command['scene_file'],str(self.old));self.assertEqual(command['spawn'],[1,3.25,-2])
        self.assertEqual(command['rail_file'],str(self.rails));self.assertEqual(command['assets'],'private')
        self.assertEqual(preloaded['generation'],'current');self.assertNotIn('ready',preloaded)
        self.assertEqual(self.store.select(self.descriptor,self.cached),self.old)

    def test_prewarm_invalid_metadata_or_unknown_world_does_no_native_work(self):
        bad=[None,{}, {**self.warmup,'mode':'persistent'}]
        for key,value in [('generation',''),('world_session',''),('world',''),('center',[0,True,0]),
                          ('anchor',[0,0,float('inf')]),('center',[0,0,1e9]),('heading',float('nan'))]:
            bad.append({**self.warmup,key:value})
        bad.append({**self.warmup,'world':'World /Other'})
        calls=[]
        for value in bad:
            with self.subTest(value=value):
                self.assertIsNone(prewarm_world(lambda c:calls.append(c),value,self.store,self.base,self.rails,'private'))
        self.assertEqual(calls,[])

    @patch('skate_warm_world.time.sleep')
    def test_exact_prewarm_reused_only_after_fresh_scene_accepted(self,_sleep):
        preload_calls=[]
        result={'ok':True,'period':.016,'tick':0}
        preloaded=prewarm_world(lambda c:preload_calls.append(c)or result,
            self.warmup,self.store,self.base,self.rails,'private')
        calls=[];_,accepted=self.checkpoint()
        responses=iter([{'ok':True,'collision_revision':1},accepted])
        def request(command):
            calls.append(command)
            return next(responses) if command['op']=='pose' else {'ok':True}
        observed=initialize_world(request,self.command,self.descriptor,self.cached,self.store,preloaded=preloaded)
        self.assertEqual(observed['period'],result['period'])
        self.assertEqual(observed['world'],accepted['world'])
        self.assertEqual(observed['collision_revision'],2)
        self.assertEqual([c['op'] for c in calls],['world_scene','pose','pose'])
        self.assertEqual(calls[0]['scene_file'],str(self.current))
        self.assertEqual(self.store.select(self.descriptor,self.cached),self.current)

    @patch('skate_warm_world.time.sleep')
    def test_prewarm_identity_mismatches_require_new_init(self,_sleep):
        original=prewarm_world(lambda c:{'ok':True,'origin':'preloaded'},self.warmup,
            self.store,self.base,self.rails,'private')
        alterations=[{'generation':'other'},{'assets':'other-assets'},{'anchor':[1,0,0]},{'scene_file':str(self.current)}]
        for key in ['world_session','world','base_fingerprint','base_manifest','base_manifest_sha256','worker_sha256']:
            identity=copy.deepcopy(original['identity']);identity[key]='changed';alterations.append({'identity':identity})
        for change in alterations:
            with self.subTest(change=change):
                calls=[]
                def request(command):
                    calls.append(command)
                    return {'ok':True,'collision_revision':2,'origin':'new-init'}
                result=initialize_world(request,self.command,self.descriptor,self.cached,self.store,
                    preloaded={**original,**change})
                self.assertEqual([c['op'] for c in calls],['init_world','world_scene','pose'])
                self.assertEqual(result['origin'],'new-init');self.assertIs(calls[0]['scene_cache_required'],True)

    def test_prewarm_native_cache_rejection_remains_an_optimization_failure(self):
        calls=[]
        def request(command):calls.append(command);raise RuntimeError('No exact durable rails')
        with self.assertRaisesRegex(RuntimeError,'No exact durable rails'):
            prewarm_world(request,self.warmup,self.store,self.base,self.rails,'private')
        self.assertEqual(len(calls),1);self.assertIs(calls[0]['scene_cache_required'],True)
        self.assertEqual(self.store.select(self.descriptor,self.cached),self.old)

    @patch('skate_warm_world.time.sleep')
    def test_warm_start_waits_for_fresh_revision_without_physics(self,_sleep):
        calls=[];polls=[];progress=[]
        def request(command):
            calls.append(command)
            if command['op']=='pose':
                polls.append(1);return {'ok':True,'collision_revision':2 if len(polls)==3 else 0}
            return {'ok':True,'period':.016}
        result=initialize_world(request,self.command,self.descriptor,self.cached,self.store,progress=lambda:progress.append(1))
        self.assertEqual([c['op'] for c in calls],['init_world','world_scene','pose','pose','pose'])
        self.assertEqual(calls[0]['scene_file'],str(self.old));self.assertIs(calls[0]['scene_cache_required'],True)
        self.assertEqual(calls[1]['scene_file'],str(self.current));self.assertEqual(len(progress),2)
        self.assertEqual(result['period'],.016)
        self.assertEqual(self.store.select(self.descriptor,self.cached),self.old)

    def test_native_cache_rejection_falls_back_to_current(self):
        calls=[]
        def request(command):
            calls.append(command)
            if command.get('scene_cache_required'):raise RuntimeError('missing exact rail cache')
            return {'ok':True}
        initialize_world(request,self.command,self.descriptor,self.cached,self.store)
        self.assertEqual(len(calls),2);self.assertEqual(calls[1],self.command)
        self.assertEqual(self.store.select(self.descriptor,self.cached),self.current)

    def test_failed_fresh_collision_never_returns_ready(self):
        calls=[]
        def request(command):
            calls.append(command['op'])
            return {'ok':True,'collision_error_revision':2,'collision_error':'invalid current geometry'} if command['op']=='pose' else {'ok':True}
        with self.assertRaisesRegex(RuntimeError,'invalid current geometry'):
            initialize_world(request,self.command,self.descriptor,self.cached,self.store)
        self.assertEqual(calls,['init_world','world_scene','pose'])
        self.assertEqual(self.store.select(self.descriptor,self.cached),self.old)

    def test_stop_during_warm_update(self):
        calls=[]
        def request(command):calls.append(command['op']);return {'ok':True}
        with self.assertRaisesRegex(RuntimeError,'Stopped'):
            initialize_world(request,self.command,self.descriptor,self.cached,self.store,stopped=lambda:True)
        self.assertEqual(calls,['init_world','world_scene'])

    def test_persistence_can_be_deferred_without_changing_init_readiness(self):
        _,accepted=self.checkpoint()
        with patch.object(self.store,'promote',side_effect=AssertionError('Save belongs in the checkpoint child')):
            result=initialize_world(lambda command:accepted,self.command,self.descriptor,self.cached,self.store,persist=False)
        self.assertEqual(result['collision_revision'],2)
        with patch.object(self.store,'select',return_value=None), \
             patch.object(self.store,'remember',side_effect=AssertionError('Save belongs in the checkpoint child')):
            result=initialize_world(lambda command:{'ok':True,'collision_revision':0},self.command,
                                    self.descriptor,self.cached,self.store,persist=False)
        self.assertEqual(result['collision_revision'],0)


if __name__=='__main__':unittest.main()
