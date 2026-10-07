"""Isolated native-tag lease and remapped hold input; no game process or input."""
from pathlib import Path
from lupa import LuaRuntime
root=Path(__file__).parent
lua=LuaRuntime(unpack_returned_tuples=True)
lua.globals().source=(root/'host-probe/mount_control.lua').read_text()
lua.execute(r'''
local objects={};local counts={A=2,B=0};local nativeCounts={A=0,B=0};local adds=0;local removes=0;local now=0
local keys={'RemappedMount'};local down={};local suppression=true;local mounted=false
local teleport=false;local falling=false;local mountState=1;local nativeAsset=true
local otherBlock=false;local interacting=false;local interactionError=false;local active=false
local sheltered=false;local shelterChild=false
local extraBlockers={}
local shelterTag='Player.Status.Sheltered.BaseBuilding'
local diagnosticArraySize=nil;local diagnosticReadError=false;local replicatedReads=0
local zoneReport={ready=true,mount_blocked_count=0};local zoneReads=0
local failAfterAdd=false;local failNextCount=false
local keyShape='LocalUnrealParam';local returnedKeyRecords={};local keyUnwraps=0
local function text(s)return {ToString=function()return s end}end
FName=text
local function obj(path)
    local o={IsValid=function()return true end,GetFullName=function()return 'Object '..path end}
    objects[path]=o;return o
end
local mount='/Script/Dominion.DominionMountComponent:'
local tagsPath='/Script/Dominion.GameplayTagsComponent:'
local signatures={
 ['/Script/Dominion.DominionPlayerCharacter:GetMountComponent']={'ObjectProperty'},
 ['/Script/Dominion.DominionCharacterBase:GetGameplayTagsComponent']={'ObjectProperty'},
 [tagsPath..'AddLocalTag']={'StructProperty'},[tagsPath..'RemoveLocalTag']={'StructProperty'},
 [tagsPath..'CountTag']={'StructProperty','IntProperty'},
 [tagsPath..'HasTag']={'StructProperty','BoolProperty','BoolProperty'},
 [mount..'CanMount']={'BoolProperty'},[mount..'IsProhibited']={'BoolProperty'},[mount..'IsMounted']={'BoolProperty'},
 [mount..'GetMountState']={'EnumProperty'},[mount..'GetEquippedMountData']={'ObjectProperty'},
 ['/Script/Dominion.DominionPlayerCharacter:IsTeleporting']={'BoolProperty'},
 ['/Script/Engine.Pawn:GetMovementComponent']={'ObjectProperty'},
 ['/Script/Engine.NavMovementComponent:IsFalling']={'BoolProperty'},
 ['/Script/Engine.SubsystemBlueprintLibrary:GetLocalPlayerSubSystemFromPlayerController']={'ObjectProperty','ClassProperty','ObjectProperty'},
 ['/Script/EnhancedInput.EnhancedInputSubsystemInterface:QueryKeysMappedToAction']={'ObjectProperty','ArrayProperty'},
 ['/Script/Engine.PlayerController:IsInputKeyDown']={'StructProperty','BoolProperty'},
 ['/Script/GameplayTags.BlueprintGameplayTagLibrary:IsGameplayTagValid']={'StructProperty','BoolProperty'},
}
StaticFindObject=function(path)
 if signatures[path]then
  local fn=obj(path);fn.ForEachProperty=function(_,cb)for _,kind in ipairs(signatures[path])do
   cb({GetClass=function()return {GetFName=function()return text(kind)end}end})
  end end;return fn
 end
 return objects[path]
end
local cls=obj('/Script/Dominion.DominionPlayerCharacter')
local function prop(n,k)
 return {GetFName=function()return text(n)end,GetClass=function()return {GetFName=function()return text(k)end}end}
end
local container=obj('/Script/GameplayTags.GameplayTagContainer')
container.GetFullName=function()return 'ScriptStruct /Script/GameplayTags.GameplayTagContainer'end
local tagStruct=obj('/Script/GameplayTags.GameplayTag')
tagStruct.GetFullName=function()return 'ScriptStruct /Script/GameplayTags.GameplayTag'end
tagStruct.ForEachProperty=function(_,cb)cb(prop('TagName','NameProperty'))end
local stackStruct=obj('/Script/Dominion.DominionGameplayTagStack')
stackStruct.GetFullName=function()return 'ScriptStruct /Script/Dominion.DominionGameplayTagStack'end
stackStruct.ForEachProperty=function(_,cb)
 local p=prop('Tag','StructProperty');p.GetStruct=function()return tagStruct end
 cb(p);cb(prop('StackCount','IntProperty'))
end
local stackContainer=obj('/Script/Dominion.DominionGameplayTagStackContainer')
stackContainer.GetFullName=function()return 'ScriptStruct /Script/Dominion.DominionGameplayTagStackContainer'end
stackContainer.ForEachProperty=function(_,cb)
 local p=prop('Stacks','ArrayProperty');p.GetInner=function()
  local inner=prop('Element','StructProperty');inner.GetStruct=function()return stackStruct end;return inner
 end;cb(p)
end
local tagsClass=obj('/Script/Dominion.GameplayTagsComponent')
tagsClass.ForEachProperty=function(_,cb)
 local p=prop('ReplicatedGameplayTagStackContainer','StructProperty')
 p.GetStruct=function()return stackContainer end;cb(p)
end
container.ForEachProperty=function(_,cb)
 local p=prop('GameplayTags','ArrayProperty');p.GetInner=function()
  local inner=prop('Element','StructProperty');inner.GetStruct=function()return tagStruct end;return inner
 end;cb(p)
end
local mountClass=obj('/Script/Dominion.DominionMountComponent')
mountClass.ForEachProperty=function(_,cb)
 for _,field in ipairs({'ProhibitTags','ProhibitAndDismountTags'})do
  local p=prop(field,'StructProperty');p.GetStruct=function()return container end;cb(p)
 end
end
local inputClass=obj('/Script/EnhancedInput.EnhancedInputLocalPlayerSubsystem')
local action=obj('/Game/Gameplay/Inputs/Actions/Basic/IA_Mount.IA_Mount')
local subsystem=obj('/World/Input');subsystem.QueryKeysMappedToAction=function(_,actual)
 assert(actual==action);local out={};returnedKeyRecords={}
 for _,key in ipairs(keys)do
  local record={KeyName=text(key)};returnedKeyRecords[#returnedKeyRecords+1]=record
  if keyShape=='plain'then out[#out+1]=record
  elseif keyShape=='UScriptStruct'then
   -- Struct wrappers expose fields through __index; asking them for get() is
   -- invalid reflection and must not be used as a generic unwrap detector.
   out[#out+1]=setmetatable({type=function()return 'UScriptStruct'end},{__index=function(_,field)
    if field=='KeyName'then return record.KeyName end
    error('Unknown reflected struct field: '..field)
   end})
  else
   -- Match UE4SS's returned-array element: only type/get are exposed on the
   -- parameter; KeyName exists on the struct obtained through get().
   local shape=keyShape
   out[#out+1]={type=function()return shape end,get=function()
    keyUnwraps=keyUnwraps+1
    if shape=='malformed'then return nil end
    if shape=='broken_get'then error('Native key read failed')end
    if shape=='missing_name'then return {}end
    return record
   end}
   if shape=='malformed'or shape=='broken_get'or shape=='missing_name'then
    out[#out].type=function()return 'LocalUnrealParam'end
   end
  end
 end
 return out
end
local library=obj('/Script/Engine.Default__SubsystemBlueprintLibrary')
library.GetLocalPlayerSubSystemFromPlayerController=function(_,pc,c)assert(c==inputClass);return subsystem end
local tagLibrary=obj('/Script/GameplayTags.Default__BlueprintGameplayTagLibrary')
local function checkTag(t)assert(t.TagName:ToString()=='Player.Status.MountBlocked')end
tagLibrary.IsGameplayTagValid=function(_,tag)checkTag(tag);return true end
local asset=obj('/Game/NativeMount');local movement=obj('/World/Movement')
movement.IsFalling=function()return falling end
local function pair(id)
 local world=obj('/World/'..id);local tag=obj('/World/'..id..'/Tags');local comp=obj('/World/'..id..'/Mount')
 local function effectiveCount()return counts[id]>0 and counts[id]or nativeCounts[id]end
 tag.CountTag=function(_,t)
  if failNextCount then failNextCount=false;error('Count read failed after successful add')end
  if t.TagName:ToString()=='Player.Status.Stunned'then return otherBlock and 1 or 0 end
  if t.TagName:ToString()==shelterTag then return sheltered and 1 or 0 end
  if t.TagName:ToString()=='Player.Status.Dead'or t.TagName:ToString()=='Player.Status.InCombat'then
   return extraBlockers[t.TagName:ToString()]and 1 or 0
  end
  checkTag(t);return effectiveCount()
 end
 tag.ReplicatedGameplayTagStackContainer={Stacks=setmetatable({ForEach=function(_,cb)
  assert(not diagnosticReadError,'Replicated diagnostic read failed');replicatedReads=replicatedReads+1
  if nativeCounts[id]>0 then
   cb(1,{get=function()return {Tag={TagName=text('Player.Status.MountBlocked')},StackCount=nativeCounts[id]}end})
  end
 end},{__len=function()return diagnosticArraySize or(nativeCounts[id]>0 and 1 or 0)end})}
 tag.AddLocalTag=function(_,t)
  checkTag(t);counts[id]=counts[id]+1;adds=adds+1
  if failAfterAdd then failNextCount=true end
 end
 tag.RemoveLocalTag=function(_,t)checkTag(t);counts[id]=counts[id]-1;removes=removes+1 end
 tag.HasTag=function(_,t,exact)
  if t.TagName:ToString()==shelterTag then return sheltered or(not exact and shelterChild)end
  if t.TagName:ToString()=='Player.Status.Dead'or t.TagName:ToString()=='Player.Status.InCombat'then
   assert(exact==false);return extraBlockers[t.TagName:ToString()]==true
  end
  assert(t.TagName:ToString()=='Player.Status.Stunned'and exact==false);return otherBlock
 end
 comp.CanMount=function()return not mounted and nativeAsset and mountState==1 and not teleport and not falling and
  not otherBlock and next(extraBlockers)==nil and not sheltered and not shelterChild and not interacting and(not suppression or effectiveCount()==0)end
 comp.IsProhibited=function()return otherBlock or next(extraBlockers)~=nil or sheltered or shelterChild or(suppression and effectiveCount()>0)end
 comp.IsMounted=function()return mounted end
 comp.GetMountState=function()return mountState end
 comp.GetEquippedMountData=function()return nativeAsset and asset or nil end
 comp.ProhibitTags={GameplayTags={{TagName=text('Player.Status.MountBlocked')},{TagName=text(shelterTag)},
  {TagName=text('Player.Status.Dead')},{TagName=text('Player.Status.InCombat')}}}
 comp.ProhibitAndDismountTags={GameplayTags={{TagName=text('Player.Status.Stunned')}}}
 local pawn=obj('/World/'..id..'/Pawn');pawn.GetWorld=function()return world end
 pawn.IsA=function(_,c)return c==cls end
 pawn.GetMountComponent=function()return comp end;pawn.GetGameplayTagsComponent=function()return tag end
 pawn.IsTeleporting=function()return teleport end;pawn.GetMovementComponent=function()return movement end
 local pc=obj('/World/'..id..'/PC');pc.GetWorld=function()return world end
 pc.IsInputKeyDown=function(_,key)return down[key.KeyName:ToString()]==true end
 return pawn,pc
end
local pawn,pc=pair('A');local other,otherPC=pair('B')
package.preload.mount_interaction=function()return {is_interacting=function(p)
 assert(p==pawn);if interactionError then return nil,'Invalid interaction metadata'end;return interacting
end}end
package.preload.mount_zone=function()return {inspect=function(p)
 assert(p==pawn);zoneReads=zoneReads+1;return zoneReport
end}end
local mod=assert(load(source))()
local allowed=true
local function update(selected,time)
 now=time;return mod.update(pawn,pc,selected,{clock=function()return now end,input_allowed=allowed,active=active})
end
local function hold()
 down={};update(true,now+0.6)
 down[keys[1]]=true;update(true,now+0.1);return update(true,now+0.26)
end
local inspected=mod.inspect(pawn,pc)
assert(inspected.read_only and inspected.count==2 and inspected.keys[1]=='RemappedMount'and adds==0 and removes==0)
assert(keyUnwraps==1) -- Returned LocalUnrealParam must be unwrapped before reading KeyName.
local ready,status=update(true,0)
assert(not ready and status.waiting_reason and adds==0 and counts.A==2)
counts.A=0;mounted=true;ready,status=update(true,0.1)
assert(not ready and status.waiting_reason=='ordinary_mount_active'and adds==0);mounted=false
down.RemappedMount=true
assert(update(true,0.2));assert(counts.A==1 and adds==1 and not mod.consume_toggle())
assert(update(true,1));assert(not mod.consume_toggle()) -- require release after activation
down.RemappedMount=false;update(true,1.1)
down.RemappedMount=true;update(true,1.2);update(true,1.44);assert(not mod.consume_toggle())
update(true,1.46);assert(mod.consume_toggle());update(true,2);assert(not mod.consume_toggle())
allowed=false;update(true,3);allowed=true;update(true,4);assert(not mod.consume_toggle())
down.RemappedMount=false;update(true,4.1)
keys={'NewBinding'};down.NewBinding=true;update(true,5);update(true,6);assert(not mod.consume_toggle())
down.NewBinding=false;update(true,6.1);down.NewBinding=true;update(true,6.2);update(true,6.46)
assert(mod.consume_toggle()and adds==1)
-- Every native entry condition remains enforced while our own tag is held.
for _,case in ipairs({
 {set=function(v)nativeAsset=not v end,reason='native_mount_unavailable'},
 {set=function(v)mountState=v and 2 or 1 end,reason='native_mount_transition'},
 {set=function(v)teleport=v end,reason='teleporting'},
 {set=function(v)falling=v end,reason='falling'},
 {set=function(v)otherBlock=v end,reason='Player.Status.Stunned'},
 {set=function(v)interacting=v end,reason='interacting'},
})do
 case.set(true);ready,status=hold()
 assert(ready and status.entry_blocked==case.reason and not mod.consume_toggle(),case.reason)
 local enter,reason=mod.can_enter(pawn,pc);assert(not enter and reason==case.reason)
 case.set(false);assert(mod.can_enter(pawn,pc));hold();assert(mod.consume_toggle())
end
counts.A=2;ready,status=hold()
assert(ready and status.entry_blocked=='another_mount_blocker'and not mod.consume_toggle())
active=true;otherBlock=true;teleport=true;falling=true;interacting=true
hold();assert(mod.consume_toggle()) -- exit remains available after new blockers
active=false;otherBlock=false;teleport=false;falling=false;interacting=false
-- A read-only inspection of a different world must not redirect lease cleanup.
mod.inspect(other,otherPC);assert(mod.shutdown());assert(counts.A==1 and counts.B==0 and removes==1)
assert(mod.shutdown()and removes==1);counts.A=0
assert(not mod.can_enter(pawn,pc))
-- Failed native suppression is cleaned up and never produces a skating toggle.
suppression=false
ready,status=update(true,now+1);assert(not ready and status.error and counts.A==0 and adds==2 and removes==2)
assert(not mod.consume_toggle())
suppression=true;down={};assert(update(true,now+1));interactionError=true
ready,status=hold();assert(not ready and status.error and counts.A==0 and removes==3)
interactionError=false;down={};assert(update(true,now+1));assert(counts.A==1)
local entryAdds,entryRemoves=adds,removes
assert(mod.can_enter(pawn,pc));assert(not mod.can_enter(other,otherPC))
assert(adds==entryAdds and removes==entryRemoves and counts.A==1) -- eligibility is read-only
-- Releasing an equipped skateboard leaves every other owner's stack intact.
assert(update(false,now+1)==false and counts.A==0 and removes==4)
-- Both parameter wrappers and already-unwrapped records retain remapped names,
-- discard duplicates/invalid names, and never retain native key wrappers.
for _,shape in ipairs({'LocalUnrealParam','RemoteUnrealParam','plain','UScriptStruct'})do
 keyShape=shape;keys={'Z_Remapped','A_Remapped','Z_Remapped','None',''};down={}
 ready,status=update(true,now+1)
 assert(ready and #status.keys==2 and status.keys[1]=='A_Remapped'and status.keys[2]=='Z_Remapped')
 returnedKeyRecords[1].KeyName=text('ChangedNativeRecord')
 assert(mod.status().keys[2]=='Z_Remapped') -- strings copied, no retained struct reference
 down.A_Remapped=true;update(true,now+0.1);update(true,now+0.26)
 assert(mod.consume_toggle())
 assert(mod.shutdown()and counts.A==0)
end
-- Bad returned elements release exactly our one tag stack and discard toggles.
-- An unrelated owner's stack survives, and corrected mappings can recover.
for _,shape in ipairs({'malformed','broken_get','missing_name','UnsupportedParam'})do
 keyShape='LocalUnrealParam';keys={'RecoveredBinding'};down={}
 assert(update(true,now+1));assert(counts.A==1)
 counts.A=counts.A+1 -- another native owner acquires its own stack
 keyShape=shape;local oldRemoves=removes
 ready,status=update(true,now+1)
 assert(not ready and status.error and not status.owns_one_stack and not status.suppressed)
 assert(counts.A==1 and removes==oldRemoves+1 and not mod.consume_toggle())
 assert(mod.shutdown()and removes==oldRemoves+1)
 counts.A=0 -- external owner releases its own stack
 keyShape='LocalUnrealParam';down={};assert(update(true,now+1))
 assert(mod.status().keys[1]=='RecoveredBinding'and mod.shutdown()and counts.A==0)
end
-- Native CountTag prefers the local map; removing its last stack reveals the
-- native map instead of summing both. A native blocker arriving while mounted
-- therefore legitimately reads 1 before AND after a successful local removal.
keyShape='LocalUnrealParam';down={};assert(update(true,now+1))
nativeCounts.A=1
local beforeRemoves=removes
assert(mod.shutdown()and removes==beforeRemoves+1 and counts.A==0 and nativeCounts.A==1)
status=mod.status()
assert(not status.owns_one_stack and not status.suppressed)
assert(status.release_before_tag_count==1 and status.release_after_tag_count==1)
assert(mod.shutdown()and removes==beforeRemoves+1) -- never chase native readback
ready,status=update(true,now+1)
assert(not ready and status.waiting_reason=='native_mount_blocked'and not status.error)
assert(removes==beforeRemoves+1 and nativeCounts.A==1 and counts.A==0)
-- The fresh native-only blocker is reported through typed replicated fields;
-- neither read-only inspection nor the throttled waiting diagnostic alters it.
local diagnosticAdds,diagnosticRemoves=adds,removes
local diagnostic=mod.inspect(pawn,pc).diagnostics
assert(diagnostic.read_only and diagnostic.count_tag==1 and diagnostic.replicated.mount_blocked_count==1)
assert(diagnostic.replicated.evidence=='replicated_stack_present'and #diagnostic.errors==0)
assert(diagnostic.replicated.stacks[1].tag=='Player.Status.MountBlocked')
assert(diagnostic.mount_state==1 and not diagnostic.mounted and not diagnostic.falling and not diagnostic.interacting)
otherBlock=true;falling=true;teleport=true;interacting=true
diagnostic=mod.inspect(pawn,pc).diagnostics
assert(diagnostic.other_blockers[4].tag=='Player.Status.Stunned'and diagnostic.other_blockers[4].present and diagnostic.other_blockers[4].count==1)
assert(diagnostic.falling and diagnostic.teleporting and diagnostic.interacting)
otherBlock=false;falling=false;teleport=false;interacting=false
ready,status=update(true,now+3)
local readCount=replicatedReads
update(true,now+0.1);assert(replicatedReads==readCount) -- two-second throttle
diagnosticArraySize=257;diagnostic=mod.inspect(pawn,pc).diagnostics
assert(diagnostic.replicated==nil and #diagnostic.errors==1)
diagnosticArraySize=nil;diagnosticReadError=true;diagnostic=mod.inspect(pawn,pc).diagnostics
assert(#diagnostic.errors==1)
diagnosticReadError=false
assert(adds==diagnosticAdds and removes==diagnosticRemoves and nativeCounts.A==1 and counts.A==0)
-- A local-only foreign stack is not relabeled as adapter-owned.
nativeCounts.A=0;counts.A=1
diagnostic=mod.inspect(pawn,pc).diagnostics
assert(diagnostic.count_tag==1 and diagnostic.replicated.mount_blocked_count==0)
assert(diagnostic.replicated.evidence=='no_replicated_stack_observed'and not mod.status().owns_one_stack)
counts.A=0;nativeCounts.A=1
nativeCounts.A=0 -- the game, not the adapter, releases the native blocker
down={};assert(update(true,now+1));hold();assert(mod.consume_toggle())
assert(mod.shutdown()and counts.A==0)
-- Persisted skateboard selection inside one verified static no-mount area:
-- own local suppression, preserve replicated restriction, require key release.
nativeCounts.A=1;zoneReport={ready=true,mount_blocked_count=1,zones={'KnownNoMount'}}
down={RecoveredBinding=true};keys={'RecoveredBinding'}
local areaAdds,areaRemoves=adds,removes
ready,status=update(true,now+3)
assert(ready and status.owns_one_stack and status.native_restriction.area_exception)
assert(status.before_tag_count==1 and status.tag_count==1 and adds==areaAdds+1)
assert(counts.A==1 and nativeCounts.A==1 and not mod.consume_toggle())
assert(mod.can_enter(pawn,pc));hold();assert(mod.consume_toggle())
-- The same known area cannot mask a second effect's replicated blocker.
nativeCounts.A=2
local can,reason=mod.can_enter(pawn,pc)
assert(not can and reason=='native_mount_blocked')
hold();assert(not mod.consume_toggle())
-- A zone report is re-read before asynchronous entry, never cached eligibility.
nativeCounts.A=1;zoneReport={ready=true,mount_blocked_count=0}
can,reason=mod.can_enter(pawn,pc);assert(not can and reason=='native_mount_blocked')
zoneReport={ready=false,error='Zone signature changed'}
can,reason=mod.can_enter(pawn,pc);assert(not can and reason=='native_mount_blocked')
zoneReport={ready=true,mount_blocked_count=1}
-- Existing death/combat/interaction/floor/transition guards still take priority.
for _,case in ipairs({
 {set=function(v)nativeAsset=not v end,reason='native_mount_unavailable'},
 {set=function(v)mountState=v and 2 or 1 end,reason='native_mount_transition'},
 {set=function(v)teleport=v end,reason='teleporting'},
 {set=function(v)falling=v end,reason='falling'},
 {set=function(v)otherBlock=v end,reason='Player.Status.Stunned'},
 {set=function(v)interacting=v end,reason='interacting'},
})do
 case.set(true);can,reason=mod.can_enter(pawn,pc)
 assert(not can and reason==case.reason,case.reason);case.set(false)
end
assert(update(false,now+1)==false and counts.A==0 and nativeCounts.A==1)
assert(removes==areaRemoves+1 and mod.shutdown()and removes==areaRemoves+1)
-- Re-selection acquires a new local stack and release does not alter the area.
down={};assert(update(true,now+3));assert(mod.can_enter(pawn,pc))
assert(mod.shutdown()and counts.A==0 and nativeCounts.A==1)
-- A foreign local stack can equal the native count before acquisition. The
-- completed add reveals it (1->2); cleanup removes exactly our added stack.
counts.A=1;local foreignAdds,foreignRemoves=adds,removes
ready,status=update(true,now+3)
assert(not ready and status.error and not status.owns_one_stack)
assert(counts.A==1 and nativeCounts.A==1 and adds==foreignAdds+1 and removes==foreignRemoves+1)
assert(mod.shutdown()and removes==foreignRemoves+1)
update(true,now+0.1);assert(adds==foreignAdds+1) -- bounded retry
counts.A=0
-- A failed read after a completed add cannot lose ownership (even 1->1).
failAfterAdd=true;local failedAdds,failedRemoves=adds,removes
ready,status=update(true,now+3)
assert(not ready and status.error and not status.owns_one_stack)
assert(adds==failedAdds+1 and removes==failedRemoves+1 and counts.A==0 and nativeCounts.A==1)
failAfterAdd=false
-- Malformed replicated metadata blocks acquisition without changing any tag.
diagnosticReadError=true;failedAdds=adds;ready,status=update(true,now+3)
assert(not ready and status.error and adds==failedAdds and counts.A==0 and nativeCounts.A==1)
diagnosticReadError=false
-- Both native areas are allowed only when both replicated stacks are explained.
nativeCounts.A=2;zoneReport={ready=true,mount_blocked_count=2}
down={};assert(update(true,now+3));assert(mod.can_enter(pawn,pc))
assert(mod.shutdown()and counts.A==0 and nativeCounts.A==2)
nativeCounts.A=0;zoneReport={ready=true,mount_blocked_count=0}
assert(update(true,now+3));assert(mod.can_enter(pawn,pc));assert(mod.shutdown())
assert(counts.A==0 and nativeCounts.A==0)
-- A selected skateboard can acquire its local suppression inside a player-built
-- shelter, even though the unchanged native mount remains prohibited. This also
-- covers a fresh re-selection, rather than only an already-owned local lease.
for _,areaCount in ipairs({0,1})do
 sheltered=true;nativeCounts.A=areaCount
 zoneReport={ready=true,mount_blocked_count=areaCount};down={RecoveredBinding=true}
 local shelterAdds,shelterRemoves=adds,removes
 ready,status=update(true,now+3)
 assert(ready and status.native_restriction.shelter_exception and status.before_can_mount==false)
 assert(counts.A==1 and nativeCounts.A==areaCount and not mod.consume_toggle())
 assert(mod.can_enter(pawn,pc));hold();assert(mod.consume_toggle())
 assert(pawn:GetGameplayTagsComponent():HasTag({TagName=FName(shelterTag)},true))
 assert(update(false,now+1)==false and counts.A==0 and nativeCounts.A==areaCount)
 assert(sheltered and not pawn:GetMountComponent():CanMount()and pawn:GetMountComponent():IsProhibited())
 down={};assert(update(true,now+3));assert(mod.can_enter(pawn,pc))
 assert(mod.shutdown()and adds==shelterAdds+2 and removes==shelterRemoves+2)
end
-- Acquiring outside and then walking into shelter keeps read-only can_enter valid.
sheltered=false;nativeCounts.A=0;zoneReport={ready=true,mount_blocked_count=0}
down={};assert(update(true,now+3));sheltered=true;assert(mod.can_enter(pawn,pc))
-- Shelter never masks unknown replicated restrictions, another native blocker,
-- death/combat/transition/movement checks, or interaction while already selected.
nativeCounts.A=1
local can,reason=mod.can_enter(pawn,pc);assert(not can and reason=='native_mount_blocked')
nativeCounts.A=0
for _,case in ipairs({
 {set=function(v)extraBlockers['Player.Status.Dead']=v and true or nil end,reason='Player.Status.Dead'},
 {set=function(v)extraBlockers['Player.Status.InCombat']=v and true or nil end,reason='Player.Status.InCombat'},
 {set=function(v)nativeAsset=not v end,reason='native_mount_unavailable'},
 {set=function(v)mountState=v and 2 or 1 end,reason='native_mount_transition'},
 {set=function(v)teleport=v end,reason='teleporting'},
 {set=function(v)falling=v end,reason='falling'},
 {set=function(v)otherBlock=v end,reason='Player.Status.Stunned'},
 {set=function(v)interacting=v end,reason='interacting'},
})do
 case.set(true);can,reason=mod.can_enter(pawn,pc)
 assert(not can and reason==case.reason,case.reason);hold();assert(not mod.consume_toggle())
 assert(mod.shutdown());local blockedAdds=adds
 ready,status=update(true,now+3)
 assert(not ready and status.waiting_reason==case.reason and adds==blockedAdds,case.reason)
 case.set(false);down={};assert(update(true,now+3));assert(mod.can_enter(pawn,pc))
end
-- Extra same-tag native effects are blocked even inside a verified area+shelter.
nativeCounts.A=2;zoneReport={ready=true,mount_blocked_count=1}
can,reason=mod.can_enter(pawn,pc);assert(not can and reason=='native_mount_blocked')
assert(mod.shutdown());local blockedAdds=adds
ready,status=update(true,now+3)
assert(not ready and status.waiting_reason=='native_mount_blocked'and adds==blockedAdds)
-- A foreign local stack must survive acquisition cleanup, with or without areas.
for _,areaCount in ipairs({0,1})do
 nativeCounts.A=areaCount;zoneReport={ready=true,mount_blocked_count=areaCount};counts.A=1
 local foreignAdds,foreignRemoves=adds,removes
 ready,status=update(true,now+3)
 assert(not ready and status.error and not status.owns_one_stack and not mod.consume_toggle())
 assert(counts.A==1 and nativeCounts.A==areaCount and adds==foreignAdds+1 and removes==foreignRemoves+1)
 assert(mod.shutdown()and removes==foreignRemoves+1)
 counts.A=0
end
-- Exact tag only: a future child restriction cannot silently acquire the exception.
sheltered=false;shelterChild=true;nativeCounts.A=0;zoneReport={ready=true,mount_blocked_count=0}
blockedAdds=adds;ready,status=update(true,now+3)
assert(not ready and status.waiting_reason==shelterTag and adds==blockedAdds)
shelterChild=false;down={};assert(update(true,now+3));assert(mod.can_enter(pawn,pc));assert(mod.shutdown())
assert(counts.A==0 and nativeCounts.A==0 and pawn:GetMountComponent():CanMount())
''')
print('PASS: native entry gates, lease ownership, diagnostics, verified static areas; exact skateboard-only shelter acquisition/reselection, asynchronous entry, retained native restrictions, unknown child/native/local blockers, and unchanged ordinary mount eligibility.')
