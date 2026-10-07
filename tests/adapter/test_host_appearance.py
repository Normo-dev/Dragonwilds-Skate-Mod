"""Check the real appearance adapter follows equipment changes between mounts."""
from pathlib import Path
from lupa import LuaRuntime

lua=LuaRuntime(unpack_returned_tuples=True)
lua.execute(r'''
package.preload.json=function()return {}end
package.preload.skate_config=function()return {mailbox='unused-test-mailbox/'}end
print=function()end
local function valid()return true end
FName=function(n)return {ToString=function()return n end}end
local function property(n,k)
 return {GetFName=function()return FName(n)end,GetClass=function()return {GetFName=function()return FName(k)end}end}
end
primitiveClass={ForEachProperty=function(_,fn)
 local p=property('CustomPrimitiveDataInternal','StructProperty')
 p.GetStruct=function()return {GetFullName=function()return 'ScriptStruct /Script/Engine.CustomPrimitiveData'end}end
 fn(p);fn(property('bRenderInMainPass','BoolProperty'))
end}
playerClass={ForEachProperty=function(_,fn)
 local p=property('VisibleMeshes',visibleListKind or 'ArrayProperty');local inner=property('',visibleInnerKind or 'ObjectProperty')
 if propertyClassAccess~='missing'and propertyClassAccess~='missing_error'then
  inner.GetPropertyClass=function()
   if propertyClassAccess=='call_error'then error('unexpected accessor failure')end
   return {GetFullName=function()return declaredMeshClass or 'Class /Script/Engine.MeshComponent'end}
  end
 elseif propertyClassAccess=='missing_error'then
  setmetatable(inner,{__index=function(_,key)if key=='GetPropertyClass'then error('method unavailable')end end})
 end
 p.GetInner=function()return inner end;fn(p)
end}
StaticFindObject=function(p)
 if p=='/Script/Engine.PrimitiveComponent'then return primitiveClass end
 if p=='/Script/Engine.MeshComponent'then
  return {IsValid=function()return not missingMeshClass end,GetFullName=function()return 'Class /Script/Engine.MeshComponent'end}
 end
 local signatures={GetNumLODs={'IntProperty'},IsMaterialSectionShown={'IntProperty','IntProperty','BoolProperty'},
   ShowMaterialSection={'IntProperty','IntProperty','BoolProperty','IntProperty'}}
 local types=signatures[p:match(':([^:]+)$')]
 if types then return {ForEachProperty=function(_,fn)for _,t in ipairs(types)do fn(property('',t))end end}end
 return {path=p}
end
targets={};scans=0
asset={IsValid=valid}
mesh={SkeletalMesh=asset,GetNumBones=function()return 0 end}
pawn={IsValid=valid,Mesh=mesh,GetClass=function()return playerClass end,GetFullName=function()return 'Pawn self'end}
other={IsValid=valid,GetFullName=function()return 'Pawn other'end}
local function component(name,owner,visible,hidden)
 return {IsValid=valid,SkeletalMesh=asset,bVisible=visible,bHiddenInGame=hidden,bRenderInMainPass=true,
  IsA=function(self,class)return self.notMesh~=true and class:GetFullName()=='Class /Script/Engine.MeshComponent'end,
  GetOwner=function()return owner end,GetFullName=function()return name end,
  GetNumBones=function()return 84 end,GetBoneIndex=function()return 1 end,
  GetNumLODs=function()return 2 end,IsMaterialSectionShown=function(self,material,lod)return not(self.hiddenLods and self.hiddenLods[lod])end,
  GetNumMaterials=function()return 1 end,GetMaterial=function()return name..' material'end,
  CustomPrimitiveData={Data={ForEach=function(_,fn)fn(1,0)end}},
  CustomPrimitiveDataInternal={Data={ForEach=function(_,fn)
   for i=1,8 do local v=i==8 and 5 or .75;fn(i,{get=function()return v end})end
  end}}}
end
old=component('old shirt',pawn,true,false)
new=component('new shirt',pawn,true,false)
hair=component('hair',pawn,true,false)
hidden=component('under armor',pawn,true,true)
invisible=component('unequipped',pawn,false,false)
foreign=component('NPC armor',other,true,false)
helper=component('invisible base rig',pawn,true,false);helper.bRenderInMainPass=false
unused=component('extra hair helper',pawn,true,false)
all={old,hair,hidden,invisible,foreign,helper}
pawn.VisibleMeshes={ForEach=function(_,fn)scans=scans+1;for i,c in ipairs(all)do fn(i,{get=function()return c end})end end}
FindAllOf=function()error('Appearance must use the native visual list, not all skeletal components')end
local function actor()
 return {IsValid=valid,K2_SetActorLocation=function()end,AddComponentByClass=function()
  local target={IsValid=valid,SetSkinnedAssetAndUpdate=function()end,SetCollisionEnabled=function()end,
   SetLeaderPoseComponent=function()end,SetMaterial=function(self,i,value)self.material=value end,
   ShowMaterialSection=function(self,material,section,shown,lod)
    assert(section==-1 and shown==false);self.hiddenLods=self.hiddenLods or {};self.hiddenLods[lod]=true
   end,IsMaterialSectionShown=function(self,material,lod)return not(self.hiddenLods and self.hiddenLods[lod])end,
   SetCustomPrimitiveDataFloat=function(self,i,value)self.primitive=self.primitive or {};self.primitive[i]=value end}
  targets[#targets+1]=target;return target
 end}
end
pawn.GetWorld=function()return {SpawnActor=function()return actor()end}end
''')
lua.globals().rig=lua.execute((Path(__file__).parent/'host-probe/host_rig.lua').read_text())
lua.execute(r'''
rig.refresh_appearance(pawn);rig.create(pawn,{X=0,Y=0,Z=0})
assert(#targets==3 and targets[2].material=='old shirt material'and targets[3].material=='hair material')
assert(targets[2].primitive[0]==.75 and targets[3].primitive[7]==5 and scans==1)
-- Old components may stay valid but be hidden after an equipment change.
old.bHiddenInGame=true;all={old,new,hair,hidden,invisible,foreign,helper,new};targets={}
rig.refresh_appearance(pawn);rig.create(pawn,{X=0,Y=0,Z=0})
assert(#targets==3 and targets[2].material=='new shirt material'and targets[3].material=='hair material')
assert(scans==2)
-- Actual helmet assets hide hair/beard material sections, independently of CPD colour.
hair.hiddenLods={[0]=true,[1]=true}
targets={};rig.refresh_appearance(pawn);rig.create(pawn,{X=0,Y=0,Z=0})
assert(#targets==3 and targets[3].primitive[7]==5 and targets[3].hiddenLods[0]and targets[3].hiddenLods[1])
hair.hiddenLods=nil;targets={};rig.refresh_appearance(pawn);rig.create(pawn,{X=0,Y=0,Z=0})
assert(targets[3].primitive[7]==5 and not targets[3].hiddenLods)
-- Do not silently replace unavailable live colour/masks with the editor defaults.
originalHairData=hair.CustomPrimitiveDataInternal;hair.CustomPrimitiveDataInternal=nil;assert(not pcall(rig.refresh_appearance,pawn))
-- A failed refresh must refuse entry, not replace the snapshot with no meshes.
all={hidden,invisible,foreign};assert(not pcall(rig.refresh_appearance,pawn))
''')
print('PASS: native visual mesh list, render-only selection, refreshed equipment/materials, exact live CPD colour/mask values, duplicate/hidden/helper rejection and refusal of missing live appearance data.')

source=(Path(__file__).parent/'host-probe/host_rig.lua').read_text()
for mode in ('missing','missing_error'):
    lua.globals().propertyClassAccess=mode
    lua.globals().rig=lua.execute(source)
    lua.execute(r'''
hair.CustomPrimitiveDataInternal=originalHairData;all={new,hair,hidden,invisible,foreign,helper,new};targets={}
hair.hiddenLods={[0]=true,[1]=true}
rig.refresh_appearance(pawn);rig.create(pawn,{X=0,Y=0,Z=0})
assert(#targets==3 and targets[2].material=='new shirt material'and targets[3].primitive[7]==5)
assert(targets[3].hiddenLods[0]and targets[3].hiddenLods[1])
-- Actual UObject type remains mandatory even when reflection's class accessor is absent.
foreign.notMesh=true;assert(not pcall(rig.refresh_appearance,pawn));foreign.notMesh=nil
new.notMesh=true;assert(not pcall(rig.refresh_appearance,pawn));new.notMesh=nil
missingMeshClass=true;assert(not pcall(rig.refresh_appearance,pawn));missingMeshClass=nil
''')
    print('PASS: bool-first object wrapper '+mode+' uses validated MeshComponent values and preserves current colour/helmet sections.')

for change,value in [('visibleListKind','SetProperty'),('visibleInnerKind','StructProperty'),
                     ('declaredMeshClass','Class /Script/Engine.Actor'),('propertyClassAccess','call_error')]:
    lua.execute("visibleListKind=nil;visibleInnerKind=nil;declaredMeshClass=nil;propertyClassAccess=nil;all={new,hair}")
    lua.globals()[change]=value
    lua.globals().rig=lua.execute(source)
    assert not lua.eval('pcall(rig.refresh_appearance,pawn)')[0], (change,value)
print('PASS: changed array/inner metadata, wrong declared class and unexpected accessor errors still reject appearance capture.')
