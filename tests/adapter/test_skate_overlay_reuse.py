"""Exact overlay reuse, invalidation and corrupt-cache fallback fixtures."""
import copy,gc,json,tempfile,unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import skate_cache_worker as worker
from skate_map_cache import MapCache,mesh_id
from skate_overlay_reuse import OverlayReuseStore
from test_skate_world_export import fixture


class OverlayReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='overlay-reuse-',dir=Path(__file__).parent)
        self.root=Path(self.temp.name);self.world,_,_=fixture(self.root)
        self.built=self.root/'prepared';self.built.mkdir()
        self.patches=ExitStack()
        self.patches.enter_context(patch.object(worker,'BUILT',self.built))
        self.patches.enter_context(patch.object(worker.cache,'ROOT',self.root))
        self.preparer=worker.WholeWorldPreparer(self.world,require_authored=False)
        self.scene={'world':'FixtureWorld','generation':'first-launch','revision':1,
                    'packages':['/Game/Test/World.World'],'physicsSettings':{'engine_default':1},
                    'objects':[{'name':f'/Game/Test/World.World:PersistentLevel.Box{i}.Mesh',
                                'mesh':'/Game/Test/Box.Box','enabled':3,
                                'transform':{'Translation':{'X':i*100},'Rotation':{'W':1}}}
                               for i in range(2)]}
        self.request={'mode':'whole_world','generation':'first-launch','revision':1,
                      'anchor':[0,0,0],'center':[0,0,0],'world_session':'save-guid-1234'}

    def tearDown(self):
        self.preparer=self.world=None;self.patches.close();gc.collect();self.temp.cleanup()

    def prepare(self):
        result=self.preparer.prepare(self.request,self.scene)
        self.request['revision']+=1;self.scene['revision']+=1
        return result

    def test_restart_reuses_exact_verified_overlay_despite_revision_timing_and_order(self):
        first=self.prepare();self.assertFalse(first['report']['reused_scene_overlay'])
        self.preparer=worker.WholeWorldPreparer(self.world,require_authored=False)
        self.scene['generation']=self.request['generation']='second-launch'
        self.scene['collection_seconds']=999;self.scene['objects'].reverse()
        with patch.object(worker,'export_overlay',side_effect=AssertionError('Unexpected export')):
            second=self.prepare()
        self.assertTrue(second['report']['reused_scene_overlay'])
        self.assertEqual(first['scene_overlay'],second['scene_overlay'])
        self.assertEqual(first['report']['scene_fingerprint'],second['report']['scene_fingerprint'])
        self.assertEqual(second['report']['world_session'],self.request['world_session'])

    def test_moves_removals_collision_and_mesh_state_changes_rebuild(self):
        prior=self.prepare()
        changes=(lambda:self.scene['objects'][0]['transform']['Translation'].update(X=43),
                 lambda:self.scene['objects'][0].update(enabled=0),
                 lambda:self.scene['objects'][0].update(actor_enabled=False),
                 lambda:self.scene['objects'].pop(),
                 lambda:self.scene['objects'].clear())
        for change in changes:
            change();current=self.prepare()
            self.assertFalse(current['report']['reused_scene_overlay'])
            self.assertNotEqual(current['scene_overlay'],prior['scene_overlay']);prior=current
        self.assertEqual(prior['report']['live_instances'],0)

    def test_unloaded_packages_preserve_history_but_explicit_empty_deletes(self):
        original=self.prepare()
        self.scene['packages']=['/Game/Test/Other.World'];self.scene['objects']=[]
        expanded=self.prepare()
        self.assertEqual(expanded['report']['live_instances'],2)
        self.preparer=worker.WholeWorldPreparer(self.world,require_authored=False)
        again=self.prepare();self.assertTrue(again['report']['reused_scene_overlay'])
        self.assertEqual(again['scene_overlay'],expanded['scene_overlay'])
        self.scene['packages']=['/Game/Test/World.World']
        removed=self.prepare()
        self.assertFalse(removed['report']['reused_scene_overlay'])
        self.assertEqual(removed['report']['live_instances'],0)
        self.assertEqual(removed['report']['overridden_packages'],2)

    def test_another_save_cannot_select_saved_overlay(self):
        first=self.prepare();self.request['world_session']='other-save'
        self.preparer=worker.WholeWorldPreparer(self.world,require_authored=False)
        second=self.prepare()
        self.assertFalse(second['report']['reused_scene_overlay'])
        self.assertNotEqual(second['scene_overlay'],first['scene_overlay'])

    def test_payload_and_manifest_corruption_fall_back_to_normal_export(self):
        first=self.prepare();path=Path(first['scene_overlay'])
        with (path.parent/'instances.bin').open('r+b') as stream:stream.write(b'badbytes')
        second=self.prepare();self.assertFalse(second['report']['reused_scene_overlay'])
        path=Path(second['scene_overlay']);manifest=json.loads(path.read_text());manifest['instance_count']+=1
        path.write_text(json.dumps(manifest))
        third=self.prepare();self.assertFalse(third['report']['reused_scene_overlay'])
        fourth=self.prepare();self.assertTrue(fourth['report']['reused_scene_overlay'])

    def test_malformed_and_escaping_pointer_fall_back_without_reading_outside(self):
        self.prepare();pointer=next((self.built/'verified-overlays-v1').glob('*.json'))
        pointer.write_text('{')
        second=self.prepare();self.assertFalse(second['report']['reused_scene_overlay'])
        record=json.loads(pointer.read_text());record['manifest']='../../outside.json';pointer.write_text(json.dumps(record))
        third=self.prepare();self.assertFalse(third['report']['reused_scene_overlay'])

    def test_changed_live_extra_mesh_bytes_invalidates_reuse(self):
        extra='/Game/Test/Added.Added';path=self.root/'static/geometry'/(mesh_id(extra)+'.json')
        entry={'path':extra,'agg':{'BoxElems':[{'X':10,'Y':20,'Z':30,'Center':{},'Rotation':{}}]}}
        path.write_text(json.dumps(entry));self.scene['objects'][0]['mesh']=extra
        first=self.prepare()
        entry['agg']['BoxElems'][0]['X']=99;path.write_text(json.dumps(entry))
        # Fresh helper reads changed geometry; authored base geometry is unchanged.
        self.world=MapCache(self.root)
        self.preparer=worker.WholeWorldPreparer(self.world,require_authored=False)
        second=self.prepare()
        self.assertFalse(second['report']['reused_scene_overlay'])
        self.assertNotEqual(first['report']['scene_fingerprint'],second['report']['scene_fingerprint'])
        self.assertEqual(first['world_manifest'],second['world_manifest'])

    def test_invalid_policy_incomplete_snapshot_and_nonfinite_data_never_reuse(self):
        self.prepare()
        for value in ({'complete':False},{'errors':['probe failed']},{'physicsSettings':{'engine_default':2}}):
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.preparer.prepare(self.request,{**self.scene,**value})
        self.scene['objects'][0]['transform']['Translation']['X']=float('nan')
        with self.assertRaises(ValueError):self.prepare()


if __name__=='__main__':unittest.main()
