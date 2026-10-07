from copy import deepcopy
import math
import unittest
from skate_building_metadata import compose_transform, resolve_metadata, body_collision, promote_scene
from skate_authored_gate import live_included


def transform(location=(0,0,0), yaw=0, scale=(1,1,1)):
    return {'Rotation':dict(zip('XYZW',(0,0,math.sin(math.radians(yaw)/2),math.cos(math.radians(yaw)/2)))),
            'Translation':dict(zip('XYZ',location)), 'Scale3D':dict(zip('XYZ',scale))}


class BuildingMetadataTests(unittest.TestCase):
    def fixture(self):
        data='/Game/Data.Data';derived='/Game/Derived.Derived';sha='ab'*32
        rule={'schema':'S3BUILDINGRULE1','verified':True,'game_exe_sha256':sha,'selected_tag':'BuildingMesh',
              'active_profile':'PlacedFixture','inactive_profile':'PreviewFixture',
              'transform_policy':'entity_local_then_piece_ftransform','evidence':['fixture evidence only']}
        entity={'entity_index':0,'archetype_name':'StaticMeshEntity','mesh':'/Game/Floor.Floor',
                'transform':transform((10,0,2)), 'body':{'CollisionProfileName':'AuthoredPreviewFixture'},
                'component_tags':['BuildingMesh'],'supported_static_mesh_metadata':True,'errors':[]}
        other={**deepcopy(entity),'entity_index':1,'mesh':'/Engine/Plane.Plane','component_tags':['AntiFoliage'],
               'body':{'CollisionProfileName':'NoCollision'}}
        catalogue={'magic':'S3BL1','schema':1,'complete':True,'errors':[],
                   'data':{data:{'derived_path':derived,'derived_available':True,
                       'representation_category':'EBuildingPieceRepresentationCategory::Lightweight'}},
                   'derived':{derived:{'entities':[entity,other],'entity_count':2,'complete':True,'errors':[],
                       'entity_representation_present':True,'usable_static_mesh_metadata':True}}}
        capture={'schema':'S3BUILDINGSTATE2','complete':True,'collision_verified':True,'errors':[],
                 'game_exe_sha256':sha,'settings':{'active_profile':'PlacedFixture','inactive_profile':'PreviewFixture'},
                 'data_assets':[{'index':417,'name':'BuildingPieceData '+data}],
                 'pieces':[{'id':12,'data_index':417,'data_asset':'BuildingPieceData '+data,
                            'derived_asset':derived,'entity_count':2,
                            'collision_enabled':True,'profile_state':'active','transform':transform((100,200,300),90)}]}
        return capture,catalogue,rule,sha

    def collision_fixture(self):
        capture,catalogue,rule,sha=self.fixture()
        capture.update(scope='lightweight_pieces',inventory_piece_ids=[12,99],actor_piece_ids=[99],world='World /Game/Level.Level')
        rule.update(body_policy='captured_native_entity_body_v1',reader_protocol='DWBBDY2',
            geometry_policy='actual_bodysetup_matches_catalogue_mesh')
        capture['data_assets'].append({'index':418,'name':'/Game/Actor.Actor'})
        catalogue['data']['/Game/Actor.Actor']={'representation_category':'EBuildingPieceRepresentationCategory::ManagedActor'}
        capture['inventory']=[{'id':12,'data_index':417},{'id':99,'data_index':418}]
        capture['native_missing_piece_ids']=[99]
        capture['pieces'][0]['entities']=[{'entity_index':i,'collision_verified':True,'physics_present':True,
            'building_mesh':i==0,'actor_enabled':i==0,'created':True,'dirty':False,
            'collision':{'enabled':3 if i==0 else 0,'object_type':27,'responses':[2 if i==0 else 0]*32},
            'geometry_verified':True,'body_mesh':'/Game/Floor.Floor' if i==0 else '/Engine/Plane.Plane',
            'profile_name':'PlacedFixture' if i==0 else 'NoCollision'} for i in range(2)]
        capture['managers']=[{'owner_collision':True,'component':{'enabled':3,'object_type':0,'responses':[2]*32}}]
        policy={'schema':'S3AP1','query_channel':17,'query_responses':[2]*32,'default_responses':[2]*32,
            'channel_names':{'Player':17,'Pawn':2,'Building':27,'ECC_GameTraceChannel14':27,'WorldStatic':0},
            'profiles':{'PlacedFixture':{'enabled':3,'object_type':27,'responses':[2]*32},
                'PreviewFixture':{'enabled':1,'object_type':27,'responses':[0]*32},
                'NoCollision':{'enabled':0,'object_type':None,'responses':[0]*32},
                'AuthoredPreviewFixture':{'enabled':1,'object_type':27,'responses':[0]*32}}}
        selected=next(iter(catalogue['derived'].values()))['entities'][0]
        selected['body'].update(CollisionEnabled='ECollisionEnabled::QueryOnly',
            ObjectType='ECollisionChannel::ECC_GameTraceChannel14',
            CollisionResponses={'ResponseArray':[{'Channel':'Player','Response':'ECollisionResponse::ECR_Ignore'}]})
        return capture,catalogue,rule,sha,policy

    def test_exact_local_then_piece_and_only_verified_tag_gets_override(self):
        capture,catalogue,rule,sha=self.fixture();before=deepcopy(catalogue)
        result=resolve_metadata(capture,catalogue,rule,game_exe_sha256=sha)
        self.assertFalse(result['geometry_promoted'])
        selected,other=result['entities']
        self.assertEqual(selected['profile_override'],'PlacedFixture')
        self.assertIsNone(other['profile_override'])
        self.assertEqual(other['authored_body'],{'CollisionProfileName':'NoCollision'})
        for actual,expected in zip(selected['transform']['Translation'].values(),(100,210,302)):
            self.assertAlmostEqual(actual,expected)
        self.assertEqual(catalogue,before)
        capture['pieces'][0]['profile_state']='inactive';capture['pieces'][0]['collision_enabled']=False
        self.assertEqual(resolve_metadata(capture,catalogue,rule,game_exe_sha256=sha)['entities'][0]['profile_override'],'PreviewFixture')

    def test_current_read_only_capture_and_missing_flag_are_rejected(self):
        capture,catalogue,rule,sha=self.fixture();capture['schema']='S3BUILDINGSTATE1';capture['collision_verified']=False
        with self.assertRaisesRegex(ValueError,'have not been captured'):
            resolve_metadata(capture,catalogue,rule,game_exe_sha256=sha)
        capture,catalogue,rule,sha=self.fixture();del capture['pieces'][0]['collision_enabled']
        with self.assertRaisesRegex(ValueError,'per-piece collision flag'):
            resolve_metadata(capture,catalogue,rule,game_exe_sha256=sha)

    def test_missing_unknown_or_unsupported_metadata_never_becomes_empty_collision(self):
        for change in ('missing','representation','unsupported','index','duplicate','transform','derived_asset','entity_count'):
            with self.subTest(change=change):
                capture,catalogue,rule,sha=self.fixture();derived=next(iter(catalogue['derived'].values()))
                if change=='missing':catalogue['data'].clear()
                elif change=='representation':derived['entity_representation_present']=False
                elif change=='unsupported':derived['entities'][0]['supported_static_mesh_metadata']=False
                elif change=='index':capture['pieces'][0]['data_index']=418
                elif change=='duplicate':capture['pieces'].append(deepcopy(capture['pieces'][0]))
                elif change=='transform':del capture['pieces'][0]['transform']
                elif change=='derived_asset':capture['pieces'][0]['derived_asset']='/Game/Other.Other'
                elif change=='entity_count':capture['pieces'][0]['entity_count']=1
                with self.assertRaises(ValueError):resolve_metadata(capture,catalogue,rule,game_exe_sha256=sha)

    def test_native_sparse_order_and_insertions_preserve_surviving_collision_order(self):
        capture,catalogue,rule,sha,policy=self.collision_fixture()
        def promote():
            return promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,
                world_session='save-fixture',world=capture['world'])
        first=deepcopy(capture['pieces'][0]);second=deepcopy(first);second['id']=42
        second['transform']=transform((200,300,400),30)
        capture['pieces']=[second,first];capture['inventory_piece_ids']=[42,99,12]
        capture['inventory'].append({'id':42,'data_index':417})
        initial=promote()
        self.assertEqual([r['building_piece_id'] for r in initial['objects']],[12,42])
        capture['pieces'].reverse();capture['inventory_piece_ids'].reverse()
        self.assertEqual(promote(),initial)
        inserted=deepcopy(first);inserted['id']=20
        capture['pieces'].insert(0,inserted);capture['inventory_piece_ids'].append(20)
        capture['inventory'].append({'id':20,'data_index':417})
        added=promote()
        self.assertEqual([r for r in added['objects'] if r['building_piece_id']!=20],initial['objects'])
        capture['pieces']=[p for p in capture['pieces'] if p['id']!=12]
        capture['inventory_piece_ids'].remove(12)
        capture['inventory']=[p for p in capture['inventory'] if p['id']!=12]
        removed=promote()
        self.assertEqual([r for r in removed['objects'] if r['building_piece_id']==42],initial['objects'][1:])
        self.assertEqual(removed['packages'],initial['packages'])

    def test_changed_executable_profiles_or_rule_cannot_authorize_resolution(self):
        for change in ('hash','profile','verified','active_disabled'):
            capture,catalogue,rule,sha=self.fixture()
            if change=='hash':rule['game_exe_sha256']='cd'*32
            elif change=='profile':capture['settings']['active_profile']='Other'
            elif change=='verified':rule['verified']=False
            else:capture['pieces'][0]['collision_enabled']=False
            with self.assertRaises(ValueError):resolve_metadata(capture,catalogue,rule,game_exe_sha256=sha)

    def test_transform_rejects_nonfinite_unknown_and_mirrored_state(self):
        for value in (float('nan'),float('inf'),True):
            bad=transform();bad['Translation']['X']=value
            with self.assertRaises(ValueError):compose_transform(transform(),bad)
        for value in (-1,0):
            with self.assertRaisesRegex(ValueError,'native matrix'):compose_transform(transform(),transform(scale=(value,1,1)))
        bad=transform();bad['Rotation']['W']=0
        with self.assertRaisesRegex(ValueError,'not normalized'):compose_transform(transform(),bad)
        with self.assertRaisesRegex(ValueError,'exact'):compose_transform(transform(),{'Rotation':{'W':1}})

    def test_native_profile_replacement_discards_preview_enabled_and_exceptions(self):
        capture,catalogue,rule,sha,policy=self.collision_fixture()
        body=next(iter(catalogue['derived'].values()))['entities'][0]['body']
        body['CollisionEnabled']='ECollisionEnabled::NoCollision'
        result=body_collision(body,'PlacedFixture',policy)
        self.assertTrue(result['included']);self.assertEqual(result['enabled'],3)
        self.assertEqual((result['query_channel'],result['object_type'],result['response_player']),(17,27,2))
        self.assertTrue(live_included(result,policy))
        self.assertFalse(body_collision(body,None,policy)['included'])

    def test_untagged_custom_body_retains_exact_player_pair_and_defaults(self):
        *_,policy=self.collision_fixture()
        body={'CollisionProfileName':'Custom','CollisionEnabled':3,'ObjectType':'ECollisionChannel::ECC_GameTraceChannel14',
            'CollisionResponses':{'ResponseArray':[{'Channel':'Player','Response':'ECollisionResponse::ECR_Ignore'}]}}
        self.assertFalse(body_collision(body,None,policy)['included'])
        body['CollisionResponses']['ResponseArray']=[{'Channel':'Player'}]
        self.assertTrue(body_collision(body,None,policy)['included'])
        policy['query_responses'][27]=1
        self.assertFalse(body_collision(body,None,policy)['included'])

    def test_rotated_floor_rows_are_strict_and_inputs_remain_unchanged(self):
        capture,catalogue,rule,sha,policy=self.collision_fixture();before=deepcopy((capture,catalogue,policy))
        result=promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world='World /Game/Level.Level')
        self.assertEqual(result['piece_count'],1);self.assertEqual(result['actor_piece_count'],1)
        self.assertEqual(result['entity_count'],2);self.assertEqual(result['included_entities'],1)
        row=result['objects'][0];self.assertTrue(live_included(row,policy))
        self.assertEqual(row['profile'],'PlacedFixture')
        for actual,expected in zip(row['transform']['Translation'].values(),(100,210,302)):
            self.assertAlmostEqual(actual,expected)
        self.assertEqual((capture,catalogue,policy),before)

    def test_preview_and_all_deleted_inventory_emit_same_empty_package(self):
        capture,catalogue,rule,sha,policy=self.collision_fixture()
        def promote():return promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world='World /Game/Level.Level')
        original=promote();capture['pieces'][0].update(collision_enabled=False,profile_state='inactive')
        preview=promote();self.assertEqual(preview['objects'],[]);self.assertEqual(preview['packages'],original['packages'])
        capture.update(pieces=[],inventory_piece_ids=[],actor_piece_ids=[],data_assets=[],inventory=[],native_missing_piece_ids=[])
        deleted=promote();self.assertEqual(deleted['objects'],[]);self.assertEqual(deleted['packages'],original['packages'])
        other=promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='other-save',world='World /Game/Level.Level')
        self.assertNotEqual(other['packages'],original['packages'])

    def test_piece_disable_and_manager_pair_apply_to_untagged_bodies_too(self):
        for change in ('piece','owner','enabled','response','reciprocal','missing'):
            with self.subTest(change=change):
                capture,catalogue,rule,sha,policy=self.collision_fixture()
                other=next(iter(catalogue['derived'].values()))['entities'][1]
                other['body']={'CollisionProfileName':'PlacedFixture'}
                native=capture['pieces'][0]['entities'][1]
                native['actor_enabled']=True;native['collision'].update(enabled=3,responses=[2]*32)
                self.assertTrue(body_collision(other['body'],None,policy)['included'])
                manager=capture['managers'][0]
                if change=='piece':capture['pieces'][0].update(collision_enabled=False,profile_state='inactive')
                elif change=='owner':manager['owner_collision']=False
                elif change=='enabled':manager['component']['enabled']=0
                elif change=='response':manager['component']['responses'][17]=1
                elif change=='reciprocal':policy['query_responses'][0]=0
                else:del capture['managers']
                call=lambda:promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world='World /Game/Level.Level')
                if change=='missing':
                    with self.assertRaises(ValueError):call()
                else:self.assertEqual(call()['objects'],[])

    def test_missing_actual_body_and_malformed_policy_fail_closed(self):
        for change in ('missing_body','response_count','bool_enabled','missing_channel','missing_responses','dirty','wrong_mesh','unverified_geometry','tag_mismatch'):
            with self.subTest(change=change):
                capture,catalogue,rule,sha,policy=self.collision_fixture()
                native=capture['pieces'][0]['entities'][0]
                if change=='missing_body':del native['collision']
                elif change=='response_count':native['collision']['responses']=[2]
                elif change=='bool_enabled':native['collision']['enabled']=True
                elif change=='missing_channel':native['collision']['object_type']=None
                elif change=='dirty':native['dirty']=True
                elif change=='wrong_mesh':native['body_mesh']='/Game/Wrong.Wrong'
                elif change=='unverified_geometry':native['geometry_verified']=False
                elif change=='tag_mismatch':native['building_mesh']=False
                else:del policy['query_responses']
                with self.assertRaises(ValueError):
                    promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world='World /Game/Level.Level')

    def test_complete_representation_partition_prevents_missing_or_duplicate_bodies(self):
        for change in ('scope','world','missing_inventory','missing_native','unknown','duplicate','overlap','actor_category','missing_category'):
            with self.subTest(change=change):
                capture,catalogue,rule,sha,policy=self.collision_fixture()
                if change=='scope':capture['scope']='diagnostic'
                elif change=='world':capture['world']='World /Game/Other.Other'
                elif change=='missing_inventory':del capture['inventory_piece_ids']
                elif change=='missing_native':del capture['native_missing_piece_ids']
                elif change=='unknown':capture['inventory_piece_ids'].append(100)
                elif change=='duplicate':capture['inventory_piece_ids'].append(12)
                elif change=='overlap':capture['native_missing_piece_ids'].append(12)
                elif change=='actor_category':next(iter(catalogue['data'].values()))['representation_category']='EBuildingPieceRepresentationCategory::ManagedActor'
                else:del next(iter(catalogue['data'].values()))['representation_category']
                with self.assertRaises(ValueError):
                    promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world='World /Game/Level.Level')

    def test_promoted_rows_load_through_existing_s3o1_and_delete_independently(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        import numpy as np
        from skate_world_export import export_overlay,INSTANCE_DTYPE
        from skate_observed_world import ObservedWorldState
        capture,catalogue,rule,sha,policy=self.collision_fixture()
        def promote():return promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world='World /Game/Level.Level')
        mesh=np.array([[[0,0,0],[100,0,0],[100,100,0]],[[0,0,0],[100,100,0],[0,100,0]]],dtype='<f8')
        meta={'id':'fixture-floor','path':'/Game/Floor.Floor','count':2,'offset':0,'bounds':[[0,0,0],[100,100,0]],'unsupported':0}
        class Cache:
            authored=SimpleNamespace(policy=policy)
            metadata={'authored_policy':{'fixture':True}}
            def live_geometry_id(self,item):return meta['id']
            def _geometry(self,key):return mesh,meta
        cache=Cache();base={'source_fingerprint':'ab'*32,'default_shape_complexity':1,'geometry':[],'packages':['/Game/Level.Level'],
            'completeness':{'authored_policy':cache.metadata['authored_policy']}}
        def scene(rows):return {'world':'World /Game/Level.Level','complete':True,'errors':[],
            'physicsSettings':{'engine_default':1},'query_policy':{'enabled':3,'channel':17,'responses':[2]*32},**rows}
        with tempfile.TemporaryDirectory(prefix='building-promotion-',dir=Path(__file__).parent) as folder:
            folder=Path(folder);state=ObservedWorldState(folder/'observed')
            unrelated={'name':'/Game/Level.Level:Nearby','enabled':0}
            merged,pending=state.preview(scene({'packages':['/Game/Level.Level'],'objects':[unrelated]}),'save-fixture',base['source_fingerprint']);state.commit(pending)
            rows=promote();merged,pending=state.preview(scene(rows),'save-fixture',base['source_fingerprint']);state.commit(pending)
            added=export_overlay(folder/'added',base=base,cache=cache,scene=merged)
            self.assertEqual((added['instance_count'],added['triangle_count']),(1,2))
            instances=np.fromfile(folder/'added/instances.bin',dtype=INSTANCE_DTYPE)
            self.assertTrue(np.allclose(instances['v'][0],[100,210,302]))
            # Exact existing matrix path also preserves this rotated floor.
            world=mesh@instances['r'][0].T+instances['v'][0]
            self.assertTrue(np.allclose(world[0],[[100,210,302],[100,310,302],[0,310,302]]))
            capture.update(pieces=[],inventory_piece_ids=[],actor_piece_ids=[],data_assets=[],inventory=[],native_missing_piece_ids=[])
            merged,pending=state.preview(scene(promote()),'save-fixture',base['source_fingerprint']);state.commit(pending)
            self.assertEqual(merged['objects'],[unrelated])
            deleted=export_overlay(folder/'deleted',base=base,cache=cache,scene=merged)
            self.assertEqual((deleted['instance_count'],deleted['triangle_count']),(0,0))
            self.assertNotEqual(added['source_fingerprint'],deleted['source_fingerprint'])

    def test_unloaded_lightweight_is_distinct_from_actor_and_actual_deletion(self):
        capture,catalogue,rule,sha,policy=self.collision_fixture()
        capture['inventory'].append({'id':50,'data_index':417});capture['inventory_piece_ids'].append(50)
        capture['native_missing_piece_ids'].append(50)
        result=promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world=capture['world'])
        self.assertEqual(result['loaded_piece_ids'],[12]);self.assertEqual(result['unloaded_piece_ids'],[50])
        self.assertEqual(result['actor_piece_ids'],[99]);self.assertTrue(result['requires_retained_unloaded_merge'])
        self.assertEqual(len(result['objects']),1)

    def test_actual_filter_is_authoritative_over_profile_names_and_ghost_guesses(self):
        capture,catalogue,rule,sha,policy=self.collision_fixture()
        policy['profiles']={};piece=capture['pieces'][0];piece.pop('profile_state');piece['ghosted']=True
        call=lambda:promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world=capture['world'])
        self.assertEqual(len(call()['objects']),1)
        piece['entities'][0]['collision']['responses'][17]=1
        self.assertEqual(call()['objects'],[])

    def delta_fixture(self):
        capture,catalogue,rule,sha,policy=self.collision_fixture()
        capture['inventory'] += [{'id':50,'data_index':417},{'id':51,'data_index':417}]
        capture['inventory_piece_ids'] += [50,51]
        capture.update(capture_mode='delta',native_piece_ids=[50,12],
            retained_loaded_piece_ids=[50],native_missing_piece_ids=[99,51])
        return capture,catalogue,rule,sha,policy

    def test_delta_emits_only_captured_bodies_and_explicit_retention_partition(self):
        capture,catalogue,rule,sha,policy=self.delta_fixture();before=deepcopy(capture)
        result=promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world=capture['world'])
        self.assertEqual(result['loaded_piece_ids'],[12])
        self.assertEqual(result['retained_loaded_piece_ids'],[50])
        self.assertEqual(result['native_piece_ids'],[12,50])
        self.assertEqual(result['unloaded_piece_ids'],[51])
        self.assertEqual(result['actor_piece_ids'],[99])
        self.assertEqual([r['building_piece_id'] for r in result['objects']],[12])
        self.assertTrue(result['requires_retained_loaded_merge'])
        self.assertEqual(capture,before)
        # A disabled captured piece explicitly replaces its prior body set with
        # empty rows; retained geometry is never manufactured by this promoter.
        capture['pieces'][0]['collision_enabled']=False
        result=promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world=capture['world'])
        self.assertEqual(result['objects'],[]);self.assertEqual(result['loaded_piece_ids'],[12])

    def test_delta_exact_partition_and_native_category_are_required(self):
        for change in ('missing_native','missing_retained','duplicate','overlap','unaccounted',
                       'not_native','missing_wrong','unknown_native','retained_actor','full_retained','mode'):
            with self.subTest(change=change):
                capture,catalogue,rule,sha,policy=self.delta_fixture()
                if change=='missing_native':del capture['native_piece_ids']
                elif change=='missing_retained':del capture['retained_loaded_piece_ids']
                elif change=='duplicate':capture['retained_loaded_piece_ids'].append(50)
                elif change=='overlap':capture['retained_loaded_piece_ids'].append(12)
                elif change=='unaccounted':capture['retained_loaded_piece_ids']=[]
                elif change=='not_native':capture['native_piece_ids']=[12]
                elif change=='missing_wrong':capture['native_missing_piece_ids'].append(50)
                elif change=='unknown_native':
                    capture['retained_loaded_piece_ids'].append(100);capture['native_piece_ids'].append(100)
                elif change=='retained_actor':
                    capture['retained_loaded_piece_ids'].append(99);capture['native_piece_ids'].append(99)
                    capture['native_missing_piece_ids'].remove(99)
                elif change=='full_retained':capture['capture_mode']='full'
                elif change=='mode':capture['capture_mode']='unknown'
                with self.assertRaises(ValueError):
                    promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world=capture['world'])

    def test_retained_only_delta_requires_current_manager_and_strict_policy(self):
        capture,catalogue,rule,sha,policy=self.delta_fixture()
        capture['pieces']=[];capture['retained_loaded_piece_ids']=[12,50]
        call=lambda:promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world=capture['world'])
        result=call();self.assertEqual(result['objects'],[])
        self.assertEqual(result['retained_loaded_piece_ids'],[12,50])
        capture['managers']=[]
        with self.assertRaisesRegex(ValueError,'manager policy'):call()

    def test_explicit_full_partition_matches_legacy_full_capture(self):
        capture,catalogue,rule,sha,policy=self.collision_fixture()
        call=lambda:promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world=capture['world'])
        legacy=call()
        capture.update(capture_mode='full',native_piece_ids=[12],retained_loaded_piece_ids=[])
        self.assertEqual(call(),legacy)

    def test_accepted_legacy_empty_native_list_needs_complete_two_pass_census(self):
        for mode in (None,'full'):
            capture,catalogue,rule,sha,policy=self.collision_fixture()
            call=lambda:promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world=capture['world'])
            expected=call()
            if mode is not None:capture['capture_mode']=mode
            capture.update(native_piece_ids=[],native_reader={'schema':2,'loaded_piece_count':1,'verified_passes':2})
            self.assertEqual(call(),expected)
        for change in ('no_reader','wrong_count','bool_count','one_pass','schema','missing_complement','delta'):
            with self.subTest(change=change):
                capture,catalogue,rule,sha,policy=self.collision_fixture()
                capture.update(native_piece_ids=[],native_reader={'schema':2,'loaded_piece_count':1,'verified_passes':2})
                if change=='no_reader':del capture['native_reader']
                elif change=='wrong_count':capture['native_reader']['loaded_piece_count']=2
                elif change=='bool_count':capture['native_reader']['loaded_piece_count']=True
                elif change=='one_pass':capture['native_reader']['verified_passes']=1
                elif change=='schema':capture['native_reader']['schema']=1
                elif change=='missing_complement':capture['native_missing_piece_ids']=[]
                elif change=='delta':capture.update(capture_mode='delta',retained_loaded_piece_ids=[])
                with self.assertRaises(ValueError):
                    promote_scene(capture,catalogue,rule,policy,game_exe_sha256=sha,world_session='save-fixture',world=capture['world'])


if __name__=='__main__':unittest.main()
