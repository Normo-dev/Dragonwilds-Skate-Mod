-- Bounded read-only native-piece evidence. Actual entity profile verification
-- is a separate gate: transform/flags alone NEVER promote collision geometry.
local M={}
local pending
local function valid(o)return o and o:IsValid()end
local function path(o)assert(valid(o),'Building object unavailable');return o:GetFullName()end
local function find(name)
    local o=StaticFindObject(name:gsub('^[^ ]+ ',''));assert(valid(o),'Building object disappeared: '..name);return o
end
local function class(name)return find('/Script/Dominion.'..name)end
local function addr(o)
    local n=o:GetAddress();assert(type(n)=='number' and n%1==0 and n>0 and n<=9007199254740991,'Invalid building object address');return n
end
local function same(a,b)
    if #a~=#b then return false end
    for i,v in ipairs(a)do if b[i]~=v then return false end end
    return true
end
local function identity(p)
    local pawn=find(p.pawn);assert(addr(pawn)==p.pawn_address,'Building player identity changed')
    local world=pawn:GetWorld();assert(path(world)==p.world and addr(world)==p.world_address,'Building world identity changed')
    local owner=find(p.manager);assert(owner:IsA(class('GlobalBuildingManager')) and addr(owner)==p.manager_address,'Building owner identity changed')
    assert(path(owner:GetWorld())==p.world and addr(owner:GetWorld())==p.world_address,'Building owner changed world')
    local c=owner.LightweightBuildingPieceManager
    assert(valid(c) and c:IsA(class('LightweightBuildingPieceManager')) and addr(c)==p.component_address,'Building native manager identity changed')
    assert(path(c:GetWorld())==p.world and addr(c:GetWorld())==p.world_address,'Building native manager changed world')
    return pawn,owner,c
end
local function key(row)
    local t=row.transform;local q,v,s=t.Rotation,t.Translation,t.Scale3D
    return string.format('%u:%d:%d:%.17g,%.17g,%.17g,%.17g:%.17g,%.17g,%.17g:%.17g,%.17g,%.17g:%s:%s:%s:%s:%u',
        row.id,row.data_asset_address,row.derived_asset_address,q.X,q.Y,q.Z,q.W,v.X,v.Y,v.Z,s.X,s.Y,s.Z,
        tostring(row.preview),tostring(row.ghosted),tostring(row.can_defer_collision_update),tostring(row.collision_enabled),row.entity_count)
end
local function validate_inventory(p,owner,component)
    local expected={};for id in pairs(p.client)do expected[id]=true end
    local n=0;owner.BuildingPieces:ForEach(function(k)
        local id=k:get();assert(expected[id],'Client building inventory changed during native capture');expected[id]=nil;n=n+1
    end)
    assert(next(expected)==nil and n==#owner.BuildingPieces,'Client building inventory changed during native capture')
    local prior=p.result.managers[1]
    assert(owner:GetActorEnableCollision()==prior.owner_collision,'Building owner collision changed during native capture')
    assert(component:GetCollisionEnabled()==prior.component.enabled and component:GetCollisionObjectType()==prior.component.object_type,
        'Building manager query policy changed during native capture')
    local profile=component:GetCollisionProfileName();if type(profile)~='string'then profile=profile:ToString()end
    assert(profile==prior.component.profile,'Building manager profile changed during native capture')
    for c=0,31 do assert(component:GetCollisionResponseToChannel(c)==prior.component.responses[c+1],
        'Building manager response changed during native capture')end
    local saved=p.result.native_capture.mass_subsystem;local mass=find(saved.name)
    assert(mass:IsA(find('/Script/JagexMassEntity.JgxMassSubsystem')) and addr(mass)==saved.address
        and path(mass:GetWorld())==p.world and addr(mass:GetWorld())==p.world_address,'Mass subsystem changed during native capture')
end
local function fail(p,why)
    p.result.complete=false;p.result.collision_verified=false
    p.result.errors[#p.result.errors+1]=tostring(why);pending=nil;return p.result
end
function M.begin(pawn,result,options)
    assert(not pending,'Native building capture already running');options=options or{}
    assert(type(result)=='table' and result.schema=='S3BUILDINGSTATE1' and result.complete==true and #result.errors==0,'Native building capture needs a complete typed state inventory')
    assert(#result.managers==1,'Native building capture requires exactly one same-world manager')
    local world=pawn:GetWorld();local owner=find(result.managers[1].name);local c=owner.LightweightBuildingPieceManager
    local p={clock=options.clock or os.time,result=result,pawn=path(pawn),pawn_address=addr(pawn),world=path(world),world_address=addr(world),
        manager=path(owner),manager_address=addr(owner),component_address=addr(c),reader=require('building_native'),
        phase='assets',index=1,asset_map={},asset_names={},derived_map={},derived_names={},client={},row_keys={}}
    identity(p)
    for _,row in ipairs(result.pieces)do assert(not p.client[row.id],'Duplicate client building piece');p.client[row.id]=row end
    for _,row in ipairs(result.data_assets)do p.asset_names[#p.asset_names+1]=row end
    for _,o in ipairs(FindAllOf('BuildingPieceDerivedData')or{})do
        if valid(o) and o:IsA(class('BuildingPieceDerivedData')) then
            local name=path(o)
            if not name:find('Default__',1,true) and not name:find(' /Temp/',1,true) then p.derived_names[#p.derived_names+1]=name end
        end
    end
    table.sort(p.derived_names)
    result.native_pieces={};result.native_piece_ids={};result.native_missing_piece_ids={}
    result.native_capture={schema='S3BUILDINGNATIVE1',complete=false,collision_verified=false,
        policy_limit='Actual Mass entity body profiles are required; enabled/ghost flags alone do not prove the active profile.'}
    result.native_capture.manager_address=p.component_address
    local settings=find('/Script/Dominion.Default__BuildingSettings')
    local function profile(field,expected)
        local name=settings[field].Name
        assert(name:ToString()==expected,'Building profile settings changed before native capture')
        local index=name:GetComparisonIndex()
        assert(type(index)=='number' and index%1==0 and index>=0 and index<=4294967295,'Invalid building profile comparison index')
        return {name=expected,comparison_index=index}
    end
    result.native_capture.profile_names={active=profile('ActiveCollisionProfileName',result.settings.active_profile),
        inactive=profile('InactiveCollisionProfileName',result.settings.inactive_profile)}
    local configured=require('skate_config').game_exe_sha256
    if configured~=nil then
        assert(type(configured)=='string' and #configured==64 and configured:match('^[0-9a-fA-F]+$'),'Invalid configured game identity')
        result.native_capture.configured_game_exe_sha256=configured:lower()
    end
    local mass_class=find('/Script/JagexMassEntity.JgxMassSubsystem');local mass
    for _,o in ipairs(FindAllOf('JgxMassSubsystem')or{})do
        if valid(o) and o:IsA(mass_class) and not path(o):find('Default__',1,true) and valid(o:GetWorld())
            and path(o:GetWorld())==p.world and addr(o:GetWorld())==p.world_address then
            assert(not mass,'Multiple same-world Mass subsystems');mass=o
        end
    end
    assert(mass,'Same-world Mass subsystem unavailable')
    result.native_capture.mass_subsystem={name=path(mass),address=addr(mass)}
    pending=p;return true
end
function M.cancel()pending=nil end
function M.advance(maxItems,budgetSeconds)
    local p=pending;if not p then return end
    local started=p.clock();local count=0
    local ok,why=pcall(function()
        while count<(maxItems or 4)do
            if count>0 and budgetSeconds and p.clock()-started>=budgetSeconds then break end
            count=count+1;local _,owner,component=identity(p)
            if p.phase=='assets'then
                local r=p.asset_names[p.index]
                if not r then p.phase='derived';p.index=1
                else
                    local o=find(r.name);assert(o:IsA(class('BuildingPieceData')) and o.BuildingPieceDataIndex==r.index,'Building asset type/index changed')
                    local a=addr(o);assert(not p.asset_map[a],'Duplicate building asset pointer')
                    p.asset_map[a]={name=r.name,index=r.index};p.index=p.index+1
                end
            elseif p.phase=='derived'then
                local name=p.derived_names[p.index]
                if not name then p.phase='ids';p.index=1
                else
                    local o=find(name);assert(o:IsA(class('BuildingPieceDerivedData')),'Building derived type changed')
                    local a=addr(o);assert(not p.derived_map[a],'Duplicate building derived pointer')
                    p.derived_map[a]=name;p.index=p.index+1
                end
            elseif p.phase=='ids'then
                p.ids=p.reader.ids(component);p.result.native_piece_ids=p.ids
                local seen={};for _,id in ipairs(p.ids)do assert(p.client[id],'Native building absent from complete client inventory');seen[id]=true end
                for id in pairs(p.client)do if not seen[id]then p.result.native_missing_piece_ids[#p.result.native_missing_piece_ids+1]=id end end
                table.sort(p.result.native_missing_piece_ids);p.phase='pieces';p.index=1
            elseif p.phase=='pieces'or p.phase=='verify'then
                local ids={};for i=p.index,math.min(p.index+15,#p.ids)do ids[#ids+1]=p.ids[i]end
                if #ids==0 then
                    assert(same(p.ids,p.reader.ids(component)),'Native building inventory changed during capture')
                    validate_inventory(p,owner,component)
                    if p.phase=='pieces'then p.phase='verify';p.index=1
                    else p.phase='done';break end
                else
                    for _,r in ipairs(p.reader.read(component,ids))do
                        local a=p.asset_map[r.data_asset_address];local d=p.derived_map[r.derived_asset_address]
                        assert(a and d,'Native building asset pointers are not in the typed loaded inventory')
                        local ao,derived=find(a.name),find(d)
                        assert(ao:IsA(class('BuildingPieceData')) and addr(ao)==r.data_asset_address and ao.BuildingPieceDataIndex==a.index,'Native data asset identity/index changed')
                        assert(derived:IsA(class('BuildingPieceDerivedData')) and addr(derived)==r.derived_asset_address,'Native derived asset identity changed')
                        local current=owner.BuildingPieces:Find(r.id):get().ClientVisible
                        assert(current.BuildingPieceDataIndex==a.index and p.client[r.id].data_index==a.index,'Native/client building asset mismatch')
                        assert(current.bGhosted==r.ghosted,'Native/client building ghost state mismatch')
                        local k=key(r)
                        if p.phase=='pieces'then
                            p.row_keys[r.id]=k;r.data_asset=a.name;r.derived_asset=d;r.data_index=a.index
                            p.result.native_pieces[#p.result.native_pieces+1]=r
                        else assert(p.row_keys[r.id]==k,'Native building transform/flags/identity changed during capture')end
                    end
                    p.index=p.index+#ids
                end
            else break end
        end
    end)
    if not ok then return fail(p,why)end
    if p.phase~='done'then return nil end
    p.result.native_capture.complete=true;p.result.native_capture.piece_count=#p.ids
    p.result.unresolved='Actual native transforms and flags captured; Mass entity body profile verification is still required.'
    pending=nil;return p.result
end
return M
