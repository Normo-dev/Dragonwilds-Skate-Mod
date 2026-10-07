"""Collision adapter checks only; these do not simulate host gameplay."""
import copy,json,struct,tempfile,unittest
from pathlib import Path
import numpy as np
from skate_map_cache import MapCache,mesh_id,ROOT,geometry,empty_collision_evidence


def transform(x=0,y=0,z=0):
    return {'Translation':{'X':x,'Y':y,'Z':z},'Rotation':{'W':1},'Scale3D':{'X':1,'Y':1,'Z':1}}


class CacheContract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='collision-cache-test-',dir=Path(__file__).parent)
        self.root=Path(self.temp.name)
        assert self.root.resolve().is_relative_to(Path(__file__).resolve().parent)
        (self.root/'static/geometry').mkdir(parents=True)
        self.mesh='/Game/Test/Floor.Floor';self.key=mesh_id(self.mesh)
        self.entry={'path':self.mesh,'agg':{'BoxElems':[{'X':200,'Y':200,'Z':20,'Center':{'Z':-10},'Rotation':{}}]}}
        (self.root/'static/geometry'/f'{self.key}.json').write_text(json.dumps(self.entry))
        self.a='/Game/Test/A.World';self.b='/Game/Test/B.World'
        records=[{'name':f'StaticMeshComponent {self.a}:PersistentLevel.Floor.Mesh','id':self.key,'transform':transform()},
                 {'name':f'StaticMeshComponent {self.b}:PersistentLevel.Floor.Mesh','id':self.key,'transform':transform(500)}]
        (self.root/'static/test.instances.jsonl').write_text('\n'.join(json.dumps(x) for x in records))
        self.cache=MapCache(self.root)

    def tearDown(self):
        # Windows cannot unlink mapped index files until their owners are gone.
        self.cache=None
        import gc;gc.collect()
        self.temp.cleanup()

    def build(self,scene=None,anchor=(0,0,0),center=(0,0,0),radius=1000):
        path=self.root/'area.triangles'
        report=self.cache.build(anchor,path,radius,scene,center)
        raw=path.read_bytes();magic,count=struct.unpack('<4sI',raw[:8])
        self.assertEqual(magic,b'S3T1');self.assertEqual(count,report['total'])
        return report,np.frombuffer(raw[8:],dtype='<f4').reshape(-1,3,3)

    def test_observed_empty_package_removes_only_that_package(self):
        r,_=self.build({'packages':[self.a],'objects':[]});self.assertEqual(r['total'],12)
        r,_=self.build({'packages':[],'objects':[]});self.assertEqual(r['total'],24)
        r,_=self.build(None);self.assertEqual(r['total'],24)

    def test_added_changed_and_deleted_live_instance(self):
        live={'name':f'StaticMeshComponent {self.a}:PersistentLevel.NewFloor.Mesh','mesh':self.mesh,'transform':transform(200),'enabled':3}
        scene={'packages':[self.a],'objects':[live]}
        r,t=self.build(scene);self.assertEqual(r['total'],24);self.assertEqual(r['live_instances'],1)
        live['transform']=transform(5000)
        r,_=self.build(scene);self.assertEqual(r['total'],12);self.assertEqual(r['live_instances'],0)
        scene['objects']=[]
        r,_=self.build(scene);self.assertEqual(r['total'],12)
        r,_=self.build({'packages':[],'objects':[dict(live,transform=transform(200))]})
        self.assertEqual(r['total'],36)

    def test_fixed_anchor_with_moving_center(self):
        r,t=self.build(anchor=(100,20,30),center=(500,0,0),radius=150)
        self.assertEqual(r['total'],12)
        self.assertAlmostEqual(float(t[:,:,0].min()),3.0)
        self.assertAlmostEqual(float(t[:,:,1].max()),-.3,places=6)
        self.assertEqual(r['coverage_min_xy'],[350.0,-150.0])

    def test_unknown_geometry_reported(self):
        scene={'packages':[self.a],'objects':[{'name':f'{self.a}:PersistentLevel.New.Mesh','mesh':'/Game/Unknown.Unknown','transform':transform()}]}
        r,_=self.build(scene);self.assertEqual(r['missing_geometry_count'],1);self.assertFalse(r['complete'])
        key=mesh_id('/Game/Unknown.Unknown')
        (self.root/'static/geometry'/f'{key}.json').write_text(json.dumps(self.entry))
        r,_=self.build(scene);self.assertEqual(r['missing_geometry_count'],0);self.assertEqual(r['total'],24)

    def test_library_extension_preserves_instance_index_geometry(self):
        (self.root/'static/geometry'/('0'*24+'.json')).write_text(json.dumps(self.entry))
        old=self.cache
        self.cache=MapCache(self.root)
        r,_=self.build();self.assertEqual(r['total'],24)
        self.assertNotEqual(old.cache_dir,self.cache.cache_dir)
        # Both generations remain usable concurrently on Windows.
        r=old.build((0,0,0),self.root/'old.triangles',1000);self.assertEqual(r['total'],24)
        del old

    def test_unchanged_area_reuse_and_persistent_index(self):
        self.build();r,_=self.build();self.assertTrue(r['reused_area'])
        cache=MapCache(self.root);self.assertFalse(cache.index_rebuilt)
        self.assertEqual(len(cache.instances),2)
        del cache

    def test_radius_limit_fails_without_reducing_coverage(self):
        self.cache.MAX_TRIANGLES=23
        with self.assertRaisesRegex(ValueError,'Radius was not reduced'):
            self.build()

    def test_authored_visibility_holes_survive_index(self):
        folder=self.root/'test-terrain';folder.mkdir()
        points=np.array([[[x*100,y*100,-50] for x in range(3)] for y in range(3)],dtype='<f4')
        points.tofile(folder/'0000.points');visibility=np.zeros((3,3),dtype='u1');visibility[1,1]=255
        visibility.tofile(folder/'0000.visibility')
        (folder/'terrain.jsonl').write_text(json.dumps({'file':'0000','n':3,'min':[0,0,-50],'max':[200,200,-50],'mirrored':False}))
        self.cache=MapCache(self.root)
        r,_=self.build();self.assertEqual(r['terrain_triangles'],0)

    def test_complex_only_requires_verified_simple_query_policy(self):
        mesh='/Game/Test/ComplexOnly.ComplexOnly';key=mesh_id(mesh)
        entry={'path':mesh,'agg':None,'trace':None,'raw_aggregate_checked':True,'unhandled_geometry':{},
               'cooked':{'success':True,'has_payload':True,'convex_count':0,'triangle_mesh_count':1,'shared':False}}
        (self.root/'static/geometry'/f'{key}.json').write_text(json.dumps(entry))
        scene={'packages':[self.a],'objects':[{'name':f'{self.a}:PersistentLevel.OnlyComplex.Mesh','mesh':mesh,'transform':transform()}]}
        r,_=self.build(scene);self.assertFalse(r['complete']);self.assertEqual(r['unresolved_empty_geometry_instances'],1)
        scene['physicsSettings']={'engine_default':1}
        r,_=self.build(scene);self.assertTrue(r['complete']);self.assertEqual(r['ignored_empty_geometry_instances'],1)

    def test_unhandled_aggregate_is_not_classified_as_empty(self):
        mesh='/Game/Test/LevelSet.LevelSet';key=mesh_id(mesh)
        entry={'path':mesh,'agg':None,'trace':None,'raw_aggregate_checked':True,'unhandled_geometry':{'LevelSetElems':1},
               'cooked':{'success':True,'has_payload':False,'convex_count':0,'triangle_mesh_count':0,'shared':False}}
        (self.root/'static/geometry'/f'{key}.json').write_text(json.dumps(entry))
        scene={'packages':[self.a],'physicsSettings':{'engine_default':1},
               'objects':[{'name':f'{self.a}:PersistentLevel.LevelSet.Mesh','mesh':mesh,'transform':transform()}]}
        r,_=self.build(scene);self.assertFalse(r['complete']);self.assertEqual(r['unsupported_shapes'],1)

    def test_all_disabled_shapes_require_verified_simple_query_policy(self):
        mesh='/Game/Test/Disabled.Disabled';key=mesh_id(mesh)
        entry=copy.deepcopy(self.entry);entry.update(path=mesh,trace=None,raw_aggregate_checked=True,unhandled_geometry={})
        entry['agg']['BoxElems'][0]['CollisionEnabled']=0
        (self.root/'static/geometry'/f'{key}.json').write_text(json.dumps(entry))
        self.assertEqual(empty_collision_evidence(entry)['reason'],'all_simple_shapes_disabled')
        scene={'packages':[self.a],'objects':[{'name':f'{self.a}:PersistentLevel.Disabled.Mesh','mesh':mesh,'transform':transform()}]}
        r,_=self.build(scene);self.assertFalse(r['complete']);self.assertEqual(r['unresolved_empty_geometry_instances'],1)
        scene['physicsSettings']={'engine_default':3}
        r,_=self.build(scene);self.assertFalse(r['complete'])
        scene['physicsSettings']['engine_default']=1
        r,_=self.build(scene);self.assertTrue(r['complete']);self.assertEqual(r['ignored_empty_geometry_instances'],1)
        active=copy.deepcopy(entry);active['agg']['BoxElems'][0]['CollisionEnabled']=3
        self.assertIsNone(empty_collision_evidence(active))
        unknown=copy.deepcopy(entry);unknown['unhandled_geometry']={'LevelSetElems':1}
        self.assertIsNone(empty_collision_evidence(unknown))

    def test_actual_rubble_cooked_faces_preserve_volume(self):
        path=ROOT/'static/geometry/58500C1010634C495E540720.json'
        if not path.exists():self.skipTest('Owned rubble asset not present')
        entry=json.loads(path.read_text());fallback=entry['cooked']['fallbacks']['0']
        p=np.array([[v[k] for k in 'XYZ'] for v in fallback['vertices']]);t=p[np.array(fallback['indices']).reshape(-1,3)]
        t=t-p.mean(axis=0);volume=np.einsum('ij,ij->i',t[:,0],np.cross(t[:,1],t[:,2])).sum()/6
        self.assertGreater(volume,0)
        self.assertAlmostEqual(volume/fallback['volume'],1,places=3)
        recovered,omitted=geometry(entry);self.assertEqual(omitted,0)
        old=copy.deepcopy(entry);old['cooked']=None
        authored,omitted=geometry(old);self.assertEqual(omitted,1)
        self.assertEqual(len(recovered)-len(authored),8)


if __name__=='__main__':unittest.main()
