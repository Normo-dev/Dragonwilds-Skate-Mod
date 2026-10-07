"""Readonly reflected interaction gate, independent of live input and mount tags."""
from pathlib import Path
from lupa import LuaRuntime

lua = LuaRuntime(unpack_returned_tuples=True)
lua.globals().source = (Path(__file__).parent/'host-probe/mount_interaction.lua').read_text()
lua.execute(r'''
local registry={};local fieldReads=0;local badKind=false;local badInner=false
local sharedKind=nil;local sharedMethods={};local wrongDeclaredClass=false;local accessorFailure=false
local function text(s)return {ToString=function()return s end}end
local function object(path,prefix)
 local result={live=true}
 result.IsValid=function(self)return self.live end
 result.GetFullName=function()return (prefix or 'Object')..' '..path end
 result.IsA=function(self,c)return self.class==c end
 registry[path]=result;return result
end
StaticFindObject=function(path)return registry[path]end
local playerClass=object('/Script/Dominion.DominionPlayerCharacter','Class')
local detectorClass=object('/Script/Dominion.InteractableDetectorComponent','Class')
local componentClass=object('/Script/Dominion.InteractionComponent','Class')
local function property(n,k,c)
 local p={declaredClass=c,GetFName=function()return text(n)end,GetClass=function()return {GetFName=function()return text(k)end}end}
 if k=='ObjectProperty'or k=='BoolProperty'then
  if not sharedKind then
   sharedKind=k
   if k=='ObjectProperty'then sharedMethods.GetPropertyClass=function(self)
    assert(not accessorFailure,'Native class accessor failed')
    return wrongDeclaredClass and playerClass or self.declaredClass
   end end
  end
  setmetatable(p,{__index=sharedMethods})
 end
 return p
end
local getter=object('/Script/Dominion.DominionPlayerCharacter:GetInteractableDetector')
getter.ForEachProperty=function(_,cb)cb(property('ReturnValue','ObjectProperty',detectorClass))end
detectorClass.ForEachProperty=function(_,cb)
 cb(property('CurrentInteractable',badKind and 'SoftObjectProperty'or 'ObjectProperty',componentClass))
end
componentClass.ForEachProperty=function(_,cb)
 local p=property('PlayersCurrentlyInteracting','ArrayProperty')
 p.GetInner=function()return property('Element',badInner and 'WeakObjectProperty'or 'ObjectProperty',playerClass)end
 cb(p)
end
local world=object('/World');local foreign=object('/Foreign')
local pawn=object('/World/Pawn');pawn.class=playerClass;pawn.GetWorld=function()return world end
local other=object('/World/Other');other.class=playerClass
local detector=object('/World/Detector');detector.class=detectorClass;detector.GetWorld=function()return world end
local component=object('/World/Interaction');component.class=componentClass;component.GetWorld=function()return world end
local current,players=component,{}
setmetatable(detector,{__index=function(_,key)assert(key=='CurrentInteractable');fieldReads=fieldReads+1;return current end})
setmetatable(component,{__index=function(_,key)assert(key=='PlayersCurrentlyInteracting');fieldReads=fieldReads+1;return players end})
pawn.GetInteractableDetector=function()return detector end
local mod=assert(load(source))()
assert(mod.is_interacting(pawn)==false)
players={other};assert(mod.is_interacting(pawn)==false)
players={other,pawn};assert(mod.is_interacting(pawn)==true)
-- Actual UE4SS TArray reference shape, invalid elements, and identity equality.
local dead=object('/Dead');dead.live=false
players={ForEach=function(_,cb)
 cb(1,{get=function()return dead end});cb(2,{get=function()return pawn end})
end}
assert(mod.is_interacting(pawn)==true)
current=nil;assert(mod.is_interacting(pawn)==false)
current=component;detector.live=false;assert(mod.is_interacting(pawn)==false);detector.live=true
component.GetWorld=function()return foreign end
local value,error=mod.is_interacting(pawn);assert(value==nil and error)
component.GetWorld=function()return world end
-- Reflection drift must refuse before reading object-valued fields.
badKind=true;mod=assert(load(source))();local before=fieldReads
value,error=mod.is_interacting(pawn);assert(value==nil and error and fieldReads==before)
badKind=false;badInner=true;mod=assert(load(source))()
value,error=mod.is_interacting(pawn);assert(value==nil and error and fieldReads==before)
badInner=false
-- The class accessor remains authoritative whenever the shared metatable has
-- it: neither wrong metadata nor native read exceptions may fall back.
wrongDeclaredClass=true;mod=assert(load(source))()
value,error=mod.is_interacting(pawn);assert(value==nil and error and fieldReads==before)
wrongDeclaredClass=false;accessorFailure=true;mod=assert(load(source))()
value,error=mod.is_interacting(pawn);assert(value==nil and error and fieldReads==before)
accessorFailure=false
-- Bool-first construction omits GetPropertyClass exactly as the pinned loader
-- does. Hard object-property kinds and actual mapped IsA checks still apply.
sharedKind=nil;sharedMethods={};property('EarlierBoolean','BoolProperty')
players={other,pawn};mod=assert(load(source))();assert(mod.is_interacting(pawn)==true)
players={other};assert(mod.is_interacting(pawn)==false)
detector.class=playerClass;value,error=mod.is_interacting(pawn);assert(value==nil and error)
detector.class=detectorClass
component.class=playerClass;value,error=mod.is_interacting(pawn);assert(value==nil and error)
component.class=componentClass
other.class=componentClass;value,error=mod.is_interacting(pawn);assert(value==nil and error)
other.class=playerClass
badInner=true;mod=assert(load(source))();before=fieldReads
value,error=mod.is_interacting(pawn);assert(value==nil and error and fieldReads==before)
badInner=false;mod=assert(load(source))();players={pawn};assert(mod.is_interacting(pawn)==true)
''')
print('PASS: same-pawn interaction membership, nil/dead references, UE4SS array shape, foreign-world refusal, typed reflection rejection; bool-first wrapper fallback with mapped class checks and strict available metadata/errors.')
