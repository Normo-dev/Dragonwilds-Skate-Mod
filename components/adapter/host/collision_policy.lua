-- One-shot read-only policy capture for first-run collision preparation.
-- Returns authored_policy.generate-compatible data; the caller publishes it
-- only when complete==true. No file I/O, asset export, hooks or world mutation.
local M={}
local nativeComponents={
    'Engine.StaticMeshComponent','Engine.InstancedStaticMeshComponent',
    'Engine.HierarchicalInstancedStaticMeshComponent','Engine.HLODInstancedStaticMeshComponent',
    'Engine.SplineMeshComponent','Foliage.FoliageInstancedStaticMeshComponent',
    'Dominion.OreNodeSpawnComponent','Dominion.ProxyLightComponent','Dominion.InteractableFoliageISMComponent',
    'Dominion.FISMAudioComponent','Dominion.RuneEssenceNodeSpawnComponent',
    'Dominion.WitherEmitterStaticMesh','Dominion.BurningEmitterStaticMesh','Landscape.LandscapeMeshProxyComponent',
}
local enum='EnumProperty|ByteProperty'
local function valid(o)
    if o==nil then return false end
    local ok,value=pcall(function()return o:IsValid()end);return ok and value
end
local function name(o)if valid(o)then return o:GetFullName()end end
local function find(path)
    local o=StaticFindObject(path);assert(valid(o),'Missing reflected object: '..path);return o
end
local function text(value)
    if type(value)=='string'then return value end
    local result=value:ToString();assert(type(result)=='string','Expected a reflected name');return result
end
local function integer(value,maximum,label)
    assert(type(value)=='number'and value%1==0 and value>=0 and value<=maximum,'Invalid '..label)
    return value
end
local function matches(actual,expected)return ('|'..expected..'|'):find('|'..actual..'|',1,true)~=nil end
local function kind(property)return property:GetClass():GetFName():ToString()end
local function check_signature(path,types)
    local actual={}
    find(path):ForEachProperty(function(p)actual[#actual+1]=kind(p)end)
    assert(#actual==#types,'Changed collision getter signature: '..path)
    for index,expected in ipairs(types)do
        assert(matches(actual[index],expected),'Changed collision getter parameter: '..path)
    end
end
local function check_fields(path,wanted)
    local found={}
    find(path):ForEachProperty(function(p)
        local field=p:GetFName():ToString();local expected=wanted[field]
        if expected then
            assert(matches(kind(p),expected.type),'Changed collision field: '..path..':'..field)
            if expected.struct then
                local inner=p:GetInner()
                assert(kind(inner)=='StructProperty'and name(inner:GetStruct())=='ScriptStruct '..expected.struct,
                    'Changed collision array element: '..path..':'..field)
            end
            found[field]=true
        end
    end)
    for field in pairs(wanted)do assert(found[field],'Missing collision field: '..path..':'..field)end
end
local function verify_getters()
    check_signature('/Script/Engine.PrimitiveComponent:GetCollisionEnabled',{enum})
    check_signature('/Script/Engine.PrimitiveComponent:GetCollisionObjectType',{enum})
    check_signature('/Script/Engine.PrimitiveComponent:GetCollisionProfileName',{'NameProperty'})
    check_signature('/Script/Engine.PrimitiveComponent:GetCollisionResponseToChannel',{enum,enum})
    check_signature('/Script/Engine.Actor:GetActorEnableCollision',{'BoolProperty'})
end
local function primitive(object,label)
    assert(valid(object),'Invalid collision component: '..label)
    local result={name=name(object),responses={}}
    result.enabled=integer(object:GetCollisionEnabled(),5,label..' collision enabled')
    result.object_type=integer(object:GetCollisionObjectType(),31,label..' object channel')
    result.profile=text(object:GetCollisionProfileName())
    assert(result.profile~='','Empty collision profile: '..label)
    for channel=0,31 do
        result.responses[#result.responses+1]={channel=channel,
            response=integer(object:GetCollisionResponseToChannel(channel),2,label..' channel response')}
    end
    return result
end
local function profiles()
    check_fields('/Script/Engine.CollisionProfile',{
        Profiles={type='ArrayProperty',struct='/Script/Engine.CollisionResponseTemplate'},
    })
    check_fields('/Script/Engine.CollisionResponseTemplate',{
        Name={type='NameProperty'},CollisionEnabled={type=enum},ObjectTypeName={type='NameProperty'},
        CustomResponses={type='ArrayProperty',struct='/Script/Engine.ResponseChannel'},
    })
    check_fields('/Script/Engine.ResponseChannel',{Channel={type='NameProperty'},Response={type=enum}})
    local cdo=find('/Script/Engine.Default__CollisionProfile');local result={};local seen={}
    cdo.Profiles:ForEach(function(index,value)
        local p=value:get();local entry={index=index,name=text(p.Name),
            enabled=integer(p.CollisionEnabled,5,'profile collision enabled'),object_type=text(p.ObjectTypeName),responses={}}
        assert(entry.name~=''and not seen[entry.name],'Invalid/duplicate collision profile')
        seen[entry.name]=true
        p.CustomResponses:ForEach(function(_,value)
            local response=value:get()
            entry.responses[#entry.responses+1]={channel=text(response.Channel),
                response=integer(response.Response,2,'profile channel response')}
        end)
        result[#result+1]=entry
    end)
    assert(#result>0,'Collision profile definitions unavailable');return result
end
function M.capture(pawn)
    local result={schema='S3COLLISIONPOLICY1',complete=false,errors={},collision={components={}},
        expected_component_count=#nativeComponents}
    local function guarded(label,fn)
        local ok,value=pcall(fn)
        if ok then return value end
        result.errors[#result.errors+1]={where=label,error=tostring(value)}
    end
    local ready=guarded('collision policy prerequisites',function()
        assert(valid(pawn)and pawn:IsA(find('/Script/Dominion.DominionPlayerCharacter')),'Playable character unavailable')
        result.world=name(pawn:GetWorld());result.pawn=name(pawn)
        assert(result.world,'Playable world unavailable')
        verify_getters();return true
    end)
    if not ready then return result end
    result.collision.pawn_capsule=guarded('player capsule',function()
        local capsule=pawn.CapsuleComponent
        assert(valid(capsule)and capsule:IsA(find('/Script/Engine.CapsuleComponent')),'Player capsule unavailable')
        assert(name(capsule:GetWorld())==result.world,'Player capsule belongs to another world')
        local captured=primitive(capsule,'player capsule')
        assert(captured.enabled==1 or captured.enabled==3 or captured.enabled==5,'Player capsule query collision is disabled')
        return captured
    end)
    local captured=0
    for _,class in ipairs(nativeComponents)do
        local value=guarded(class..' CDO',function()
            local module,short=class:match('^([^.]+)%.(.+)$')
            return primitive(find('/Script/'..module..'.Default__'..short),class)
        end)
        if value then result.collision.components[class]=value;captured=captured+1 end
    end
    result.captured_component_count=captured
    result.collision.actor=guarded('Actor CDO',function()
        local cdo=find('/Script/Engine.Default__Actor');local enabled=cdo:GetActorEnableCollision()
        assert(type(enabled)=='boolean','Invalid actor collision default')
        return {name=name(cdo),enabled=enabled}
    end)
    result.collision.profiles=guarded('CollisionProfile CDO',profiles)
    result.physicsSettings=guarded('PhysicsSettings CDO',function()
        local settings=require('physics_settings').capture()
        local default=integer(settings.engine_default,3,'default shape complexity')
        assert(default>0,'Default shape complexity cannot itself use Default')
        local expected=({'CTF_UseSimpleAndComplex','CTF_UseSimpleAsComplex','CTF_UseComplexAsSimple'})[default]
        local witnessed=false
        for _,entry in ipairs(settings.enum_values or{})do
            if entry.value==default and entry.name:match('([^:]+)$')==expected then witnessed=true end
        end
        assert(witnessed,'Default shape complexity enum representation is unverified')
        assert(#(settings.errors or{})==0,'Physics settings reported reflection errors')
        return settings
    end)
    result.complete=#result.errors==0 and captured==#nativeComponents and result.collision.pawn_capsule~=nil
        and result.collision.actor~=nil and result.collision.profiles~=nil and result.physicsSettings~=nil
    return result
end
return M
