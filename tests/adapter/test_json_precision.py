"""Lossless building-number output without changing existing JSON protocols."""
from pathlib import Path
import json, math, random, struct, sys, unittest
sys.path.insert(0,str(Path(__file__).parent/'python-deps'))
from lupa import LuaRuntime
ROOT=Path(__file__).parent

class PrecisionTests(unittest.TestCase):
 def setUp(self):
  self.lua=LuaRuntime(unpack_returned_tuples=True)
  self.lua.execute('json=(function()'+(ROOT/'host-probe/json.lua').read_text()+'\nend)()')
 def test_nested_building_descriptor_roundtrip_and_default_isolation(self):
  # First coordinate is the actual raw building translation recovered from
  # accepted revision5; legacy JSON persisted14773.344694136 instead.
  self.lua.execute('''local v={inventory={{id=75,location={14773.344694136491,182413.1288144879,-3048.5339494022906},yaw=210.00000000000003}},settings={value=0.10000000000000002}}
   local before=json.encode(v);local old=json.decode(before);assert(old.inventory[1].location[1]~=v.inventory[1].location[1])
   local restored=json.decode(json.encode_precise(v))
   for i=1,3 do assert(restored.inventory[1].location[i]==v.inventory[1].location[i])end
   assert(restored.inventory[1].yaw==v.inventory[1].yaw and restored.settings.value==v.settings.value)
   assert(json.encode(v)==before)
   local bad={};bad.self=bad;assert(not pcall(json.encode_precise,bad));assert(json.encode(v)==before)
   assert(not pcall(json.encode_precise,0/0)and not pcall(json.encode_precise,math.huge))''')
 def test_finite_f64_bits_survive_lua_and_python_json(self):
  rng=random.Random(41203);values=[5e-324,sys.float_info.max,sys.float_info.min,0.1,-1.0000000000000002]
  while len(values)<300:
   value=struct.unpack('<d',rng.randbytes(8))[0]
   if math.isfinite(value)and value!=0:values.append(value)
  for value in values:
   self.lua.globals().value=value
   encoded=self.lua.eval('json.encode_precise(value)')
   self.assertEqual(struct.pack('<d',json.loads(encoded)),struct.pack('<d',value))
   self.assertTrue(self.lua.eval('json.decode(json.encode_precise(value))==value'))

if __name__=='__main__':unittest.main()
