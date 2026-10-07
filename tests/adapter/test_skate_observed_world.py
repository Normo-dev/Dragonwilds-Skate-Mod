import copy,json,tempfile,unittest
from pathlib import Path
from skate_observed_world import ObservedWorldState
from skate_collision_codec import decode


class ObservationTests(unittest.TestCase):
    def test_unchanged_same_process_and_restart_do_not_rewrite(self):
        with tempfile.TemporaryDirectory(prefix='world-observation-no-write-',dir=Path(__file__).parent) as folder:
            scene={'world':'World','generation':'one','revision':1,'packages':['/Game/Test/A.World'],'objects':[]}
            store=ObservedWorldState(folder)
            _,pending=store.preview(scene,'save-guid','a'*64);self.assertTrue(store.commit(pending))
            path=pending[1];original=path.read_bytes();stamp=store._stamp(path)
            scene.update(generation='two',revision=2)
            for current in (store,ObservedWorldState(folder)):
                merged,pending=current.preview(scene,'save-guid','a'*64)
                self.assertEqual(merged['changed_observed_packages'],[])
                self.assertFalse(current.commit(pending))
                self.assertEqual(path.read_bytes(),original);self.assertEqual(current._stamp(path),stamp)
                self.assertEqual(current.loaded[pending[0]]['revision'],2)

    def test_mutating_input_and_removal_are_committed(self):
        with tempfile.TemporaryDirectory(prefix='world-observation-mutation-',dir=Path(__file__).parent) as folder:
            item={'name':'/Game/Test/A.World:PersistentLevel.Box.Mesh','mesh':'/Game/Test/Box.Box','enabled':3}
            scene={'world':'World','packages':['/Game/Test/A.World'],'objects':[item]}
            store=ObservedWorldState(folder)
            _,pending=store.preview(scene,'save-guid','a'*64);store.commit(pending)
            item['enabled']=0
            merged,pending=store.preview(scene,'save-guid','a'*64)
            self.assertEqual(merged['changed_observed_packages'],['/Game/Test/A.World'])
            self.assertTrue(store.commit(pending))
            saved=json.loads(pending[1].read_text());self.assertEqual(saved['packages']['/Game/Test/A.World'][0]['enabled'],0)
            scene['objects']=[]
            _,pending=store.preview(scene,'save-guid','a'*64);self.assertTrue(store.commit(pending))
            self.assertEqual(json.loads(pending[1].read_text())['packages']['/Game/Test/A.World'],[])

    def test_external_replacement_invalidates_ram_and_keeps_external_packages(self):
        with tempfile.TemporaryDirectory(prefix='world-observation-external-',dir=Path(__file__).parent) as folder:
            scene={'world':'World','packages':['/Game/Test/A.World'],'objects':[]}
            store=ObservedWorldState(folder)
            _,pending=store.preview(scene,'save-guid','a'*64);store.commit(pending)
            path=pending[1];external=json.loads(path.read_text());external['packages']['/Game/Test/External']=[]
            temporary=path.with_suffix('.external');temporary.write_text(json.dumps(external));temporary.replace(path)
            merged,pending=store.preview(scene,'save-guid','a'*64)
            self.assertIn('/Game/Test/External',merged['packages']);self.assertFalse(store.commit(pending))
            path.write_text('{broken')
            with self.assertRaises(ValueError):store.preview(scene,'save-guid','a'*64)

    def test_external_change_between_preview_and_commit_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix='world-observation-race-',dir=Path(__file__).parent) as folder:
            scene={'world':'World','packages':['/Game/Test/A.World'],'objects':[]}
            store=ObservedWorldState(folder)
            _,pending=store.preview(scene,'save-guid','a'*64);store.commit(pending)
            _,pending=store.preview(scene,'save-guid','a'*64)
            path=pending[1];external=json.loads(path.read_text());external['packages']['/Game/Test/External']=[]
            path.write_text(json.dumps(external));expected=path.read_bytes()
            with self.assertRaisesRegex(ValueError,'changed during preparation'):store.commit(pending)
            self.assertEqual(path.read_bytes(),expected)

    def test_cache_upgrade_and_wrong_world_never_reuse_observations(self):
        with tempfile.TemporaryDirectory(prefix='world-version-',dir=Path(__file__).parent) as folder:
            scene={'world':'/Game/Maps/World.World','packages':['/Game/Test/A.World'],'objects':[]}
            store=ObservedWorldState(folder)
            _,pending=store.preview(scene,'save-actual-guid','a'*64);store.commit(pending)
            path=pending[1];original=path.read_bytes()
            store=ObservedWorldState(folder)
            with self.assertRaisesRegex(ValueError,'different map cache version'):
                store.preview(scene,'save-actual-guid','b'*64)
            with self.assertRaisesRegex(ValueError,'identity'):
                store.preview({**scene,'world':'/Game/Other.World'},'save-actual-guid','a'*64)
            self.assertEqual(path.read_bytes(),original)
            old=json.loads(original);old['schema']=1;old.pop('base_fingerprint');path.write_text(json.dumps(old))
            with self.assertRaisesRegex(ValueError,'different map cache version'):
                ObservedWorldState(folder).preview(scene,'save-actual-guid','a'*64)
            with self.assertRaisesRegex(ValueError,'fingerprint'):
                store.preview(scene,'save-actual-guid','unknown')

    def test_unloaded_package_retains_changes_and_explicit_empty_removes(self):
        with tempfile.TemporaryDirectory(prefix='world-observations-',dir=Path(__file__).parent) as folder:
            store=ObservedWorldState(folder)
            item={'name':'/Game/Test/A.World:PersistentLevel.Box.Mesh','mesh':'/Game/Test/Box.Box'}
            scene={'world':'WorldAsset','packages':['/Game/Test/A.World'],'objects':[item]}
            merged,pending=store.preview(scene,'process1:world1','a'*64);store.commit(pending)
            changed={**item,'enabled':0};scene['objects']=[changed]
            merged,pending=store.preview(scene,'process1:world1','a'*64);store.commit(pending)
            # Simulate helper restart and the package unloading from the game.
            store=ObservedWorldState(folder)
            other={'world':'WorldAsset','packages':['/Game/Test/B.World'],'objects':[]}
            merged,pending=store.preview(other,'process1:world1','a'*64);store.commit(pending)
            self.assertEqual(merged['objects'],[changed]);self.assertEqual(len(merged['packages']),2)
            scene['objects']=[];merged,pending=store.preview(scene,'process1:world1','a'*64);store.commit(pending)
            self.assertEqual(merged['objects'],[])
            # Another save/world instance never inherits the remembered removals.
            merged,_=store.preview(other,'process1:world2','a'*64);self.assertEqual(merged['packages'],['/Game/Test/B.World'])
            with self.assertRaisesRegex(ValueError,'incomplete'):store.preview({**scene,'errors':['probe failed']},'process1:world1','a'*64)

    def test_whole_world_descriptor_needs_identity_but_no_radius(self):
        request={'mode':'whole_world','generation':'test','revision':1,'scene_revision':1,'scene_file':'scene-test-1.json',
                 'anchor':[0,0,0],'center':[0,0,0],'heading':0,'world_session':'process-start:world-address'}
        decoded=decode(b'S3C2'+json.dumps(request).encode());self.assertEqual(decoded['mode'],'whole_world')
        request.pop('world_session')
        with self.assertRaisesRegex(ValueError,'world_session'):decode(b'S3C2'+json.dumps(request).encode())


if __name__=='__main__':unittest.main()
