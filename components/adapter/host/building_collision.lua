-- Read-only evidence for Dragonwilds' lightweight (Mass entity) buildings.
-- These bodies are owned by a PrimitiveComponent, not StaticMeshComponents.
-- This module captures state only. It NEVER promotes preview geometry to
-- blocking collision: the built entity policy still needs native validation.
local M={}
local pending
local function valid(o) return o and o:IsValid() end
local function path(o) assert(valid(o),'Invalid reflected object');return o:GetFullName() end
local function find(name)
    local value=StaticFindObject(name:gsub('^[^ ]+ ',''))
    assert(valid(value),'Missing object: '..name);return value
end
local function text(value)
    if type(value)=='string' then return value end
    local result=value:ToString();assert(type(result)=='string','Invalid FName');return result
end
local function kind(p) return text(p:GetClass():GetFName()) end
local function finite(value)
    assert(type(value)=='number' and value==value and value>-math.huge and value<math.huge,'Non-finite building state')
    return value
end
local function integer(value,maximum,label)
    assert(type(value)=='number' and value%1==0 and value>=0 and value<=maximum,'Invalid '..label);return value
end
local function boolean(value) assert(type(value)=='boolean','Invalid building boolean');return value end
local function fields(class,wanted)
    local seen={}
    find(class):ForEachProperty(function(p)
        local name=text(p:GetFName());local spec=wanted[name]
        if spec then
            assert(kind(p)==spec[1],'Changed building field type: '..class..':'..name)
            if spec[2] then assert(path(p:GetStruct())=='ScriptStruct '..spec[2],'Changed building struct: '..name) end
            if spec.object then
                -- The pinned loader shares Bool/Object Lua metatables. A nil
                -- subclass accessor is allowed only with exact native kind
                -- above and actual mapped-object IsA checks at every use.
                local accessor=p.GetPropertyClass
                if accessor~=nil then
                    assert(type(accessor)=='function' and path(accessor(p))=='Class '..spec.object,'Changed building object class: '..name)
                end
            end
            seen[name]=true
        end
    end)
    for name in pairs(wanted)do assert(seen[name],'Missing building field: '..class..':'..name)end
end
local function signature(name,expected)
    local actual={};find(name):ForEachProperty(function(p)actual[#actual+1]=kind(p)end)
    assert(#actual==#expected,'Changed building getter: '..name)
    for i,value in ipairs(expected)do
        assert(('|'..value..'|'):find('|'..actual[i]..'|',1,true),'Changed building getter parameter: '..name)
    end
end
local metadata_types={
    '/Script/Dominion.GlobalBuildingManager','/Script/Dominion.BuildingSubsystem',
    '/Script/Dominion.BuildingPieceResolver','/Script/Dominion.LightweightBuildingPieceManager',
    '/Script/Dominion.BuildingPieceHandle','/Script/Dominion.LightweightBuildingPiece',
}
local function describe_property(property)
    local value={name=text(property:GetFName()),type=kind(property)}
    -- Parameter wrappers sometimes expose only FProperty, not its subclass
    -- metadata helpers. A missing helper is metadata, never an instance read.
    pcall(function()
        if value.type=='StructProperty' then value.struct=path(property:GetStruct())
        elseif value.type=='ObjectProperty' then value.class=path(property:GetPropertyClass()) end
    end)
    return value
end
local function metadata(name)
    local class=find(name);local result={name=path(class),properties={},functions={}}
    class:ForEachProperty(function(property)
        assert(#result.properties<256,'Building metadata property limit exceeded')
        result.properties[#result.properties+1]=describe_property(property)
    end)
    if result.name:sub(1,6)=='Class ' then
        class:ForEachFunction(function(fn)
            assert(#result.functions<256,'Building metadata function limit exceeded')
            local value={name=path(fn),parameters={}}
            fn:ForEachProperty(function(property)
                assert(#value.parameters<64,'Building metadata parameter limit exceeded')
                value.parameters[#value.parameters+1]=describe_property(property)
            end)
            result.functions[#result.functions+1]=value
        end)
    end
    return result
end
local function verify()
    fields('/Script/Dominion.GlobalBuildingManager',{
        BuildingPieces={'MapProperty'},
        LightweightBuildingPieceManager={'ObjectProperty',object='/Script/Dominion.LightweightBuildingPieceManager'},
    })
    -- The exact installed USMAP identifies this map as UInt32->BuildingPieceState.
    -- UE4SS has no exposed FMapProperty key/value metadata getters. The loader
    -- uses its actual FProperty descriptors for TMap access; every read value is
    -- type checked below and all nested struct schemas are checked here first.
    fields('/Script/Dominion.BuildingPieceState',{
        ClientVisible={'StructProperty','/Script/Dominion.ClientVisibleBuildingPieceState'},
    })
    fields('/Script/Dominion.ClientVisibleBuildingPieceState',{
        BuildingPieceDataIndex={'IntProperty'},Location={'StructProperty','/Script/CoreUObject.Vector'},
        Yaw={'FloatProperty'},bGhosted={'BoolProperty'},StabilityValue={'FloatProperty'},FractionalHealth={'FloatProperty'},
    })
    fields('/Script/Dominion.BuildingPieceData',{BuildingPieceDataIndex={'IntProperty'}})
    fields('/Script/Dominion.BuildingSettings',{
        ActiveCollisionProfileName={'StructProperty','/Script/Engine.CollisionProfileName'},
        InactiveCollisionProfileName={'StructProperty','/Script/Engine.CollisionProfileName'},
    })
    fields('/Script/Engine.CollisionProfileName',{Name={'NameProperty'}})
    local enum='ByteProperty|EnumProperty'
    signature('/Script/Engine.Actor:GetActorEnableCollision',{'BoolProperty'})
    signature('/Script/Engine.PrimitiveComponent:GetCollisionEnabled',{enum})
    signature('/Script/Engine.PrimitiveComponent:GetCollisionObjectType',{enum})
    signature('/Script/Engine.PrimitiveComponent:GetCollisionProfileName',{'NameProperty'})
    signature('/Script/Engine.PrimitiveComponent:GetCollisionResponseToChannel',{enum,enum})
    signature('/Script/Engine.InstancedStaticMeshComponent:GetInstanceCount',{'IntProperty'})
    fields('/Script/Engine.StaticMeshComponent',{StaticMesh={'ObjectProperty',object='/Script/Engine.StaticMesh'}})
end
local function keys(map,maximum)
    local count=integer(#map,maximum,'building count');local result,seen={},{}
    map:ForEach(function(k)
        local id=integer(k:get(),4294967295,'building ID')
        assert(not seen[id],'Duplicate building ID');seen[id]=true;result[#result+1]=id
        assert(#result<=maximum,'Building capture limit exceeded')
    end)
    assert(#result==count,'Building map changed while enumerating IDs');table.sort(result);return result
end
local function same_keys(a,b)
    if #a~=#b then return false end
    for i,id in ipairs(a)do if b[i]~=id then return false end end
    return true
end
local function manager(p,name)
    local object=find(name)
    assert(path(object:GetWorld())==p.world,'Building manager changed world')
    return object
end
local function primitive(component)
    local result={name=path(component),responses={}}
    result.enabled=integer(component:GetCollisionEnabled(),5,'building manager collision enabled')
    result.object_type=integer(component:GetCollisionObjectType(),31,'building manager object channel')
    result.profile=text(component:GetCollisionProfileName())
    for channel=0,31 do result.responses[channel+1]=integer(component:GetCollisionResponseToChannel(channel),2,'building manager channel response')end
    return result
end
local function primitive_key(value)
    return value.name..':'..value.enabled..':'..value.object_type..':'..value.profile..':'..table.concat(value.responses,',')
end
local function read_piece(p,m,id)
    local object=manager(p,m.name);local found=object.BuildingPieces:Find(id):get()
    local value=found.ClientVisible;local location=value.Location
    return {id=id,data_index=integer(value.BuildingPieceDataIndex,2147483647,'building data index'),
        location={finite(location.X),finite(location.Y),finite(location.Z)},yaw=finite(value.Yaw),
        ghosted=boolean(value.bGhosted),stability=finite(value.StabilityValue),health=finite(value.FractionalHealth)}
end
local function piece_key(value)
    return string.format('%u:%d:%.17g,%.17g,%.17g:%.17g:%s:%.17g:%.17g',value.id,value.data_index,
        value.location[1],value.location[2],value.location[3],value.yaw,tostring(value.ghosted),value.stability,value.health)
end
local function fail(p,why)
    p.result.errors[#p.result.errors+1]=tostring(why);p.result.complete=false
    p.result.elapsed_seconds=p.clock()-p.started;pending=nil;return p.result
end
function M.begin(pawn,options)
    options=options or{};assert(not pending,'Building capture already running')
    local p={clock=options.clock or os.time,maximum=options.max_pieces or 50000,managers={},assets={},hisms={},manager_index=1,index=1,
        hism_index=1,asset_index=1,metadata_index=1,phase='metadata',nativeReader=options.nativeReader==true,result={schema='S3BUILDINGSTATE1',complete=false,collision_verified=false,
            errors={},managers={},data_assets={},pieces={},
            building_hisms={},type_metadata={},type_metadata_errors={},
            unresolved='Built Mass entity collision override has not been verified. These are state descriptors, not collider records.'}}
    p.started=p.clock();pending=p
    local ok,why=pcall(function()
        assert(valid(pawn),'Missing player for building capture');p.world=path(pawn:GetWorld());p.pawn=path(pawn)
        p.result.world=p.world;p.result.pawn=p.pawn;verify()
        local cdo=find('/Script/Dominion.Default__BuildingSettings')
        p.result.settings={active_profile=text(cdo.ActiveCollisionProfileName.Name),inactive_profile=text(cdo.InactiveCollisionProfileName.Name)}
        assert(p.result.settings.active_profile~='' and p.result.settings.inactive_profile~='','Empty building profile setting')
        local class=find('/Script/Dominion.GlobalBuildingManager')
        for _,value in ipairs(FindAllOf('GlobalBuildingManager')or{})do
            if valid(value) and value:IsA(class) and not path(value):find('Default__',1,true)
                and valid(value:GetWorld()) and path(value:GetWorld())==p.world then
                local component=value.LightweightBuildingPieceManager
                assert(valid(component) and component:IsA(find('/Script/Dominion.LightweightBuildingPieceManager')),'Missing lightweight collision manager')
                assert(path(component:GetWorld())==p.world,'Foreign lightweight collision manager')
                local m={name=path(value),owner_collision=boolean(value:GetActorEnableCollision()),component=primitive(component)}
                m.ids=keys(value.BuildingPieces,p.maximum);m.count=#m.ids;m.keys={};m.records={}
                p.managers[#p.managers+1]=m
            end
        end
        assert(#p.managers<=1,'Multiple same-world global building managers')
        for _,value in ipairs(FindAllOf('BuildingHISMC')or{})do
            if valid(value) and not path(value):find('Default__',1,true)then p.hisms[#p.hisms+1]=path(value)end
        end
        table.sort(p.hisms)
        local seen={};local assetClass=find('/Script/Dominion.BuildingPieceData')
        for _,value in ipairs(FindAllOf('BuildingPieceData')or{})do
            if valid(value) and value:IsA(assetClass) then
                local name=path(value)
                if not name:find('Default__',1,true) and not name:find(' /Temp/',1,true) and not seen[name] then
                    seen[name]=true;p.assets[#p.assets+1]=name
                end
            end
        end
        table.sort(p.assets);p.asset_map={}
    end)
    if not ok then p.failure=tostring(why)end
    return true
end
function M.busy()return pending~=nil end
function M.cancel()
    if pending and pending.native_capture then pending.native_capture.cancel() end
    pending=nil
end
function M.advance(maxItems,budgetSeconds)
    local p=pending;if not p then return nil end
    if p.failure then return fail(p,p.failure)end
    if p.native_capture then
        local ok,result=pcall(p.native_capture.advance,maxItems,budgetSeconds)
        if not ok then return fail(p,result) end
        if result then pending=nil;return result end
        return nil
    end
    local started=p.clock();local count=0
    local ok,why=pcall(function()
        while count<(maxItems or 4)do
            if count>0 and budgetSeconds and p.clock()-started>=budgetSeconds then break end
            count=count+1
            local pawn=find(p.pawn);assert(path(pawn:GetWorld())==p.world,'Player left captured building world')
            if p.phase=='metadata'then
                local name=metadata_types[p.metadata_index]
                if not name then p.phase='hisms'
                else
                    local succeeded,result=pcall(metadata,name)
                    if succeeded then p.result.type_metadata[#p.result.type_metadata+1]=result
                    else p.result.type_metadata_errors[#p.result.type_metadata_errors+1]={name=name,error=tostring(result)} end
                    p.metadata_index=p.metadata_index+1
                end
            elseif p.phase=='hisms'then
                local name=p.hisms[p.hism_index]
                if not name then p.phase='assets'
                else
                    local c=find(name);local w=c:GetWorld()
                    local row={name=name,world=valid(w)and path(w)or nil,
                        passes_persistent_name_filter=name:find(':PersistentLevel.',1,true)~=nil}
                    row.same_world=row.world==p.world
                    if row.same_world then
                        local owner=c:GetOwner();local outer=c:GetOuter();local mesh=c.StaticMesh
                        row.owner=valid(owner)and path(owner)or nil;row.outer=valid(outer)and path(outer)or nil
                        row.owner_collision=valid(owner)and boolean(owner:GetActorEnableCollision())or false
                        if valid(mesh)then assert(mesh:IsA(find('/Script/Engine.StaticMesh')),'Changed building HISM mesh class')end
                        row.mesh=valid(mesh)and path(mesh)or nil
                        row.instance_count=integer(c:GetInstanceCount(),p.maximum,'building HISM instance count')
                        row.component=primitive(c)
                    end
                    p.result.building_hisms[#p.result.building_hisms+1]=row;p.hism_index=p.hism_index+1
                end
            elseif p.phase=='assets'then
                local name=p.assets[p.asset_index]
                if not name then p.phase='pieces'
                else
                    local asset=find(name);local index=asset.BuildingPieceDataIndex
                    assert(type(index)=='number'and index%1==0 and index>=-1,'Invalid building data index')
                    if index>=0 then
                        integer(index,2147483647,'building data index')
                        assert(not p.asset_map[index] or p.asset_map[index]==name,'Duplicate building data index')
                        p.asset_map[index]=name
                    end
                    p.asset_index=p.asset_index+1
                end
            elseif p.phase=='pieces'or p.phase=='verify'then
                local m=p.managers[p.manager_index]
                if not m then
                    if p.phase=='pieces'then p.phase='verify';p.manager_index=1;p.index=1
                    else p.phase='done';break end
                else
                    local id=m.ids[p.index]
                    if id then
                        local value=read_piece(p,m,id);local key=piece_key(value)
                        assert(p.asset_map[value.data_index],'Unresolved runtime building data index '..value.data_index)
                        assert(find(p.asset_map[value.data_index]).BuildingPieceDataIndex==value.data_index,'Building data index changed during capture')
                        if p.phase=='pieces'then
                            value.data_asset=p.asset_map[value.data_index];value.manager=m.name
                            m.records[#m.records+1]=value;m.keys[id]=key
                        else assert(m.keys[id]==key,'Building state changed during capture')end
                        p.index=p.index+1
                    else
                        local current=manager(p,m.name)
                        assert(same_keys(m.ids,keys(current.BuildingPieces,p.maximum)),'Building IDs changed during capture')
                        assert(boolean(current:GetActorEnableCollision())==m.owner_collision and
                            primitive_key(primitive(current.LightweightBuildingPieceManager))==primitive_key(m.component),
                            'Building manager collision policy changed during capture')
                        p.manager_index=p.manager_index+1;p.index=1
                    end
                end
            else break end
        end
    end)
    if not ok then return fail(p,why)end
    if p.phase~='done'then return nil end
    for _,m in ipairs(p.managers)do
        p.result.managers[#p.result.managers+1]={name=m.name,count=m.count,owner_collision=m.owner_collision,component=m.component}
        for _,value in ipairs(m.records)do p.result.pieces[#p.result.pieces+1]=value end
    end
    for index,name in pairs(p.asset_map)do p.result.data_assets[#p.result.data_assets+1]={index=index,name=name}end
    table.sort(p.result.data_assets,function(a,b)return a.index<b.index end)
    p.result.complete=true;p.result.elapsed_seconds=p.clock()-p.started
    if p.nativeReader then
        local ok,why=pcall(function()
            p.native_capture=require('building_native_capture')
            p.native_capture.begin(find(p.pawn),p.result,{clock=p.clock,max_pieces=p.maximum})
        end)
        if not ok then return fail(p,why)end
        return nil
    end
    pending=nil;return p.result
end
return M
