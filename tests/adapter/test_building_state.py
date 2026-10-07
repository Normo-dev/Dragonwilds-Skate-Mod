"""Actual STATE2 capture contract; all objects and native packets are fixtures."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).parent));sys.path.insert(0,str(Path(__file__).parent/'python-deps'))
from lupa import LuaRuntime
from test_building_collision import MOCK,SOURCE
from test_building_native_capture import EXTRA,NATIVE
STATE=(Path(__file__).parent/'host-probe/building_state.lua').read_text()
FAST=r'''
local function object(n,a)
 local o={IsValid=function(self)return not self.deleted end,GetFullName=function()return n end,
 GetAddress=function(self)return self.replacement_address or a end}
 registry[n:gsub('^[^ ]+ ','')]=o;return o
end
meshClass=object('Class /Script/Engine.StaticMesh',0x40000)
bodyClass=object('Class /Script/Engine.BodySetup',0x41000)
meshClass.ForEachProperty=function(_,cb)
 cb({GetFName=function()return {ToString=function()return 'BodySetup'end}end,
 GetClass=function()return {GetFName=function()return {ToString=function()return 'ObjectProperty'end}end}end,
 GetPropertyClass=mode.objectAccessorMissing and nil or function()return bodyClass end})
end
body=object('BodySetup /Game/Floor.Floor:BodySetup',0x50000);body.IsA=function(_,c)return not mode.wrongBodyClass and c==bodyClass end
mesh=object('StaticMesh /Game/Floor.Floor',0x60000);mesh.IsA=function(_,c)return c==meshClass end;mesh.BodySetup=body
local findall=FindAllOf
FindAllOf=function(what)
 if what=='StaticMesh'then counters.mesh_discoveries=(counters.mesh_discoveries or 0)+1;return {mesh}end
 assert(what~='BuildingHISMC','Fast capture must not scan rendering HISM')
 return findall(what)
end
package.loaded.skate_config={game_exe_sha256=string.rep('a',64)}
package.preload.skate_world=function()return {read=function()return {session=mode.changedSave and 'different'or'fixture-save'}end}end
local reader=require('building_native')
reader.bodies=function(c,m,ids)
 assert(m==mass and #ids<=4)
 local rows=reader.read(c,ids)
 for _,r in ipairs(rows)do
  r.entities={{entity_index=0,handle=33,physics_present=true,building_mesh=true,actor_enabled=true,created=true,
    dirty=false,collision_verified=true,collision={enabled=mode.policyChange and nativecalls.read or 3,object_type=27,responses={}},
    body_setup_address=mode.unknownBody and 0xdead or body:GetAddress(),source_object_address=c:GetAddress(),body_index=r.id}}
  for i=1,32 do r.entities[1].collision.responses[i]=2 end
 end
 local r=rows[1];return rows,'raw-verified-native-packet:'..r.transform.Translation.X..':'..r.entities[1].collision.enabled
end
reader.body_packet=function(c,m,ids)local _,packet=reader.bodies(c,m,ids);return packet end
fast=assert(load(state_source))()
function start_fast()fast.begin(pawn,{clock=clock})end
function finish_fast()
 for i=1,100 do local r=fast.advance(2,0.003);if r then return r end end
 error('Fast STATE2 did not finish')
end
function baseline()
 local names={};for _,a in ipairs(assets)do names[a.BuildingPieceDataIndex]=a:GetFullName():gsub('^[^ ]+ ','')end
 local d={};for id,p in pairs(pieces)do local v=p.ClientVisible
  d[tostring(id)]={asset=names[v.BuildingPieceDataIndex],location={v.Location.X,v.Location.Y,v.Location.Z},yaw=v.Yaw,ghosted=v.bGhosted}
 end
 local responses={};for i=0,31 do responses[i+1]=component:GetCollisionResponseToChannel(i)end
 return {schema='S3BUILDINGBASELINE1',game_exe_sha256=string.rep('a',64),world=world:GetFullName(),world_session='fixture-save',descriptors=d,native_piece_ids={10,30},
  manager_policy={{owner_collision=true,component={enabled=component:GetCollisionEnabled(),object_type=27,profile='BuildingPlaced',responses=responses}}}}
end
'''
class StateTests(unittest.TestCase):
 def setUp(self):
  self.lua=LuaRuntime(unpack_returned_tuples=True)
  self.lua.globals().source=SOURCE;self.lua.globals().native_source=NATIVE;self.lua.globals().state_source=STATE
  self.lua.execute(MOCK);self.lua.execute(EXTRA);self.lua.execute(FAST)
 def test_actual_state2_and_typed_body_binding(self):
  self.lua.execute('''start_fast();local r=finish_fast();assert(r.complete,table.concat(r.errors,';'))
   assert(r.schema=='S3BUILDINGSTATE2'and r.collision_verified and r.scope=='lightweight_pieces')
   assert(r.world_session=='fixture-save'and #r.inventory==2 and #r.pieces==2 and #r.native_missing_piece_ids==0)
   assert(r.pieces[1].entities[1].body_mesh==mesh:GetFullName())
   assert(r.pieces[1].entities[1].body_setup_asset==body:GetFullName())
   assert(nativecalls.read==2 and counters.piece_reads==4 and r.type_metadata==nil and r.building_hisms==nil)''')
 def test_missing_native_records_remain_explicit_without_actor_guess(self):
  self.lua.execute('''pieces[50]={ClientVisible={BuildingPieceDataIndex=7,Location={X=40000,Y=1,Z=1},Yaw=0,bGhosted=false}}
   start_fast();local r=finish_fast();assert(r.complete,table.concat(r.errors,';'))
   assert(#r.inventory==3 and #r.pieces==2 and r.native_missing_piece_ids[1]==50 and r.actor_piece_ids==nil)''')
 def test_body_identity_and_native_policy_changes_rejected(self):
  for mode in ('mode.unknownBody=true','mode.policyChange=true','mode.unknownAsset=true','mode.changedTransform=true','mode.changedIds=true','mode.ghostMismatch=true'):
   with self.subTest(mode=mode):
    self.setUp();self.lua.execute(mode+';start_fast();local r=finish_fast();assert(not r.complete and not r.collision_verified)')
 def test_wrong_runtime_body_type_rejected(self):
  self.lua.execute('mode.wrongBodyClass=true;start_fast();local r=finish_fast();assert(not r.complete)')
 def test_unavailable_property_subclass_accessor_retains_runtime_checks(self):
  self.lua.execute('mode.objectAccessorMissing=true;start_fast();local r=finish_fast();assert(r.complete,table.concat(r.errors,";"))')
 def test_global_descriptor_change_rejects_even_without_id_change(self):
  self.lua.execute('start_fast();assert(fast.advance(1)==nil);pieces[10].ClientVisible.Yaw=1;local r=finish_fast();assert(not r.complete)')
 def test_same_name_player_world_manager_replacement_rejected(self):
  for obj in ('pawn','world','global','component','mass'):
   with self.subTest(obj=obj):
    self.setUp();self.lua.execute('start_fast();'+obj+'.replacement_address=999999;local r=finish_fast();assert(not r.complete)')
 def test_save_or_manager_policy_change_rejected(self):
  for mode in ('mode.changedSave=true','mode.disabled=true'):
   with self.subTest(mode=mode):
    self.setUp();self.lua.execute('start_fast();'+mode+';local r=finish_fast();assert(not r.complete)')
 def test_cancel_never_returns_partial_success(self):
  self.lua.execute('start_fast();assert(fast.advance(1)==nil);fast.cancel();assert(not fast.busy()and fast.advance()==nil)')
 def test_failed_capture_exposes_exact_guard_and_phase(self):
  self.lua.execute('''start_fast();assert(fast.advance(1)==nil);pieces[10].ClientVisible.Yaw=1
   local r=finish_fast();assert(not r.complete and not r.collision_verified)
   assert(r.error==r.errors[1]and r.error:find('Global building descriptor changed during capture',1,true))
   assert(r.capture_failure_phase=='verify_global')''')
 def test_raw_packet_sink_only_receives_verified_first_pass(self):
  self.lua.execute('''packets={};fast.begin(pawn,{clock=clock,packet_sink=function(bytes)packets[#packets+1]=bytes end})
   local r=finish_fast();assert(r.complete,table.concat(r.errors,';'))
   assert(#packets==1 and packets[1]=='raw-verified-native-packet:1:3'and nativecalls.read==2)
   assert(#r.derived_assets==2 and #r.body_bindings==1 and r.data_assets[1].address>0)''')
 def test_delta_unchanged_uses_no_native_body_reads(self):
  self.lua.execute('''fast.begin(pawn,{clock=clock,incremental=true,baseline=baseline()});local r=finish_fast()
   assert(r.complete,table.concat(r.errors,';'));assert(r.capture_mode=='delta'and #r.pieces==0 and #r.retained_loaded_piece_ids==2)
   assert(#r.native_piece_ids==2 and nativecalls.read==0 and #r.body_bindings==0 and counters.mesh_discoveries==nil)''')
 def test_precise_published_baseline_avoids_repeated_fractional_recapture(self):
  self.lua.execute('json=(function()'+(Path(__file__).parent/'host-probe/json.lua').read_text()+'\nend)()')
  self.lua.execute('''pieces[10].ClientVisible.Location.X=14773.344694136491
   pieces[30].ClientVisible.Location.Y=182413.1288144879
   local actual=baseline();local old=json.decode(json.encode(actual))
   fast.begin(pawn,{clock=clock,incremental=true,baseline=old});local first=finish_fast()
   assert(first.complete and #first.pieces==2 and nativecalls.read==2)
   local exact=json.decode(json.encode_precise(actual));nativecalls.read=0
   fast.begin(pawn,{clock=clock,incremental=true,baseline=exact});local second=finish_fast()
   assert(second.complete and #second.pieces==0 and #second.retained_loaded_piece_ids==2 and nativecalls.read==0)
   pieces[10].ClientVisible.Location.X=pieces[10].ClientVisible.Location.X+0.00000000001
   fast.begin(pawn,{clock=clock,incremental=true,baseline=exact});local changed=finish_fast()
   assert(changed.complete and #changed.pieces==1 and changed.pieces[1].id==10)''')
 def test_delta_changed_and_new_require_native_body_reads(self):
  self.lua.execute('''local b=baseline();pieces[10].ClientVisible.Yaw=1
   pieces[50]={ClientVisible={BuildingPieceDataIndex=7,Location={X=1,Y=2,Z=3},Yaw=0,bGhosted=false}}
   require('building_native').ids=function()return {10,30,50}end
   fast.begin(pawn,{clock=clock,incremental=true,baseline=b});local r=finish_fast()
   assert(r.complete,table.concat(r.errors,';'));assert(#r.pieces==2 and r.pieces[1].id==10 and r.pieces[2].id==50)
   assert(#r.retained_loaded_piece_ids==1 and r.retained_loaded_piece_ids[1]==30)''')
 def test_delta_streamed_out_then_reloaded_is_recaptured(self):
  self.lua.execute('''local b=baseline();require('building_native').ids=function()return {10}end
   fast.begin(pawn,{clock=clock,incremental=true,baseline=b});local r=finish_fast();assert(r.complete)
   assert(#r.pieces==0 and r.native_missing_piece_ids[1]==30)
   b.native_piece_ids={10};require('building_native').ids=function()return {10,30}end
   fast.begin(pawn,{clock=clock,incremental=true,baseline=b});r=finish_fast();assert(r.complete)
   assert(#r.pieces==1 and r.pieces[1].id==30 and r.retained_loaded_piece_ids[1]==10)''')
 def test_delta_foreign_or_changed_policy_baseline_forces_full(self):
  for change in ("b.world_session='other'","b.game_exe_sha256=string.rep('b',64)","b.manager_policy[1].owner_collision=false"):
   with self.subTest(change=change):
    self.setUp();self.lua.execute('local b=baseline();'+change+';fast.begin(pawn,{clock=clock,incremental=true,baseline=b});local r=finish_fast();assert(r.complete and r.capture_mode=="full"and #r.pieces==2)')
 def test_independent_instances_do_not_block_priority_capture(self):
  self.lua.execute('''local audit=fast.new();audit.begin(pawn,{clock=clock});assert(audit.advance(1)==nil)
   fast.begin(pawn,{clock=clock,incremental=true,baseline=baseline()});local r=finish_fast();assert(r.complete and #r.pieces==0)
   local final;for i=1,100 do final=audit.advance(2,0.003);if final then break end end
   assert(final.complete and #final.pieces==2)''')
 def test_segmented_baseline_does_not_read_descriptors_until_ready(self):
  self.lua.execute('''local count=0;local loader={advance=function()count=count+1;if count==3 then return baseline()end end}
   fast.begin(pawn,{clock=clock,incremental=true,baseline_loader=loader})
   assert(fast.advance(1)==nil and counters.piece_reads==nil)
   assert(fast.advance(1)==nil and counters.piece_reads==nil)
   local r=finish_fast();assert(r.complete and r.capture_mode=='delta'and #r.pieces==0)''')
if __name__=='__main__':unittest.main()
