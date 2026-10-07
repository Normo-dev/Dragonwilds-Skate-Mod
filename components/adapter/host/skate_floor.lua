-- Entry only: read CharacterMovement's own current walking support before
-- disabling the capsule. No visibility ray, input, alias, or movement writes.
-- CurrentFloor is engine-maintained while walking. Require grounded movement
-- and bound its contact to the present capsule to reject distant/stale support.
local M={}
local verified=false
local function valid(o)return o and o:IsValid()end
local function finite(v)return type(v)=='number'and v==v and math.abs(v)<math.huge end
local function vector(v)
    assert(v and finite(v.X)and finite(v.Y)and finite(v.Z),'Invalid floor vector')
    return {X=v.X,Y=v.Y,Z=v.Z}
end
local function object(path)
    local value=StaticFindObject(path);assert(valid(value),'Floor API unavailable: '..path);return value
end
local function kind(p)return p:GetClass():GetFName():ToString()end
local function signature(path,expected)
    local actual={};object(path):ForEachProperty(function(p)actual[#actual+1]=p end)
    assert(#actual==#expected,'Changed floor API: '..path)
    for i,want in ipairs(expected)do
        local p=actual[i]
        assert(p:GetFName():ToString()==want[1]and kind(p)==want[2], 'Changed floor parameter: '..path)
        if want[3]then assert(p:GetStruct():GetFullName()=='ScriptStruct '..want[3],'Changed floor struct')end
    end
end
local function fields(path,expected)
    local seen={}
    object(path):ForEachProperty(function(p)
        local n=p:GetFName():ToString();local want=expected[n]
        if want then
            assert(kind(p)==want[1],'Changed floor field: '..n)
            if want[2]then assert(p:GetStruct():GetFullName()=='ScriptStruct '..want[2],'Changed floor field struct')end
            seen[n]=p
        end
    end)
    for n in pairs(expected)do assert(seen[n],'Missing floor field: '..n)end
    return seen
end
local function flag(value,name)
    local result=value[name];assert(type(result)=='boolean','Invalid native floor flag: '..name)
    return result
end
local function verify()
    if verified then return end
    signature('/Script/Engine.NavMovementComponent:IsMovingOnGround',{{'ReturnValue','BoolProperty'}})
    for _,method in ipairs({'GetScaledCapsuleHalfHeight','GetScaledCapsuleRadius'})do
        signature('/Script/Engine.CapsuleComponent:'..method,{{'ReturnValue','FloatProperty'}})
    end
    fields('/Script/Engine.CharacterMovementComponent',{
        MaxStepHeight={'FloatProperty'},CurrentFloor={'StructProperty','/Script/Engine.FindFloorResult'}})
    fields('/Script/Engine.FindFloorResult',{
        bBlockingHit={'BoolProperty'},bWalkableFloor={'BoolProperty'},bLineTrace={'BoolProperty'},
        FloorDist={'FloatProperty'},LineDist={'FloatProperty'},HitResult={'StructProperty','/Script/Engine.HitResult'}})
    fields('/Script/Engine.HitResult',{
        bBlockingHit={'BoolProperty'},bStartPenetrating={'BoolProperty'},
        ImpactPoint={'StructProperty','/Script/Engine.Vector_NetQuantize'},
        ImpactNormal={'StructProperty','/Script/Engine.Vector_NetQuantizeNormal'}})
    verified=true
end

function M.find(pawn)
    local status={schema='S3ENTRYFLOOR1',ready=false,method='native_current_floor',captured_at_unix=os.time()}
    local hit
    local okay,why=pcall(function()
        verify();assert(valid(pawn),'Player unavailable')
        local movement=pawn.CharacterMovement;local capsule=pawn.CapsuleComponent
        assert(valid(movement)and valid(capsule),'Character capsule or movement unavailable')
        if movement:IsMovingOnGround()~=true then status.reason='not_grounded';return end
        local pos=vector(pawn:K2_GetActorLocation())
        status.pawn_position={pos.X,pos.Y,pos.Z}
        local half=capsule:GetScaledCapsuleHalfHeight();local radius=capsule:GetScaledCapsuleRadius()
        local step=movement.MaxStepHeight
        assert(finite(half)and half>0 and half<=10000 and finite(radius)and radius>0 and radius<=half,
            'Invalid native capsule size')
        assert(finite(step)and step>=0 and step<=10000,'Invalid native step height')
        status.feet={pos.X,pos.Y,pos.Z-half};status.halfheight=half;status.radius=radius
        -- Ordinary property Get returns a mapped UScriptStruct. UE4SS applies
        -- each BoolProperty mask internally, including nested HitResult flags.
        -- Its out-table converter does not, and its BoolProperty Lua metatable
        -- collides with ObjectProperty; neither path is used here.
        local floor=assert(movement.CurrentFloor,'Native current floor unavailable')
        local blocking=flag(floor,'bBlockingHit')
        local walkable=flag(floor,'bWalkableFloor')
        local lineTrace=flag(floor,'bLineTrace')
        status.flag_encoding='mapped_property_get';status.line_trace=lineTrace
        if not blocking or not walkable then status.reason='no_walkable_floor';return end
        local distance=lineTrace and floor.LineDist or floor.FloorDist
        assert(finite(distance),'Invalid native floor distance');status.floor_distance=distance
        local result=floor.HitResult
        assert(result,'Native floor hit unavailable')
        if not flag(result,'bBlockingHit')or flag(result,'bStartPenetrating')then
            status.reason='penetrating_floor';return
        end
        local point=vector(result.ImpactPoint);local normal=vector(result.ImpactNormal)
        status.impact_point={point.X,point.Y,point.Z};status.impact_normal={normal.X,normal.Y,normal.Z}
        local componentOkay,componentName=pcall(function()
            local component=result.Component and result.Component:Get()
            if valid(component)then return component:GetFullName()end
        end)
        if componentOkay then status.component=componentName end
        -- Bound the native result to this capsule. A lower surface several metres
        -- away or an overhead visibility blocker must never become an entry spawn.
        local dx,dy=point.X-pos.X,point.Y-pos.Y
        if distance< -5 or distance>step+5 or normal.Z<=0 or
            dx*dx+dy*dy>(radius+5)^2 or math.abs(point.Z-status.feet[3])>step+radius+5 then
            status.reason='floor_outside_capsule_support';return
        end
        hit={ImpactPoint=point,ImpactNormal=normal};status.ready=true
    end)
    if not okay then status.reason='floor_query_unavailable';status.error=tostring(why)end
    return hit,status
end
return M
