-- Read-only provenance for authored no-mount zones. The native query uses the
-- same cached polygon and optional height bounds as ZoneSubsystem entry/exit.
-- It never edits tags, invokes overlap events, or infers membership from bounds.
local M={}
local zonePath='/Script/Dominion.ZoneSplineComponent'
local ownerPath='/Game/Gameplay/Events/BP_NoMount.BP_NoMount_C'
local blockedTag='Player.Status.MountBlocked'
local maxZones,maxTags=512,64
local function valid(o)
    if o==nil then return false end
    local ok,v=pcall(function()return o:IsValid()end);return ok and v==true
end
local function full(o)assert(valid(o),'Zone object unavailable');return o:GetFullName()end
local function object(path)
    local o=StaticFindObject(path);assert(valid(o),'Missing zone API: '..path);return o
end
local function kind(p)return p:GetClass():GetFName():ToString()end
local function structure(p,path)
    assert(kind(p)=='StructProperty'and full(p:GetStruct())=='ScriptStruct '..path,
        'Changed zone struct: '..path)
end
local function signature(path,expected)
    local actual={};object(path):ForEachProperty(function(p)actual[#actual+1]=p end)
    assert(#actual==#expected,'Changed zone API parameters: '..path)
    for i,spec in ipairs(expected)do
        assert(kind(actual[i])==spec[1],'Changed zone API parameter: '..path)
        if spec[2]then structure(actual[i],spec[2])end
    end
end
local function field(path,fieldName,expected,structPath,array)
    local found=false
    object(path):ForEachProperty(function(p)
        if p:GetFName():ToString()~=fieldName then return end
        assert(not found and kind(p)==expected,'Changed zone field: '..fieldName)
        if array then p=p:GetInner()end
        if structPath then structure(p,structPath)end
        found=true
    end)
    assert(found,'Missing zone field: '..fieldName)
end
local verified=false
local function verify()
    if verified then return end
    signature(zonePath..':IsLocationInside',{{'StructProperty','/Script/CoreUObject.Vector'},{'BoolProperty'}})
    signature('/Script/Engine.Actor:K2_GetActorLocation',{{'StructProperty','/Script/CoreUObject.Vector'}})
    signature('/Script/Engine.ActorComponent:GetOwner',{{'ObjectProperty'}})
    field(zonePath,'ZoneTags','StructProperty','/Script/GameplayTags.GameplayTagContainer')
    field('/Script/GameplayTags.GameplayTagContainer','GameplayTags','ArrayProperty','/Script/GameplayTags.GameplayTag',true)
    field('/Script/GameplayTags.GameplayTag','TagName','NameProperty')
    for _,axis in ipairs({'X','Y','Z'})do field('/Script/CoreUObject.Vector',axis,'DoubleProperty')end
    verified=true
end
local function each(values,limit,callback)
    assert(values~=nil and #values<=limit,'Zone array limit exceeded')
    local count=0
    local function visit(value)
        count=count+1;assert(count<=limit,'Zone array limit exceeded');callback(value)
    end
    if type(values)=='table'and type(values.ForEach)~='function'then
        for _,value in ipairs(values)do visit(value)end
    else values:ForEach(function(_,value)visit(value:get())end)end
end
local function has_blocker(zone)
    local seen,blocked={},false
    each(zone.ZoneTags.GameplayTags,maxTags,function(value)
        local text=value.TagName:ToString()
        assert(type(text)=='string'and text~=''and text~='None'and not seen[text],'Invalid zone tag')
        seen[text]=true;if text==blockedTag then blocked=true end
    end)
    return blocked
end
function M.inspect(pawn)
    local report={schema='DWSMOUNTZONE1',read_only=true,ready=false,mount_blocked_count=0,zones={}}
    local ok,why=pcall(function()
        verify()
        local playerClass=object('/Script/Dominion.DominionPlayerCharacter')
        local zoneClass=object(zonePath)
        assert(valid(pawn)and pawn:IsA(playerClass),'Playable character unavailable for zone query')
        local world=full(pawn:GetWorld());report.world=world;report.pawn=full(pawn)
        local source=pawn:K2_GetActorLocation();local location={X=source.X,Y=source.Y,Z=source.Z}
        for _,value in pairs(location)do
            assert(type(value)=='number'and value==value and math.abs(value)<math.huge,'Invalid zone query position')
        end
        report.position=location
        local zones=FindAllOf('ZoneSplineComponent')or{}
        assert(type(zones)=='table'and #zones<=maxZones,'Zone component discovery limit exceeded')
        local seen={};report.scanned=0
        for _,zone in ipairs(zones)do
            assert(valid(zone)and zone:IsA(zoneClass),'Unexpected zone component type')
            local zoneName=full(zone)
            assert(not seen[zoneName],'Duplicate zone component');seen[zoneName]=true
            -- Class defaults and other loaded worlds have no relevance to this pawn.
            local zoneWorld=zone:GetWorld()
            if valid(zoneWorld)and full(zoneWorld)==world then
                report.scanned=report.scanned+1
                if has_blocker(zone)then
                    local inside=zone:IsLocationInside(location)
                    assert(type(inside)=='boolean','Invalid native zone containment result')
                    if inside then
                        local owner=zone:GetOwner();assert(valid(owner),'Containing zone owner unavailable')
                        local ownerClass=full(owner:GetClass())
                        assert(full(owner:GetWorld())==world,'Containing zone owner world mismatch')
                        assert(ownerClass=='BlueprintGeneratedClass '..ownerPath,
                            'Unrecognized containing mount-blocking zone owner: '..ownerClass)
                        report.zones[#report.zones+1]={component=zoneName,owner=full(owner),
                            owner_class=ownerClass,tag=blockedTag,inside=true,query='IsLocationInside'}
                        report.mount_blocked_count=report.mount_blocked_count+1
                    end
                end
            end
        end
        report.ready=true
    end)
    if not ok then report.ready=false;report.error=tostring(why)end
    return report
end
return M
