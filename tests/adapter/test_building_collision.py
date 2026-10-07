"""Read-only lightweight-building capture invariants; no game or IPC access."""
from pathlib import Path
import unittest,sys
sys.path.insert(0,str(Path(__file__).parent/'python-deps'))
from lupa import LuaRuntime

SOURCE=(Path(__file__).parent/'host-probe/building_collision.lua').read_text()
MOCK=r'''
registry={}; schemas={}; counters={reads=0,mutations=0}; mode={}; time=0
function clock()time=time+0.0001;return time end
local function name(s)return {ToString=function()return s end}end
local function object(full)
 local o={deleted=false,IsValid=function(self)return not self.deleted end,GetFullName=function()return full end}
 registry[full:gsub('^[^ ]+ ','')]=o;return o
end
local function prop(n,t,s,c)
 local p={GetFName=function()return name(n)end,GetClass=function()return {GetFName=function()return name(t)end}end,
  GetStruct=function()return registry[s]or object('ScriptStruct '..s)end,
  GetPropertyClass=function()return registry[c]or object('Class '..c)end}
 if mode.objectAccessorMissing and t=='ObjectProperty'then p.GetPropertyClass=nil end
 if mode.wrongDeclaredClass and c then p.GetPropertyClass=function()return object('Class /Script/Test.Foreign')end end
 return p
end
local function schema(path,kind,properties)
 local o=object(kind..' '..path);schemas[path]=properties
 o.ForEachProperty=function(_,cb)for _,p in ipairs(schemas[path])do cb(prop(p[1],p[2],p[3],p[4]))end end
 o.ForEachFunction=function()end
 return o
end
globalClass=schema('/Script/Dominion.GlobalBuildingManager','Class',{
 {'BuildingPieces','MapProperty'},{'LightweightBuildingPieceManager','ObjectProperty',nil,'/Script/Dominion.LightweightBuildingPieceManager'}})
schema('/Script/Dominion.BuildingPieceState','ScriptStruct',{{'ClientVisible','StructProperty','/Script/Dominion.ClientVisibleBuildingPieceState'}})
schema('/Script/Dominion.ClientVisibleBuildingPieceState','ScriptStruct',{
 {'BuildingPieceDataIndex','IntProperty'},{'Location','StructProperty','/Script/CoreUObject.Vector'},
 {'Yaw','FloatProperty'},{'bGhosted','BoolProperty'},{'StabilityValue','FloatProperty'},{'FractionalHealth','FloatProperty'}})
assetClass=schema('/Script/Dominion.BuildingPieceData','Class',{{'BuildingPieceDataIndex','IntProperty'}})
schema('/Script/Dominion.BuildingSettings','Class',{
 {'ActiveCollisionProfileName','StructProperty','/Script/Engine.CollisionProfileName'},
 {'InactiveCollisionProfileName','StructProperty','/Script/Engine.CollisionProfileName'}})
schema('/Script/Engine.CollisionProfileName','ScriptStruct',{{'Name','NameProperty'}})
lightClass=object('Class /Script/Dominion.LightweightBuildingPieceManager')
schema('/Script/Engine.StaticMeshComponent','Class',{{'StaticMesh','ObjectProperty',nil,'/Script/Engine.StaticMesh'}})
for fn,types in pairs({['Actor:GetActorEnableCollision']={'BoolProperty'},
 ['PrimitiveComponent:GetCollisionEnabled']={'ByteProperty'},
 ['PrimitiveComponent:GetCollisionObjectType']={'ByteProperty'},
 ['PrimitiveComponent:GetCollisionProfileName']={'NameProperty'},
 ['PrimitiveComponent:GetCollisionResponseToChannel']={'ByteProperty','ByteProperty'},
 ['InstancedStaticMeshComponent:GetInstanceCount']={'IntProperty'}})do
 local p={};for i,t in ipairs(types)do p[i]={'P'..i,t}end
 schema('/Script/Engine.'..fn,'Function',p)
end
world=object('World /Game/Test.World');otherworld=object('World /Game/Other.World')
pawn=object('BP_PlayerCharacter_C /Game/Test.World:PersistentLevel.Player');pawn.GetWorld=function()return mode.foreignPawn and otherworld or world end
component=object('LightweightBuildingPieceManager /Game/Test.World:PersistentLevel.Global.Lightweight')
component.IsA=function(_,c)return c==lightClass end;component.GetWorld=function()return world end
component.GetCollisionEnabled=function()return mode.disabled and 0 or 3 end
component.GetCollisionObjectType=function()return 27 end
component.GetCollisionProfileName=function()return name('BuildingPlaced')end
component.GetCollisionResponseToChannel=function(_,c)return c==17 and 2 or 0 end
pieces={
 [30]={ClientVisible={BuildingPieceDataIndex=7,Location={X=1,Y=2,Z=3},Yaw=45,bGhosted=false,StabilityValue=0.8,FractionalHealth=1}},
 [10]={ClientVisible={BuildingPieceDataIndex=9,Location={X=4,Y=5,Z=6},Yaw=90,bGhosted=true,StabilityValue=0.4,FractionalHealth=0.5}},
}
local map={}
setmetatable(map,{__len=function()local n=0;for _ in pairs(pieces)do n=n+1 end;return n end})
map.ForEach=function(_,callback)
 counters.reads=counters.reads+1
 for id,v in pairs(pieces)do
  if callback({get=function()return id end},{get=function()return v end})==true then break end
 end
end
map.Find=function(_,id)counters.reads=counters.reads+1;counters.piece_reads=(counters.piece_reads or 0)+1;assert(pieces[id],'Missing map key');return {get=function()return pieces[id]end}end
map.Add=function()error('Mutation forbidden')end;map.Remove=map.Add;map.Empty=map.Add
global=object('GlobalBuildingManager /Game/Test.World:PersistentLevel.Global')
global.IsA=function(_,c)return c==globalClass end;global.GetWorld=function()return mode.foreignManager and otherworld or world end
global.GetActorEnableCollision=function()return true end
global.BuildingPieces=map;global.LightweightBuildingPieceManager=component
assets={}
for i,index in ipairs({7,9})do
 local a=object('BuildingPieceData /Game/Buildings/Piece'..index..'.Piece'..index)
 a.IsA=function(_,c)return c==assetClass end;a.BuildingPieceDataIndex=index
 setmetatable(a,{__index=function(_,k)error('Unapproved/soft asset property read: '..k)end})
 assets[i]=a
end
settings=object('BuildingSettings /Script/Dominion.Default__BuildingSettings')
settings.ActiveCollisionProfileName={Name=name('BuildingPlaced')};settings.InactiveCollisionProfileName={Name=name('BuildingPreview')}
setmetatable(settings,{__index=function(_,k)error('Unapproved/soft settings property read: '..k)end})
StaticFindObject=function(path)return registry[path]end
FindAllOf=function(what)
 if what=='GlobalBuildingManager'then return {global}end
 if what=='BuildingPieceData'then return assets end
 if what=='BuildingHISMC'then return hisms or{}end
 error('Broad object inventory forbidden: '..what)
end
RegisterHook=function()error('Hook forbidden')end;LoopAsync=function()error('Async forbidden')end
io.open=function()error('Filesystem forbidden')end
module=assert(load(source))()
function finish()
 for _=1,100 do local result=module.advance(2,0.003);if result then return result end end
 error('Capture did not finish')
end
function begin()module.begin(pawn,{clock=clock});return module end
'''

class BuildingCaptureTests(unittest.TestCase):
    def setUp(self):
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().source=SOURCE
        self.lua.execute(MOCK)
    def run_lua(self,code):self.lua.execute(code)
    def test_capture_sorted_and_explicitly_unverified(self):
        self.run_lua("""begin();local r=finish();assert(r.complete and not r.collision_verified and #r.pieces==2)
assert(r.pieces[1].id==10 and r.pieces[1].ghosted and r.pieces[2].id==30)
assert(r.pieces[2].data_asset=='BuildingPieceData /Game/Buildings/Piece7.Piece7')
assert(r.settings.active_profile=='BuildingPlaced' and r.managers[1].component.responses[18]==2)
assert(not module.busy() and module.advance()==nil)""")
    def test_schema_rejected_before_map_reads(self):
        self.run_lua("""schemas['/Script/Dominion.ClientVisibleBuildingPieceState'][2][3]='/Script/CoreUObject.Rotator'
begin();local r=finish();assert(not r.complete and counters.reads==0)""")
    def test_foreign_manager_ignored(self):
        self.run_lua("mode.foreignManager=true;begin();local r=finish();assert(r.complete and #r.pieces==0)")
    def test_removed_piece_rejected(self):
        self.run_lua("begin();pieces[10]=nil;local r=finish();assert(not r.complete)")
    def test_same_count_id_replacement_rejected(self):
        self.run_lua("begin();pieces[99]=pieces[10];pieces[10]=nil;local r=finish();assert(not r.complete)")
    def test_value_change_during_capture_rejected(self):
        self.run_lua("""begin();for i=1,100 do assert(module.advance(1)==nil);if counters.piece_reads then break end end
assert(counters.piece_reads==1)
pieces[10].ClientVisible.Yaw=22;local r=finish();assert(not r.complete)""")
    def test_runtime_index_missing_rejected(self):
        self.run_lua("pieces[10].ClientVisible.BuildingPieceDataIndex=400;begin();local r=finish();assert(not r.complete)")
    def test_shared_property_metatable_fallback_keeps_actual_type_checks(self):
        self.run_lua("mode.objectAccessorMissing=true;begin();local r=finish();assert(r.complete and #r.pieces==2)")
        self.run_lua("component.IsA=function()return false end;begin();local r=finish();assert(not r.complete)")
    def test_available_wrong_declared_class_rejected(self):
        self.run_lua("mode.wrongDeclaredClass=true;begin();local r=finish();assert(not r.complete and counters.reads==0)")
    def test_duplicate_runtime_index_rejected(self):
        self.run_lua("assets[2].BuildingPieceDataIndex=7;begin();local r=finish();assert(not r.complete)")
    def test_component_policy_change_rejected(self):
        self.run_lua("begin();mode.disabled=true;local r=finish();assert(not r.complete)")
    def test_world_change_rejected(self):
        self.run_lua("begin();mode.foreignPawn=true;local r=finish();assert(not r.complete)")
    def test_deleted_manager_is_not_retained(self):
        self.run_lua("begin();global.deleted=true;local r=finish();assert(not r.complete)")
    def test_bounded_batches_and_cancel(self):
        self.run_lua("begin();assert(module.advance(100,0.00005)==nil and module.busy());module.cancel();assert(not module.busy())")
    def test_hism_outside_persistent_name_is_recorded(self):
        self.run_lua("""local c={};for k,v in pairs(component)do c[k]=v end
c.GetFullName=function()return 'BuildingHISMC /Game/Test.World:MassRepresentation.Floor' end
registry['/Game/Test.World:MassRepresentation.Floor']=c
c.GetOwner=function()return global end;c.GetOuter=function()return global end
local meshClass={IsValid=function()return true end,GetFullName=function()return 'Class /Script/Engine.StaticMesh'end};registry['/Script/Engine.StaticMesh']=meshClass
c.GetInstanceCount=function()return 12 end
c.StaticMesh={IsValid=function()return true end,IsA=function(_,k)return k==meshClass end,GetFullName=function()return 'StaticMesh /Game/Test.Floor'end};hisms={c}
begin();local r=finish();assert(r.complete and #r.building_hisms==1)
local h=r.building_hisms[1];assert(h.same_world and not h.passes_persistent_name_filter and h.instance_count==12)""")
    def test_metadata_getters_are_not_invoked(self):
        self.run_lua("""globalClass.ForEachFunction=function(_,cb)
cb({IsValid=function()return true end,GetFullName=function()return 'Function /Script/Dominion.GlobalBuildingManager:Example' end,
 ForEachProperty=function(_,p)p({GetFName=function()return {ToString=function()return 'Result' end}end,
 GetClass=function()return {GetFName=function()return {ToString=function()return 'BoolProperty' end}end}end})end})end
global.Example=function()error('Metadata probe called a getter')end
begin();local r=finish();assert(r.complete)
local f=r.type_metadata[1].functions[1];assert(f.name:find(':Example',1,true) and f.parameters[1].type=='BoolProperty')""")
    def test_metadata_count_limit_does_not_reject_state(self):
        self.run_lua("""globalClass.ForEachFunction=function(_,cb)
local f={IsValid=function()return true end,GetFullName=function()return 'Function /Script/Dominion.GlobalBuildingManager:Example' end,
 ForEachProperty=function()end};for i=1,1000 do cb(f)end end
begin();local r=finish();assert(r.complete and #r.pieces==2)
assert(r.type_metadata_errors[1].error:find('function limit exceeded',1,true))""")

if __name__=='__main__':unittest.main()
