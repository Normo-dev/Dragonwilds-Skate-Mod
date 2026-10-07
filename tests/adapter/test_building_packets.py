"""Exact binary capture decoding and mailbox isolation, with no game access."""
from pathlib import Path
import copy,struct,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).parent))
from skate_building_packets import ROW,ENTITY,HEADER,decode_state_packets

class PacketTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='building-packets-',dir=Path(__file__).parent);self.root=Path(self.temp.name)
  self.path=self.root/'building-packets-1-2-3.bin'
  self.capture={'schema':'S3BUILDINGSTATE2','complete':True,'collision_verified':True,'pieces':[],
   'data_assets':[{'address':100,'index':7,'name':'BuildingPieceData /Game/Data.Data'}],
   'derived_assets':[{'address':200,'name':'BuildingPieceDerivedData /Game/Derived.Derived'}],
   'body_bindings':[{'address':300,'mesh':'StaticMesh /Game/Floor.Floor','body':'BodySetup /Game/Floor.Floor:BodySetup'}],
   'native_reader':{'schema':2,'verified_passes':2,'manager_address':400,'mass_address':500,'loaded_piece_count':1}}
  self.write()
 def tearDown(self):self.temp.cleanup()
 def packet(self,*,ident=12,enabled=3,body=300,dirty=0,owner=400,response=2,ei=0):
  row=ROW.pack(ident,100,200,0,0,0,1,1,2,3,1,1,1,0,0,0,1,1)
  entity=ENTITY.pack(ei,0xffffffffff,1,1,1,1,dirty,3,enabled,27,*([response]*32),77,body,owner,ident,0,0)
  return HEADER.pack(b'DWBBDY2',1,400,500,1)+row+entity
 def write(self,**kwargs):
  packet=self.packet(**kwargs);raw=b'S3BPK1'+struct.pack('<I',len(packet))+packet;self.path.write_bytes(raw)
  self.capture['piece_packets']={'schema':'S3BUILDINGPACKETS1','path':self.path.name,'bytes':len(raw),'packet_count':1,'piece_count':1}
 def test_exact_transform_policy_identity_and_unsigned_ids(self):
  self.write(ident=0xf1234567);r=decode_state_packets(self.capture,self.root)
  piece=r['pieces'][0];self.assertEqual(piece['id'],0xf1234567)
  self.assertEqual(piece['transform']['Translation'],{'X':1.,'Y':2.,'Z':3.})
  self.assertEqual(piece['entities'][0]['body_index'],0xf1234567)
  self.assertTrue(piece['entities'][0]['geometry_verified']);self.assertEqual(piece['entities'][0]['handle'],str(0xffffffffff))
  self.assertEqual(piece['data_index'],7);self.assertEqual(len(r['piece_packets']['sha256']),64)
  self.assertEqual(self.capture['pieces'],[])
 def test_disabled_body_needs_no_geometry_but_keeps_actual_policy(self):
  self.write(enabled=0,body=0);self.capture['body_bindings']=[]
  entity=decode_state_packets(self.capture,self.root)['pieces'][0]['entities'][0]
  self.assertEqual(entity['collision']['enabled'],0);self.assertNotIn('geometry_verified',entity)
 def test_dirty_foreign_invalid_response_or_ordinal_rejected(self):
  for values in ({'dirty':1},{'owner':999},{'response':3},{'ei':1},{'body':999}):
   with self.subTest(values=values):
    self.write(**values)
    with self.assertRaises(ValueError):decode_state_packets(self.capture,self.root)
 def test_counts_lengths_and_conflicting_rows_rejected(self):
  for change in ('bytes','packet_count','piece_count','loaded','trailing','truncated','pieces'):
   with self.subTest(change=change):
    self.write();capture=copy.deepcopy(self.capture)
    if change in ('bytes','packet_count','piece_count'):capture['piece_packets'][change]+=1
    elif change=='loaded':capture['native_reader']['loaded_piece_count']=2
    elif change=='pieces':capture['pieces']=[{'id':12}]
    elif change=='trailing':self.path.write_bytes(self.path.read_bytes()+b'x');capture['piece_packets']['bytes']+=1
    else:self.path.write_bytes(self.path.read_bytes()[:-1]);capture['piece_packets']['bytes']-=1
    with self.assertRaises(ValueError):decode_state_packets(capture,self.root)
 def test_path_escape_and_missing_typed_binding_rejected(self):
  for name in ('../building-packets-1-2-3.bin','C:/foreign.bin','other.bin','sub/building-packets-1-2-3.bin'):
   capture=copy.deepcopy(self.capture);capture['piece_packets']['path']=name
   with self.assertRaises(ValueError):decode_state_packets(capture,self.root)
  for key in ('data_assets','derived_assets','body_bindings'):
   capture=copy.deepcopy(self.capture);capture[key]=[]
   with self.assertRaises(ValueError):decode_state_packets(capture,self.root)
 def test_empty_complete_inventory_supported(self):
  self.path.write_bytes(b'S3BPK1');self.capture['piece_packets'].update(bytes=6,packet_count=0,piece_count=0)
  self.capture['native_reader']['loaded_piece_count']=0
  self.assertEqual(decode_state_packets(self.capture,self.root)['pieces'],[])
if __name__=='__main__':unittest.main()
