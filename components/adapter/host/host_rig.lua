-- Dragonwilds appearance and reference skeleton for the Skate 3 pose adapter.
local json=require('json')
local M={}
local appearance={}
local appearanceAPI=false
local poseCache={}
local auditNextPose=false
local poseState={mode='full',verified=false,initialized=false,driven=0,total=0}
local box=require('skate_config').mailbox
local identity={Rotation={X=0,Y=0,Z=0,W=1},Translation={X=0,Y=0,Z=0},Scale3D={X=1,Y=1,Z=1}}
local function valid(o) return o and o:IsValid() end
local function log(s) print('[SkateRig] '..s..'\n') end
local function kind(p)return p:GetClass():GetFName():ToString()end
local function signature(path,types)
    local found={}
    StaticFindObject(path):ForEachProperty(function(p)found[#found+1]=kind(p)end)
    assert(#found==#types,'Changed appearance API: '..path)
    for i,t in ipairs(types)do assert(found[i]==t,'Changed appearance parameter: '..path)end
end
local function verify_appearance(pawn)
    if appearanceAPI then return end
    local fields={}
    StaticFindObject('/Script/Engine.PrimitiveComponent'):ForEachProperty(function(p)
        local name=p:GetFName():ToString()
        if name=='CustomPrimitiveDataInternal' then
            assert(kind(p)=='StructProperty'and p:GetStruct():GetFullName()=='ScriptStruct /Script/Engine.CustomPrimitiveData',
                'Live appearance data layout changed')
            fields.live=true
        elseif name=='bRenderInMainPass' then
            assert(kind(p)=='BoolProperty','Appearance visibility layout changed');fields.visible=true
        end
    end)
    pawn:GetClass():ForEachProperty(function(p)
        if p:GetFName():ToString()=='VisibleMeshes' then
            assert(kind(p)=='ArrayProperty','Native visual mesh list layout changed')
            local inner=p:GetInner()
            assert(kind(inner)=='ObjectProperty','Native visual mesh list layout changed')
            -- This loader names both BoolProperty and ObjectProperty Lua
            -- metatables "ObjectProperty". If a bool wrapper is constructed
            -- first, GetPropertyClass is absent from later object wrappers.
            -- Keep the exact metadata check when available, then validate the
            -- actual typed UObject values on every appearance refresh below.
            local available,getter=pcall(function()return inner.GetPropertyClass end)
            if available and type(getter)=='function' then
                assert(getter(inner):GetFullName()=='Class /Script/Engine.MeshComponent',
                    'Native visual mesh list layout changed')
            else log('Object property class accessor unavailable; validating native MeshComponent values')end
            fields.meshes=true
        end
    end)
    assert(fields.live and fields.visible and fields.meshes,'Native current appearance fields unavailable')
    signature('/Script/Engine.SkinnedMeshComponent:GetNumLODs',{'IntProperty'})
    signature('/Script/Engine.SkinnedMeshComponent:IsMaterialSectionShown',{'IntProperty','IntProperty','BoolProperty'})
    signature('/Script/Engine.SkinnedMeshComponent:ShowMaterialSection',{'IntProperty','IntProperty','BoolProperty','IntProperty'})
    appearanceAPI=true
end
local function unwrap(value)
    local ok,result=pcall(function()return value:get()end)
    if ok then return result end
    return value
end
local function transform(t)
    return {Rotation={X=t.Rotation.X,Y=t.Rotation.Y,Z=t.Rotation.Z,W=t.Rotation.W},
            Translation={X=t.Translation.X,Y=t.Translation.Y,Z=t.Translation.Z},
            Scale3D={X=t.Scale3D.X,Y=t.Scale3D.Y,Z=t.Scale3D.Z}}
end
local function new_leader(pawn,anchor)
    local actor=pawn:GetWorld():SpawnActor(StaticFindObject('/Script/Engine.Actor'),anchor,{Pitch=0,Yaw=0,Roll=0})
    assert(valid(actor),'Cannot create character pose actor')
    local leader=actor:AddComponentByClass(StaticFindObject('/Script/Engine.PoseableMeshComponent'),false,identity,false)
    assert(valid(leader),'Cannot create character pose component')
    leader:SetSkinnedAssetAndUpdate(pawn.Mesh.SkeletalMesh,true)
    leader:SetCollisionEnabled(0)
    actor:K2_SetActorLocation(anchor,false,{},true)
    return actor,leader
end
function M.refresh_appearance(pawn)
    verify_appearance(pawn)
    local meshClass=StaticFindObject('/Script/Engine.MeshComponent')
    assert(valid(meshClass)and meshClass:GetFullName()=='Class /Script/Engine.MeshComponent',
        'Native visual mesh component class unavailable')
    local current,seen={},{}
    -- The game owns this list of rendered character parts. Enumerating every
    -- attached skeletal component also copied its invisible animation helpers.
    pawn.VisibleMeshes:ForEach(function(_,value)
        local c=unwrap(value)
        if valid(c)then
            assert(c:IsA(meshClass)==true,'Native visual mesh list contains a non-MeshComponent object')
        end
        if valid(c) and valid(c:GetOwner()) and c:GetOwner():GetFullName()==pawn:GetFullName() and
            c.bVisible and not c.bHiddenInGame and c.bRenderInMainPass and valid(c.SkeletalMesh) and c:GetNumBones()>0 then
            local name=c:GetFullName()
            if not seen[name]then
                seen[name]=true
                local values={}
                -- CustomPrimitiveData is the editor default. The transient
                -- Internal array is current shader data (colour/wetness/dissolve).
                c.CustomPrimitiveDataInternal.Data:ForEach(function(index,item)
                    local n=unwrap(item)
                    assert(type(index)=='number'and index%1==0 and index>=1 and index<=256 and
                        type(n)=='number'and n==n and math.abs(n)<math.huge,'Invalid live character material data')
                    values[index]=n
                end)
                local hidden={}
                local lods,materials=c:GetNumLODs(),c:GetNumMaterials()
                assert(type(lods)=='number'and lods%1==0 and lods>=1 and lods<=32 and
                    type(materials)=='number'and materials%1==0 and materials>=0 and materials<=256,'Invalid appearance section count')
                for lod=0,lods-1 do for material=0,materials-1 do
                    local shown=c:IsMaterialSectionShown(material,lod)
                    assert(type(shown)=='boolean','Invalid current material section state')
                    if not shown then hidden[#hidden+1]={material=material,lod=lod}end
                end end
                current[#current+1]={source=c,primitive=values,hidden=hidden}
            end
        end
    end)
    assert(#current>0,'No visible Dragonwilds character appearance components found')
    appearance=current
end
function M.prepare(pawn,anchor)
    local actor,leader=new_leader(pawn,anchor)
    actor:SetActorHiddenInGame(true)
    local ok,why=pcall(function()
        local rig={bones={},mesh=pawn.Mesh.SkeletalMesh:GetFullName()}
        for i=0,pawn.Mesh:GetNumBones()-1 do
            local name=pawn.Mesh:GetBoneName(i):ToString()
            local parent=pawn.Mesh:GetParentBone(FName(name)):ToString()
            rig.bones[#rig.bones+1]={name=name,parent=parent,bind=transform(leader:GetBoneTransformByName(FName(name),1))}
            log('BONE '..name..' parent='..parent)
        end
        M.refresh_appearance(pawn)
        local encoded=json.encode(rig)
        local f=assert(io.open(box..'host-rig.json','w'));assert(f:write(encoded));assert(f:close())
        log('Reference captured: '..#rig.bones..' bones')
    end)
    actor:K2_DestroyActor()
    if not ok then error(why) end
end
local function configure_layout(pawn,layout)
    poseState={mode='full',verified=false,initialized=false,driven=#poseCache,total=#poseCache,reason='No sparse pose layout'}
    if layout==nil then return end
    local ok,why=pcall(function()
        assert(type(layout)=='table' and layout.schema=='S3HOSTPOSE1','Unknown host pose layout')
        assert(type(layout.bones)=='table' and #layout.bones==#poseCache,'Host pose layout bone count changed')
        for key in pairs(layout.bones)do assert(type(key)=='number' and key%1==0 and key>=1 and key<=#poseCache,'Invalid host pose layout index')end
        local seen,driven={},0
        for i,bone in ipairs(poseCache)do
            local entry=layout.bones[i];local name=bone.name:ToString()
            assert(type(entry)=='table' and entry.name==name and type(entry.parent)=='string' and
                type(entry.driven)=='boolean','Host pose layout name or type mismatch')
            assert(not seen[name],'Duplicate host pose bone')
            assert(pawn.Mesh:GetParentBone(bone.name):ToString()==entry.parent,'Host pose layout parent mismatch')
            if entry.parent=='None' then
                assert(i==1 and name=='Root' and entry.driven,'Root must be first and driven')
            else assert(seen[entry.parent],'Host pose parent must precede its child')end
            seen[name]=true;bone.driven=entry.driven;if entry.driven then driven=driven+1 end
        end
        assert(seen.Root and poseCache[1].name:ToString()=='Root','Host pose root is missing')
        poseState.mode='sparse';poseState.driven=driven;poseState.reason='Waiting for full-pose initialization and sparse readback'
    end)
    if not ok then
        poseState.mode='full';poseState.reason=tostring(why)
        log('Sparse pose layout rejected; using all bones: '..poseState.reason)
    end
end
function M.create(pawn,anchor,layout)
    local actor,leader=new_leader(pawn,anchor)
    poseCache={}
    auditNextPose=true
    for i=0,pawn.Mesh:GetNumBones()-1 do
        poseCache[#poseCache+1]={name=pawn.Mesh:GetBoneName(i),t={Translation={X=0,Y=0,Z=0},Rotation={X=0,Y=0,Z=0,W=1},Scale3D={X=1,Y=1,Z=1}}}
    end
    configure_layout(pawn,layout)
    local count=0
    for _,entry in ipairs(appearance) do
        local source=entry.source
        if valid(source) and valid(source:GetOwner()) and source:GetOwner():GetFullName()==pawn:GetFullName() and valid(source.SkeletalMesh) then
            local ok,same=pcall(function() return source:GetBoneIndex(FName('Pelvis'))>=0 end)
            if ok and same then
                local target=actor:AddComponentByClass(StaticFindObject('/Script/Engine.PoseableMeshComponent'),false,identity,false)
                target:SetSkinnedAssetAndUpdate(source.SkeletalMesh,true)
                for i=0,source:GetNumMaterials()-1 do target:SetMaterial(i,source:GetMaterial(i)) end
                for index,value in ipairs(entry.primitive)do target:SetCustomPrimitiveDataFloat(index-1,value)end
                target:SetCollisionEnabled(0)
                target:SetLeaderPoseComponent(leader,true,false)
                for _,section in ipairs(entry.hidden)do
                    -- Material ID is already resolved by the source getter.
                    -- INDEX_NONE skips section-to-material LOD remapping.
                    target:ShowMaterialSection(section.material,-1,false,section.lod)
                    assert(target:IsMaterialSectionShown(section.material,section.lod)==false,
                        'Could not preserve equipped character section visibility')
                end
                count=count+1
                log('COPIED APPEARANCE '..source:GetFullName())
            end
        end
    end
    assert(count>0,'No Dragonwilds character appearance components found')
    log('Character appearance ready: '..count..' mesh components')
    return actor,leader
end
local function submit(leader,bones,i,bone)
    local n=(i-1)*10;local t=bone.t
    t.Translation.X=bones[n+1];t.Translation.Y=bones[n+2];t.Translation.Z=bones[n+3]
    t.Rotation.X=bones[n+4];t.Rotation.Y=bones[n+5];t.Rotation.Z=bones[n+6];t.Rotation.W=bones[n+7]
    t.Scale3D.X=bones[n+8];t.Scale3D.Y=bones[n+9];t.Scale3D.Z=bones[n+10]
    leader:SetBoneTransformByName(bone.name,t,1)
end
local function apply(leader,bones,sparse)
    for i,bone in ipairs(poseCache)do if not sparse or bone.driven then submit(leader,bones,i,bone)end end
end
local function check_sparse(leader,bones)
    for i,bone in ipairs(poseCache)do
        local n=(i-1)*10;local actual=leader:GetBoneTransformByName(bone.name,1)
        local p,q,s=actual.Translation,actual.Rotation,actual.Scale3D
        local dx,dy,dz=p.X-bones[n+1],p.Y-bones[n+2],p.Z-bones[n+3]
        assert(dx*dx+dy*dy+dz*dz<=0.0001,'Sparse position mismatch: '..bone.name:ToString())
        local direct,opposite=0,0
        for j,key in ipairs({'X','Y','Z','W'})do
            local expected=bones[n+3+j];local actualValue=q[key]
            direct=direct+(actualValue-expected)^2;opposite=opposite+(actualValue+expected)^2
        end
        assert(math.min(direct,opposite)<=1e-10,'Sparse rotation mismatch: '..bone.name:ToString())
        assert(math.abs(s.X-bones[n+8])<=1e-5 and math.abs(s.Y-bones[n+9])<=1e-5 and
            math.abs(s.Z-bones[n+10])<=1e-5,'Sparse scale mismatch: '..bone.name:ToString())
    end
end
local function fallback(leader,bones,why)
    poseState.mode='full';poseState.verified=false;poseState.reason=tostring(why)
    local failures=0
    -- A failed speculative setter/readback must never prevent the old full
    -- path from restoring the remaining bones in this same presentation call.
    for i,bone in ipairs(poseCache)do
        local ok=pcall(submit,leader,bones,i,bone);if not ok then failures=failures+1 end
    end
    poseState.fallback_errors=failures
    log('Sparse pose disabled; full pose fallback attempted (setter errors '..failures..'): '..poseState.reason)
end
function M.pose_status()
    local result={};for key,value in pairs(poseState)do result[key]=value end;return result
end
function M.present(leader,bones)
    assert(#bones==#poseCache*10,'Host skeleton packet length changed')
    if poseState.mode=='sparse' and poseState.initialized then
        local ok,why=pcall(function()
            apply(leader,bones,true)
            if not poseState.verified then check_sparse(leader,bones)end
        end)
        if not ok then fallback(leader,bones,why)
        elseif not poseState.verified then
            poseState.verified=true;poseState.reason='Sparse descendant readback matched the complete pose'
            log('Sparse pose verified: '..poseState.driven..' of '..poseState.total..' bone setters per frame')
        end
    else apply(leader,bones,false);poseState.initialized=true end
    if auditNextPose then
        auditNextPose=false
        local actual={}
        for _,bone in ipairs(poseCache) do
            actual[#actual+1]={name=bone.name:ToString(),transform=transform(leader:GetBoneTransformByName(bone.name,1))}
        end
        local f=assert(io.open(box..'host-pose-audit.json','w'))
        f:write(json.encode({submitted=bones,actual=actual}));f:close()
        log('First host pose read back for verification')
    end
end
return M
