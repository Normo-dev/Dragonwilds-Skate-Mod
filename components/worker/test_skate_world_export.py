"""Numeric fixtures only; no game files or live input."""
import json,shutil,tempfile,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
from skate_map_cache import MapCache,mesh_id
from skate_world_export import export_world,validate_export,export_overlay


def fixture(root):
    (root/'static/geometry').mkdir(parents=True,exist_ok=True)
    mesh='/Game/Test/Box.Box';key=mesh_id(mesh)
    entry={'path':mesh,'agg':{'BoxElems':[{'X':20,'Y':30,'Z':40,'Center':{'Z':-20},'Rotation':{}}]}}
    (root/'static/geometry'/f'{key}.json').write_text(json.dumps(entry))
    random=np.random.default_rng(934);records=[]
    for i in range(160):
        angle=random.uniform(-3,3);scale=random.uniform(.05,8,size=3);scale[0]*=-1 if i%3==0 else 1
        if i==1:scale[0]=0
        transform={'Translation':dict(zip('XYZ',random.uniform(-1000,1000,size=3))),
                   'Rotation':{'Z':float(np.sin(angle/2)),'W':float(np.cos(angle/2))},'Scale3D':dict(zip('XYZ',scale))}
        records.append({'id':key,'name':f'/Game/Test/World.World:PersistentLevel.Box{i}.Mesh','transform':transform})
    (root/'static/test.instances.jsonl').write_text('\n'.join(json.dumps(r) for r in records))
    folder=root/'test-terrain';folder.mkdir(exist_ok=True);n=18
    points=np.array([[[x*20,y*20,(x+y)%4] for x in range(n)] for y in range(n)],dtype='<f4')
    points.tofile(folder/'0000.points');vis=np.zeros((n,n),dtype='u1');vis[7:9,7:9]=255;vis.tofile(folder/'0000.visibility')
    (folder/'terrain.jsonl').write_text(json.dumps({'name':'TestTerrain','file':'0000','n':n,'min':[0,0,0],'max':[340,340,3],'mirrored':True}))
    cache=MapCache(root);output=root/'export';manifest=export_world(output,cache=cache,physics_settings={'engine_default':1})
    terrain,_=cache.terrain([0,0,0],1e6);objects,_=cache.static([0,0,0],1e6)
    triangles=np.concatenate([terrain,objects]);triangles.astype('<f8').tofile(root/'expected.f64')
    assert len(triangles)==manifest['terrain_triangle_count']+manifest['static_triangle_count']
    return cache,output,triangles


class WorldExportTests(unittest.TestCase):
    def test_production_route_requires_authored_player_collision_evidence(self):
        import skate_cache_worker as worker
        with tempfile.TemporaryDirectory(prefix='s3w1-policy-',dir=Path(__file__).parent) as directory:
            cache,_,_=fixture(Path(directory));preparer=worker.WholeWorldPreparer(cache)
            with self.assertRaisesRegex(ValueError,'authored-policy.json'):
                preparer.load_base({'physicsSettings':{'engine_default':1}})
            preparer=cache=None
            import gc;gc.collect()

    def test_new_live_mesh_preserves_base_after_restart_but_authored_change_invalidates(self):
        import skate_cache_worker as worker
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix='s3w1-extra-',dir=Path(__file__).parent) as directory:
            root=Path(directory);original,_,_=fixture(root);built=root/'prepared';built.mkdir()
            with patch.object(worker,'BUILT',built),patch.object(worker.cache,'ROOT',root):
                first=worker.WholeWorldPreparer(original,require_authored=False)
                request={'mode':'whole_world','generation':'fixture','revision':1,'anchor':[0,0,0],
                         'center':[0,0,0],'world_session':'save-same-guid'}
                scene={'world':'FixtureWorld','generation':'fixture','revision':1,
                       'packages':['/Game/Test/World.World'],'objects':[],'physicsSettings':{'engine_default':1}}
                initial=first.prepare(request,scene)
                # Force the new mesh to sort before the base mesh, changing current index IDs.
                for index in range(1000):
                    mesh=f'/Game/Test/Extra{index}.Extra{index}'
                    if mesh_id(mesh)<mesh_id('/Game/Test/Box.Box'):break
                added={'path':mesh,'agg':{'BoxElems':[{'X':10,'Y':20,'Z':30,'Center':{},'Rotation':{}},
                                                     {'X':20,'Y':30,'Z':40,'Center':{},'Rotation':{}}]}}
                (root/'static/geometry'/(mesh_id(mesh)+'.json')).write_text(json.dumps(added))
                updated=MapCache(root);second=worker.WholeWorldPreparer(updated,require_authored=False)
                request['revision']=2;scene['revision']=2
                scene['objects']=[{'name':'/Game/Test/World.World:PersistentLevel.New.Mesh','mesh':mesh,
                                   'enabled':3,'transform':{'Translation':{},'Rotation':{'W':1},'Scale3D':{'X':1,'Y':1,'Z':1}}}]
                current=second.prepare(request,scene)
                self.assertEqual(current['world_manifest'],initial['world_manifest'])
                self.assertEqual(current['report']['source_fingerprint'],initial['report']['source_fingerprint'])
                self.assertEqual(current['report']['removed_offline_triangles'],1920)
                self.assertEqual(current['report']['live_triangles'],24)
                self.assertEqual(current['report']['total'],584)
                request['revision']=3;scene['revision']=3;scene['collection_seconds']=123.5
                repeated=second.prepare(request,scene)
                self.assertEqual(repeated['report']['scene_fingerprint'],current['report']['scene_fingerprint'])
                # A changed mesh actually used by the authored map gets a new base.
                path=root/'static/geometry'/(mesh_id('/Game/Test/Box.Box')+'.json')
                value=json.loads(path.read_text());value['agg']['BoxElems'][0]['X']=50;path.write_text(json.dumps(value))
                changed=MapCache(root);third=worker.WholeWorldPreparer(changed,require_authored=False)
                request['revision']=4;scene['revision']=4
                with self.assertRaisesRegex(ValueError,'different map cache version'):third.prepare(request,scene)
                first=second=third=original=updated=changed=None
            import gc;gc.collect()

    def test_whole_world_preparer_has_no_area_radius_and_remembers_unloads(self):
        import skate_cache_worker as worker
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix='s3w1-route-',dir=Path(__file__).parent) as directory:
            root=Path(directory);cache,_,_=fixture(root);built=root/'prepared';built.mkdir()
            with patch.object(worker,'BUILT',built),patch.object(worker.cache,'ROOT',root):
                preparer=worker.WholeWorldPreparer(cache,require_authored=False)
                request={'mode':'whole_world','generation':'fixture','revision':1,'anchor':[0,0,0],'center':[0,0,0],'world_session':'fixture-world-session'}
                scene={'world':'FixtureWorld','generation':'fixture','revision':1,'packages':['/Game/Test/World.World'],
                       'objects':[],'physicsSettings':{'engine_default':1}}
                result=preparer.prepare(request,scene)
                self.assertEqual(result['report']['total'],560);self.assertEqual(result['report']['removed_offline_triangles'],1920)
                self.assertTrue(Path(result['world_manifest']).exists());self.assertTrue(Path(result['scene_overlay']).exists())
                # A later observation of another package retains the earlier deletion.
                request['revision']=2;scene['revision']=2;scene['packages']=['/Game/Test/Other.World']
                result=preparer.prepare(request,scene);self.assertEqual(result['report']['total'],560)
                self.assertEqual(result['report']['overridden_packages'],2)
                preparer=None
            cache=None
            import gc;gc.collect()

    def test_portable_complete_order_and_file_integrity(self):
        with tempfile.TemporaryDirectory(prefix='s3w1-test-',dir=Path(__file__).parent) as directory:
            root=Path(directory);cache,output,triangles=fixture(root)
            manifest=validate_export(output/'manifest.json');self.assertEqual(manifest['instance_count'],160)
            self.assertEqual(manifest['terrain_triangle_count'],560)
            geometry=np.fromfile(output/'geometry.f64',dtype='<f8').reshape(-1,3,3)
            np.testing.assert_array_equal(geometry,cache.local_triangles)
            self.assertEqual(manifest,export_world(output,cache=cache,physics_settings={'engine_default':1}))
            with (output/'geometry.f64').open('r+b') as f:f.seek(0);f.write(b'badbytes')
            with self.assertRaisesRegex(ValueError,'checksum'):validate_export(output/'manifest.json')
            cache=None
            import gc;gc.collect()

    def test_unknown_project_policy_is_not_assumed(self):
        with tempfile.TemporaryDirectory(prefix='s3w1-test-',dir=Path(__file__).parent) as directory:
            root=Path(directory);cache,_,_=fixture(root)
            with self.assertRaisesRegex(ValueError,'Verified engine'):export_world(root/'other',cache=cache)
            cache=None
            import gc;gc.collect()


if __name__=='__main__':
    import sys
    if '--fixture' in sys.argv:
        root=Path(__file__).parent/'compact-world-fixture';root.mkdir(exist_ok=True)
        if (root/'export/manifest.json').exists():cache=MapCache(root);output=root/'export'
        else:cache,output,_=fixture(root)
        base=validate_export(output/'manifest.json')
        original_scene={'generation':'fixture-noop','revision':1,'packages':['/Game/Test/World.World'],
                        'physicsSettings':{'engine_default':1},'objects':[]}
        for line in (root/'static/test.instances.jsonl').read_text().splitlines():
            item=json.loads(line);item['mesh']='/Game/Test/Box.Box';item['enabled']=3
            original_scene['objects'].append(item)
        export_overlay(root/'overlay-noop',base=base,cache=cache,scene=original_scene)
        key=mesh_id('/Game/Test/NewBox.NewBox');entry={'path':'/Game/Test/NewBox.NewBox','agg':{'BoxElems':[{'X':50,'Y':20,'Z':30,'Center':{},'Rotation':{}}]}}
        (root/'static/geometry'/f'{key}.json').write_text(json.dumps(entry))
        scene={'generation':'fixture','revision':1,'packages':['/Game/Test/World.World'],'physicsSettings':{'engine_default':1},'objects':[]}
        for i,mesh in enumerate(['/Game/Test/Box.Box','/Game/Test/NewBox.NewBox']):
            scene['objects'].append({'name':f'/Game/Test/World.World:PersistentLevel.Live{i}.Mesh','mesh':mesh,'enabled':3,
                                    'transform':{'Translation':{'X':5000+i*1000,'Y':5000,'Z':100},'Rotation':{'W':1},'Scale3D':{'X':1,'Y':1,'Z':1}}})
        export_overlay(root/'overlay-add',base=base,cache=cache,scene=scene)
        import copy
        two=copy.deepcopy(scene);two['packages'].append('/Game/Test/AddedPackage.World')
        extra={'name':'/Game/Test/AddedPackage.World:PersistentLevel.Live.Mesh','mesh':'/Game/Test/Box.Box','enabled':3,
               'transform':{'Translation':{'X':10000,'Y':5000,'Z':100},'Rotation':{'W':1},'Scale3D':{'X':1,'Y':1,'Z':1}}}
        # Insert ahead of existing live rows to shift their ordinals without changing them.
        two['objects'].insert(0,extra);export_overlay(root/'overlay-two',base=base,cache=cache,scene=two)
        reordered=copy.deepcopy(two);reordered['objects']=[two['objects'][1],two['objects'][0],two['objects'][2]]
        export_overlay(root/'overlay-two-reordered',base=base,cache=cache,scene=reordered)
        extra['transform']['Translation']['X']=20000
        export_overlay(root/'overlay-two-moved',base=base,cache=cache,scene=two)
        scene['objects']=[];scene['revision']=2;export_overlay(root/'overlay-delete',base=base,cache=cache,scene=scene)
        scene['objects']=[{'name':'/Game/Test/World.World:PersistentLevel.Disabled.Mesh','mesh':'/Game/Missing.NoCollision','enabled':0}]
        export_overlay(root/'overlay-disabled',base=base,cache=cache,scene=scene)
        print(output/'manifest.json')
    else:unittest.main()
