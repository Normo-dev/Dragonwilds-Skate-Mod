"""Unit checks for zero-tick source support proof and revision race guard."""
import math
import unittest
from skate_relay import check_entry_floor, floor_proof_covers_pose


class EntryFloorTests(unittest.TestCase):
    floor={'point':[3.,4.,5.],'normal':[0.,2.,0.]}

    def query(self, *, floor=None, position=None, normal=None, revision=0):
        calls=[]
        response={'collision_revision':revision,'surface':{
            'position':position if position is not None else [3.,4.,5.],
            'normal':normal if normal is not None else [0.,.8,0.]}}
        def request(command):calls.append(command);return response
        result=check_entry_floor(self.floor if floor is None else floor,request)
        return result,calls

    def test_matching_normalized_support_uses_only_surface(self):
        (matched,detail,response),calls=self.query()
        self.assertTrue(matched)
        self.assertEqual(detail['native_revision'],0)
        self.assertEqual(calls,[{'op':'surface','point':[3.,4.,5.],'above':.1,'below':.1}])
        self.assertTrue(floor_proof_covers_pose(detail,{'collision_revision':0}))

    def test_invalid_host_never_requests_native(self):
        for value in ({}, {'point':[3,4,float('nan')],'normal':[0,1,0]},
                      {'point':[3,4,5],'normal':[0,0,0]},
                      {'point':[3,4,5],'normal':[0,-1,0]},
                      {'point':[3,True,5],'normal':[0,1,0]},
                      {'point':[3,4,5],'normal':[0,float('inf'),0]}):
            with self.subTest(value=value):
                (matched,detail,response),calls=self.query(floor=value)
                self.assertFalse(matched);self.assertEqual(calls,[])

    def test_distance_and_normal_are_both_required(self):
        for kwargs in ({'position':[3,4.051,5]}, {'position':[3.051,4,5]},
                       {'normal':[math.sqrt(1-.93**2),.93,0]},
                       {'normal':[0,-1,0]}, {'position':[3,float('nan'),5]}):
            with self.subTest(kwargs=kwargs):self.assertFalse(self.query(**kwargs)[0][0])
        self.assertTrue(self.query(position=[3,4.049,5],normal=[math.sqrt(1-.95**2),.95,0])[0][0])

    def test_null_surface_or_query_failure_blocks(self):
        self.assertFalse(check_entry_floor(self.floor,lambda _: {'surface':None,'collision_revision':0})[0])
        def failed(_):raise RuntimeError('test failure')
        matched,detail,response=check_entry_floor(self.floor,failed)
        self.assertFalse(matched);self.assertEqual(detail['reason'],'surface_query_failed')

    def test_native_revision_must_be_exact_nonnegative_integer(self):
        for revision in (None,False,0.0,'0',-1):
            self.assertFalse(self.query(revision=revision)[0][0])

    def test_scene_swap_requires_proof_for_pose_revision(self):
        _,old,_=self.query(revision=3)[0]
        self.assertFalse(floor_proof_covers_pose(old,{'collision_revision':4}))
        _,current,_=self.query(revision=4)[0]
        self.assertTrue(floor_proof_covers_pose(current,{'collision_revision':4}))
        _,next_scene,_=self.query(revision=5)[0]
        self.assertFalse(floor_proof_covers_pose(next_scene,{'collision_revision':4}))
        _,missing,_=self.query(position=[3,4.08,5],revision=4)[0]
        self.assertFalse(floor_proof_covers_pose(missing,{'collision_revision':4}))


if __name__=='__main__':unittest.main()
