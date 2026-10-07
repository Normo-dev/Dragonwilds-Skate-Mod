from pathlib import Path
import json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).parent/'python-deps'))
from lupa import LuaRuntime
ROOT=Path(__file__).parent

class BaselineTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='building-baseline-',dir=ROOT);self.root=Path(self.temp.name);self.ident='a'*64
  self.lua=LuaRuntime(unpack_returned_tuples=True);self.lua.globals().folder=self.root.as_posix()+'/'
  self.lua.execute('package.preload.json=function()'+(ROOT/'host-probe/json.lua').read_text()+'\nend')
  self.lua.execute('baseline=(function()'+(ROOT/'host-probe/building_baseline.lua').read_text()+'\nend)()')
  self.write()
 def tearDown(self):self.temp.cleanup()
 def write(self,tail=None):
  (self.root/'building-baseline.id').write_bytes((self.ident+'\n').encode())
  header={'schema':'S3BUILDINGBASELINE1','identifier':self.ident,'world_session':'fixture'}
  rows=[{'id':i,'native':i%2==0,'descriptor':{'asset':'/Game/Floor.Floor','location':[i,2,3],'yaw':0,'ghosted':False}}for i in range(100)]
  (self.root/('building-baseline-'+self.ident+'.rows')).write_bytes((json.dumps(header)+'\n'+(tail if tail is not None else ''.join(json.dumps(r)+'\n'for r in rows))).encode())
 def test_row_decode_is_bounded_and_completed_value_cached(self):
  self.lua.execute('''value,loader=baseline.read(folder);assert(value==nil and loader)
   for i=1,100 do assert(loader.advance()==nil)end
   value=loader.advance();assert(value.descriptors['99'].location[1]==99 and #value.native_piece_ids==50)
   local cached,pending=baseline.read(folder);assert(cached==value and pending==nil)''')
 def test_changed_marker_requires_matching_stream(self):
  self.ident='b'*64;(self.root/'building-baseline.id').write_text(self.ident+'\n')
  self.lua.execute('local a,b=baseline.read(folder);assert(a==nil and b==nil)')
 def test_duplicate_malformed_or_oversized_row_discards_hint(self):
  for tail in ('not-json\n','x'*4097+'\n',json.dumps({'id':1,'native':True,'descriptor':{}})+'\n'+json.dumps({'id':1,'native':True,'descriptor':{}})+'\n'):
   with self.subTest(tail=tail[:20]):
    self.write(tail);self.lua.execute('local a,b=baseline.read(folder);assert(a==nil and b);local v;for i=1,5 do v=b.advance();if v then break end end;assert(type(v)=="table"and v.schema==nil)')
if __name__=='__main__':unittest.main()
