"""Native mode gate using synthetic property metadata; no process, input or IPC."""
from pathlib import Path
import json
from lupa import LuaRuntime

root=Path(__file__).parent
# The exact field/type contract is represented as synthetic metadata; no live
# capture or game asset is needed to exercise the adapter's validation paths.
fields={'CurrentInputMode','GameplayInputMode','MountedInputMode','GameplayLockOnTargetingInputMode'}
metadata=[{'name':name,'type':'ObjectProperty','object_class':'Class /Script/Dominion.DominionInputMode'}
          for name in sorted(fields)]
lua=LuaRuntime(unpack_returned_tuples=True)
lua.globals().source=(root/'host-probe/gameplay_input_gate.lua').read_text()
lua.globals().metadata=lua.table_from([lua.table_from(p)for p in metadata])
lua.execute(r'''
local objects={};local reads=0;local reflectionCalls=0
local badProperty=false;local badEnum=false;local badPause=false;local paused=false
local sharedKind=nil;local sharedMethods={};local wrongDeclaredClass=false;local accessorFailure=false
local function text(s)return {ToString=function()return s end}end
local function object(path,prefix)
 local value={live=true};value.IsValid=function(self)return self.live end
 value.GetFullName=function()return (prefix or'Object')..' '..path end
 value.IsA=function(self,class)return self.class==class end
 objects[path]=value;return value
end
StaticFindObject=function(path)return objects[path]end
FindAllOf=function()error('The input gate must not enumerate widgets or objects')end
RegisterHook=function()error('The input gate must not install hooks')end
local controller=object('/Script/Dominion.DominionPlayerController','Class')
local pawnClass=object('/Script/Dominion.DominionPlayerCharacter','Class')
local modeClass=object('/Script/Dominion.DominionInputMode','Class')
local playerController=object('/Script/Engine.PlayerController','Class')
local modeEnum=object('/Script/Dominion.EDominionWidgetInputMode','Enum')
local function property(n,k)
 local p={GetFName=function()return text(n)end,GetClass=function()return {GetFName=function()return text(k)end}end}
 if k=='ObjectProperty'or k=='BoolProperty'then
  -- Match the pinned native wrapper bug: the first of these two types owns
  -- the same Lua metatable and subsequent wrappers never add their methods.
  if not sharedKind then
   sharedKind=k
   if k=='ObjectProperty'then sharedMethods.GetPropertyClass=function(self)
    assert(not accessorFailure,'Native class accessor failed')
    return wrongDeclaredClass and controller or self.declaredClass
   end end
  end
  setmetatable(p,{__index=sharedMethods})
 end
 return p
end
controller.ForEachProperty=function(_,callback)
 reflectionCalls=reflectionCalls+1
 for _,entry in ipairs(metadata)do
  local p=property(entry.name,badProperty and entry.name=='CurrentInputMode'and'SoftObjectProperty'or entry.type)
  p.declaredClass=modeClass;callback(p)
 end
end
playerController.ForEachProperty=function(_,callback)callback(property('bShowMouseCursor','BoolProperty'))end
modeClass.ForEachProperty=function(_,callback)
 local p=property('InputConfig',badEnum and'ByteProperty'or'EnumProperty')
 p.GetEnum=function()return modeEnum end;callback(p)
end
local pauseGetter=object('/Script/Engine.GameplayStatics:IsGamePaused')
pauseGetter.ForEachProperty=function(_,callback)
 callback(property('WorldContextObject','ObjectProperty'));callback(property('ReturnValue',badPause and'IntProperty'or'BoolProperty'))
end
local library=object('/Script/Engine.Default__GameplayStatics')
library.IsGamePaused=function()return paused end
local world=object('/World');local foreign=object('/Foreign')
local pawn=object('/World/Pawn');pawn.class=pawnClass;pawn.GetWorld=function()return world end
local pc=object('/World/PC');pc.class=controller;pc.GetWorld=function()return world end
local modes={}
for _,field in ipairs({'GameplayInputMode','MountedInputMode','GameplayLockOnTargetingInputMode'})do
 local m=object('/Game/'..field);m.class=modeClass;m.InputConfig=2;modes[field]=m
end
local current=modes.GameplayInputMode;local cursor=false
setmetatable(pc,{__index=function(_,key)
 reads=reads+1
 if key=='CurrentInputMode'then return current end
 if key=='bShowMouseCursor'then return cursor end
 assert(modes[key]~=nil,'Unexpected/soft controller field read: '..key);return modes[key]
end})
local gate=assert(load(source))()
local allowed,status=gate.allowed(pawn,pc)
assert(allowed and status.ready and status.mode_field=='GameplayInputMode')
local verifiedCalls=reflectionCalls
for field,value in pairs(modes)do
 current=value;allowed,status=gate.allowed(pawn,pc);assert(allowed and status.mode_field==field)
end
assert(reflectionCalls==verifiedCalls,'Metadata verification should be cached')
-- Every controller menu is blocked without relying on a visible mouse cursor.
for _,label in ipairs({'MainInventory','FullScreenUI','Chat','Crafting','QuickAccessRadial','Radial','Emotes'})do
 current=object('/Game/'..label);current.class=modeClass;current.InputConfig=1
 allowed,status=gate.allowed(pawn,pc)
 assert(not allowed and status.ready and status.reason=='ui_or_cutscene_mode'and status.cursor==false,label)
end
-- Game input config alone is insufficient: death, build/fishing/unknown modes
-- cannot bypass the narrow identities configured on this exact controller.
current=object('/Game/UnknownGameMode');current.class=modeClass;current.InputConfig=2
allowed,status=gate.allowed(pawn,pc);assert(not allowed and status.reason=='non_gameplay_mode')
current.InputConfig=4;assert(not gate.allowed(pawn,pc))
current=modes.GameplayInputMode;paused=true;allowed,status=gate.allowed(pawn,pc)
assert(not allowed and status.reason=='paused');paused=false
cursor=true;allowed,status=gate.allowed(pawn,pc);assert(not allowed and status.reason=='mouse_cursor');cursor=false
-- A death screen must retain its native mode identity behind both input gates.
current=object('/Game/Gameplay/Inputs/Modes/DIM_Dead.DIM_Dead','DominionInputMode')
current.class=modeClass;current.InputConfig=2
for _,state in ipairs({{true,false},{false,true},{true,true}})do
 paused=state[1];cursor=state[2];allowed,status=gate.allowed(pawn,pc)
 assert(not allowed and status.ready and status.input_mode=='DominionInputMode /Game/Gameplay/Inputs/Modes/DIM_Dead.DIM_Dead')
end
paused=false;cursor=false
current=nil;allowed,status=gate.allowed(pawn,pc);assert(not allowed and status.reason=='input_mode_unavailable')
current=modes.GameplayInputMode;current.InputConfig=1;assert(not gate.allowed(pawn,pc));current.InputConfig=2
pc.GetWorld=function()return foreign end;allowed,status=gate.allowed(pawn,pc)
assert(not allowed and not status.ready and status.error);pc.GetWorld=function()return world end
pc.live=false;assert(not gate.allowed(pawn,pc));pc.live=true
current.InputConfig={};allowed,status=gate.allowed(pawn,pc)
assert(not allowed and not status.ready and status.input_config==nil);current.InputConfig=2
-- Soft-property drift and unavailable/changed reflection fail before state reads.
for _,case in ipairs({
 function(v)badProperty=v end,function(v)badEnum=v end,function(v)badPause=v end,
})do
 case(true);gate=assert(load(source))();local before=reads
 allowed,status=gate.allowed(pawn,pc);assert(not allowed and not status.ready and status.error and reads==before)
 case(false)
end
gate=assert(load(source))();assert(gate.allowed(pawn,pc))
-- Existing object-first reflection remains strict about declared class and
-- must not swallow a real accessor exception into the wrapper fallback.
wrongDeclaredClass=true;gate=assert(load(source))();local before=reads
allowed,status=gate.allowed(pawn,pc);assert(not allowed and status.error and reads==before)
wrongDeclaredClass=false;accessorFailure=true;gate=assert(load(source))()
allowed,status=gate.allowed(pawn,pc);assert(not allowed and status.error and reads==before)
accessorFailure=false
-- Fresh BoolProperty-first order reproduces the live loader collision.
sharedKind=nil;sharedMethods={};property('EarlierBoolean','BoolProperty')
gate=assert(load(source))();allowed,status=gate.allowed(pawn,pc)
assert(allowed and status.property_class_validation=='native_property_and_mapped_class')
-- Every available configured object is checked, even an unused mode after the
-- matching gameplay field. A valid current mode cannot hide that wrong type.
modes.MountedInputMode.class=pawnClass
allowed,status=gate.allowed(pawn,pc);assert(not allowed and status.error)
modes.MountedInputMode.class=modeClass
current.class=controller;allowed,status=gate.allowed(pawn,pc);assert(not allowed and status.error)
current.class=modeClass
badProperty=true;gate=assert(load(source))();before=reads
allowed,status=gate.allowed(pawn,pc);assert(not allowed and status.error and reads==before)
badProperty=false;gate=assert(load(source))();assert(gate.allowed(pawn,pc))
-- Missing optional configured objects remain nonmatching; no guessed alias.
modes.MountedInputMode.live=false;assert(gate.allowed(pawn,pc));modes.MountedInputMode.live=true
''')
print('PASS: synthetic hard-property contract, cursorless menus, gameplay identities, pause/death/unknown modes, world checks; real shared-metatable construction order, strict available declared class/accessor errors and all mapped mode classes; zero scans/hooks/input.')
