import copy,json,unittest
from authored_policy import generate

CONFIG='''[/Script/Engine.CollisionProfile]
+DefaultChannelResponses=(Channel=ECC_GameTraceChannel1,DefaultResponse=ECR_Ignore,bTraceType=True,Name="Footstep")
+DefaultChannelResponses=(Channel=ECC_GameTraceChannel4,DefaultResponse=ECR_Block,bTraceType=False,Name="Player")
+DefaultChannelResponses=(Channel=ECC_GameTraceChannel15,DefaultResponse=ECR_Ignore,bTraceType=True,Name="FeetIK")
+EditProfiles=(Name="BlockAllDynamic",CustomResponses=((Channel="FeetIK")))
'''


def fixture():
    defaults=[2]*32;defaults[8]=defaults[14]=defaults[28]=0
    component=list(defaults);component[14]=component[28]=2
    player=list(defaults);player[4]=0
    def primitive(profile,object_type,responses):
        return {'name':'private-transient-object-name','enabled':3,'profile':profile,'object_type':object_type,
                'responses':[{'channel':i,'response':r} for i,r in enumerate(responses)]}
    return {'schema':'S3MOUNTPROBE1','objects':['unrelated-private-UI-data'],
            'collision':{'components':{'Engine.StaticMeshComponent':primitive('BlockAllDynamic',1,component)},
                         'pawn_capsule':primitive('Player',17,player),'actor':{'name':'private-actor-name','enabled':True},
                         'profiles':[{'name':'BlockAllDynamic','enabled':3,'object_type':'WorldDynamic','responses':[{'channel':'Footstep','response':2}]},
                                     {'name':'Player','enabled':3,'object_type':'Player','responses':[{'channel':'Camera','response':0}]}]}}


class PolicyTests(unittest.TestCase):
    def test_measured_defaults_edits_and_private_data_exclusion(self):
        policy=generate(fixture(),[CONFIG],{'DefaultEngine.ini':'verified-hash'})
        self.assertEqual(policy['query_channel'],17)
        self.assertEqual(policy['default_responses'][8],0)  # Actual reserved-channel default, never assumed Block.
        self.assertEqual(policy['default_responses'][28],0)
        self.assertEqual(policy['profiles']['BlockAllDynamic']['responses'][28],2)
        self.assertNotIn('private',json.dumps(policy))
        changed=fixture();changed['collision']['pawn_capsule']['name']='different-transient-name'
        self.assertEqual(policy,generate(changed,[CONFIG],{'DefaultEngine.ini':'verified-hash'}))

    def test_missing_unknown_and_mismatched_measurements_reject(self):
        broken=fixture();broken['collision']['pawn_capsule']['responses'].pop()
        with self.assertRaisesRegex(ValueError,'all32'):generate(broken,[CONFIG],{})
        broken=fixture();broken['collision']['profiles'][1]['object_type']='UnconfiguredPlayer'
        with self.assertRaisesRegex(ValueError,'Unknown collision channel'):generate(broken,[CONFIG],{})
        broken=fixture();broken['collision']['components']['Engine.StaticMeshComponent']['responses'][14]['response']=0
        with self.assertRaisesRegex(ValueError,'differs'):generate(broken,[CONFIG],{})
        broken=fixture();broken['collision']['profiles'][0]['responses'].append({'channel':'Visibility','response':2})
        with self.assertRaisesRegex(ValueError,'Cannot establish default'):generate(broken,[CONFIG],{})

    def test_named_channels_follow_fname_case_and_missing_object_name_is_explicit_null(self):
        probe=fixture();config=CONFIG+'\n+DefaultChannelResponses=(Channel=ECC_GameTraceChannel3,DefaultResponse=ECR_Block,bTraceType=False,Name="Ai")'
        probe['collision']['profiles'].append({'name':'Example','enabled':1,'object_type':'','responses':[{'channel':'AI','response':'ECR_Ignore'}]})
        policy=generate(probe,[config],{})
        self.assertEqual(policy['channel_names']['AI'],policy['channel_names']['Ai'])
        self.assertIsNone(policy['profiles']['Example']['object_type'])
        self.assertEqual(policy['profiles']['Example']['responses'][16],0)


if __name__=='__main__':unittest.main()
