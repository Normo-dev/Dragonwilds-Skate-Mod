-- Guard-triggered, read-only ground diagnostics. No input, mutation, file I/O,
-- ticks, or retained UObject/FName references. The caller owns guard recovery.
local M={}
local function finite(v) return type(v)=='number' and v==v and math.abs(v)<math.huge end
local function valid(o) return o and o:IsValid() end
local function vector(v)
    if not v or not finite(v.X) or not finite(v.Y) or not finite(v.Z) then error('Non-finite or unavailable vector') end
    return {v.X,v.Y,v.Z}
end
local function attempt(result,label,fn)
    local ok,value=pcall(fn)
    if not ok then result.errors[#result.errors+1]={stage=label,error=tostring(value)} end
    return ok,value
end
local function signature(path,expected)
    local result={path=path,valid=false,parameters={}}
    local fn=StaticFindObject(path)
    if not valid(fn) then result.error='Reflected function unavailable';return result end
    fn:ForEachProperty(function(p)
        local item={name=p:GetFName():ToString(),type=p:GetClass():GetFName():ToString()}
        if item.type=='StructProperty' then item.struct=p:GetStruct():GetFullName() end
        if item.type=='ArrayProperty' then item.inner_type=p:GetInner():GetClass():GetFName():ToString() end
        result.parameters[#result.parameters+1]=item
    end)
    if #result.parameters~=#expected then result.error='Unexpected reflected parameter count';return result end
    for i,want in ipairs(expected) do
        local got=result.parameters[i]
        if got.name~=want[1] or got.type~=want[2] or
            (want[3] and got.struct~='ScriptStruct '..want[3]) or
            (want[4] and got.inner_type~=want[4]) then
            result.error='Unexpected reflected parameter '..i;return result
        end
    end
    result.valid=true
    return result
end

-- Epic's KismetSystemLibrary signature is checked against runtime reflection
-- before it can be called. GetStruct/GetInner are documented UE4SS Lua APIs.
-- https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/Engine/Kismet/UKismetSystemLibrary?application_version=5.5
local traceParameters={
    {'WorldContextObject','ObjectProperty'},
    {'Start','StructProperty','/Script/CoreUObject.Vector'},
    {'End','StructProperty','/Script/CoreUObject.Vector'},
    {'ProfileName','NameProperty'},
    {'bTraceComplex','BoolProperty'},
    {'ActorsToIgnore','ArrayProperty',nil,'ObjectProperty'},
    {'DrawDebugType','ByteProperty'},
    {'OutHit','StructProperty','/Script/Engine.HitResult'},
    {'bIgnoreSelf','BoolProperty'},
    {'TraceColor','StructProperty','/Script/CoreUObject.LinearColor'},
    {'TraceHitColor','StructProperty','/Script/CoreUObject.LinearColor'},
    {'DrawTime','FloatProperty'},
    {'ReturnValue','BoolProperty'},
}

local function copy_hit(out,hit)
    out.impact_point=vector(hit.ImpactPoint)
    out.impact_normal=vector(hit.ImpactNormal)
    out.normal=vector(hit.Normal)
    for name,key in pairs({bBlockingHit='blocking_hit',bStartPenetrating='start_penetrating'}) do
        if type(hit[name])=='boolean' then out[key]=hit[name] end
    end
    for name,key in pairs({Distance='distance',Time='time',PenetrationDepth='penetration_depth'}) do
        if finite(hit[name]) then out[key]=hit[name] end
    end
    -- FHitResult.Component is an FWeakObjectPtr in the installed UE4SS build.
    local component=hit.Component and hit.Component:Get()
    if valid(component) then out.component=component:GetFullName() end
end

function M.inspect(pawn,proposed,halfheight)
    local result={schema='S3GROUND1',errors={},checks={},capsule={},signatures={}}
    local okay=attempt(result,'input',function()
        if not valid(pawn) then error('Pawn unavailable') end
        result.pawn=pawn:GetFullName();result.proposed=vector(proposed)
        if not finite(halfheight) or halfheight<=0 or halfheight>10000 then error('Invalid capsule halfheight') end
        result.halfheight=halfheight;result.feet=proposed.Z-halfheight
    end)
    if not okay then return result end

    local capsule,profile
    local gotCapsule=attempt(result,'capsule',function()
        capsule=pawn.CapsuleComponent
        if not valid(capsule) then error('Pawn capsule unavailable') end
        result.capsule.name=capsule:GetFullName()
    end)
    if not gotCapsule then return result end

    attempt(result,'capsule_trace_complex_on_move',function()
        local class=StaticFindObject('/Script/Engine.PrimitiveComponent')
        if not valid(class) then error('PrimitiveComponent class unavailable') end
        local reflected=false
        class:ForEachProperty(function(p)
            if p:GetFName():ToString()=='bTraceComplexOnMove' then
                result.capsule.trace_complex_property=p:GetFullName()
                reflected=p:GetClass():GetFName():ToString()=='BoolProperty'
            end
        end)
        if not reflected then error('bTraceComplexOnMove BoolProperty unavailable') end
        local value=capsule.bTraceComplexOnMove
        if type(value)~='boolean' then error('bTraceComplexOnMove did not return a boolean') end
        result.capsule.trace_complex_on_move=value
    end)

    local gotProfile=attempt(result,'collision_profile',function()
        local layout=signature('/Script/Engine.PrimitiveComponent:GetCollisionProfileName',{{'ReturnValue','NameProperty'}})
        result.signatures.profile=layout
        if not layout.valid then error(layout.error) end
        profile=capsule:GetCollisionProfileName()
        result.capsule.profile_lua_type=type(profile)
        result.capsule.profile_name=profile:ToString()
        if type(result.capsule.profile_name)~='string' then error('Collision profile FName unavailable') end
    end)
    if not gotProfile then result.skipped='Capsule collision profile unavailable';return result end

    local checked=attempt(result,'profile_trace_signature',function()
        local layout=signature('/Script/Engine.KismetSystemLibrary:LineTraceSingleByProfile',traceParameters)
        result.signatures.trace=layout
        if not layout.valid then error(layout.error) end
    end)
    if not checked then result.skipped='Profile trace signature was not validated';return result end
    local sys
    local gotSystem=attempt(result,'kismet_system_library',function()
        sys=require('UEHelpers').GetKismetSystemLibrary()
        if not valid(sys) then error('KismetSystemLibrary default object unavailable') end
    end)
    if not gotSystem then return result end

    for _,spec in ipairs({{'profile_simple',200,false},{'profile_complex',200,true},{'profile_simple_near_feet',5,false}}) do
        local start={X=proposed.X,Y=proposed.Y,Z=result.feet+spec[2]}
        local finish={X=proposed.X,Y=proposed.Y,Z=result.feet-1000}
        local item={name=spec[1],trace_complex=spec[3],start=vector(start),finish=vector(finish)}
        result.checks[#result.checks+1]=item
        local called,why=pcall(function()
            if not valid(pawn) or not valid(capsule) or not valid(sys) then error('Trace target became unavailable') end
            local hit={};local color={R=0,G=0,B=0,A=0}
            local yes=sys:LineTraceSingleByProfile(pawn,start,finish,profile,spec[3],{pawn},0,hit,true,color,color,0)
            if type(yes)~='boolean' then error('Profile trace did not return a boolean') end
            item.hit=yes
            if yes then copy_hit(item,hit) end
        end)
        if not called then item.error=tostring(why) end
    end
    return result
end
return M
