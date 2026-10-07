"""Typed Lua/native capture invariants using plain simulated snapshots."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).parent));sys.path.insert(0,str(Path(__file__).parent/'python-deps'))
from lupa import LuaRuntime
from test_building_collision import MOCK,SOURCE
NATIVE=(Path(__file__).parent/'host-probe/building_native_capture.lua').read_text()
EXTRA=r'''
local serial=1000
for _,o in pairs(registry)do
 serial=serial+16;local a=serial;o.GetAddress=function(self)return rawget(self,'replacement_address') or a end
end
local function obj(name,address)
 local o={IsValid=function(self)return not self.deleted end,GetFullName=function()return name end,
 GetAddress=function(self)return self.replacement_address or address end}
 registry[name:gsub('^[^ ]+ ','')]=o;return o
end
derivedClass=obj('Class /Script/Dominion.BuildingPieceDerivedData',0x10000)
massClass=obj('Class /Script/JagexMassEntity.JgxMassSubsystem',0x10100)
derived={obj('BuildingPieceDerivedData /Game/Buildings/Derived7.Derived7',0x20000),obj('BuildingPieceDerivedData /Game/Buildings/Derived9.Derived9',0x20100)}
for _,o in ipairs(derived)do o.IsA=function(_,c)return c==derivedClass end end
mass=obj('JgxMassSubsystem /Game/Test.World:MassSubsystem',0x30000)
mass.IsA=function(_,c)return c==massClass end;mass.GetWorld=function()return mode.foreignMass and otherworld or world end
local findall=FindAllOf
FindAllOf=function(what)
 if what=='BuildingPieceDerivedData'then return derived end
 if what=='JgxMassSubsystem'then return mode.extraMass and {mass,mass} or {mass} end
 return findall(what)
end
nativecalls={ids=0,read=0}
settings.ActiveCollisionProfileName.Name.GetComparisonIndex=function()return 71 end
settings.InactiveCollisionProfileName.Name.GetComparisonIndex=function()return 72 end
package.preload.skate_config=function()return {}end
package.preload.building_native=function()return {
 ids=function(c)
  assert(c==component);nativecalls.ids=nativecalls.ids+1
  if mode.unknownId then return {10,99}end
  if mode.changedIds and nativecalls.ids>1 then return {10}end
  return {10,30}
 end,
 read=function(c,ids)
  assert(c==component and #ids<=16);nativecalls.read=nativecalls.read+1
  local rows={}
  for _,id in ipairs(ids)do
   local asset=id==10 and 2 or 1
   rows[#rows+1]={id=id,data_asset_address=mode.unknownAsset and 0xdead or assets[asset]:GetAddress(),
    derived_asset_address=derived[asset]:GetAddress(),
    transform={Rotation={X=0,Y=0,Z=0,W=1},Translation={X=mode.changedTransform and nativecalls.read or 1,Y=2,Z=3},Scale3D={X=1,Y=1,Z=1}},
    preview=false,ghosted=(id==10 and not mode.ghostMismatch),can_defer_collision_update=true,collision_enabled=true,entity_count=1}
  end
  return rows
 end}end
nativecapture=assert(load(native_source))()
function start_native()
 begin();state=finish();assert(state.complete)
 nativecapture.begin(pawn,state,{clock=clock})
end
function finish_native()
 for i=1,100 do local r=nativecapture.advance(2,0.003);if r then return r end end
 error('Native capture did not finish')
end
'''
class TestCapture(unittest.TestCase):
 def setUp(self):
  self.lua=LuaRuntime(unpack_returned_tuples=True);self.lua.globals().source=SOURCE
  self.lua.globals().native_source=NATIVE;self.lua.execute(MOCK);self.lua.execute(EXTRA)
 def test_actual_rows_remain_unverified(self):
  self.lua.execute('''start_native();local r=finish_native()
   assert(r.complete and r.schema=='S3BUILDINGSTATE1' and not r.collision_verified)
   assert(r.native_capture.complete and not r.native_capture.collision_verified)
   assert(#r.native_pieces==2 and #r.native_piece_ids==2 and #r.native_missing_piece_ids==0)
   assert(r.native_pieces[1].id==10 and r.native_pieces[1].data_index==9)
   assert(r.native_pieces[1].data_asset==assets[2]:GetFullName() and r.native_pieces[1].derived_asset==derived[2]:GetFullName())
   assert(r.native_capture.mass_subsystem.address==mass:GetAddress())
   assert(r.native_capture.profile_names.active.comparison_index==71 and r.native_capture.profile_names.inactive.name=='BuildingPreview')
   assert(nativecalls.ids==3 and nativecalls.read==2)''')
 def test_foreign_or_multiple_mass_rejected(self):
  for mode in ('mode.foreignMass=true','mode.extraMass=true'):
   with self.subTest(mode=mode):self.setUp();self.lua.execute(mode+';assert(not pcall(start_native))')
 def test_unknown_native_id_rejected(self):
  self.lua.execute('mode.unknownId=true;start_native();local r=finish_native();assert(not r.complete)')
 def test_unknown_asset_rejected(self):
  self.lua.execute('mode.unknownAsset=true;start_native();local r=finish_native();assert(not r.complete)')
 def test_changed_native_inventory_rejected(self):
  self.lua.execute('mode.changedIds=true;start_native();local r=finish_native();assert(not r.complete)')
 def test_changed_native_transform_rejected(self):
  self.lua.execute('mode.changedTransform=true;start_native();local r=finish_native();assert(not r.complete)')
 def test_ghost_mismatch_rejected(self):
  self.lua.execute('mode.ghostMismatch=true;start_native();local r=finish_native();assert(not r.complete)')
 def test_same_name_object_replacement_rejected(self):
  for obj in ('pawn','world','global','component'):
   with self.subTest(obj=obj):self.setUp();self.lua.execute(f'start_native();{obj}.replacement_address=999999;local r=finish_native();assert(not r.complete)')
 def test_missing_native_ids_reported_not_promoted(self):
  self.lua.execute('''pieces[50]={ClientVisible={BuildingPieceDataIndex=7,Location={X=0,Y=0,Z=0},Yaw=0,bGhosted=false,StabilityValue=1,FractionalHealth=1}}
   start_native()
   -- Inventory already captured at begin: use a fresh native capture for this fixture.
   nativecapture.cancel();nativecapture.begin(pawn,state,{clock=clock})
   local r=finish_native();assert(r.complete and #r.native_missing_piece_ids==1 and r.native_missing_piece_ids[1]==50)
   assert(not r.collision_verified)''')
 def test_manager_collision_change_rejected(self):
  self.lua.execute('start_native();mode.disabled=true;local r=finish_native();assert(not r.complete)')
 def test_client_inventory_change_rejected(self):
  self.lua.execute('start_native();pieces[99]=pieces[30];local r=finish_native();assert(not r.complete)')
 def test_bounded_cancel(self):
  self.lua.execute('start_native();assert(nativecapture.advance(1)==nil);nativecapture.cancel();assert(nativecapture.advance()==nil)')
if __name__=='__main__':unittest.main()
