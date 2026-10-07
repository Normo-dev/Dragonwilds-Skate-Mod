-- Lease only the native spell-wheel admission flag on configured gameplay
-- modes. Shared assets outlive pawns/controllers; retain plain identities only.
local M={}
local controllerClass='/Script/Dominion.DominionPlayerController'
local modeClass='/Script/Dominion.DominionInputMode'
local flag='bIsSpellcastingMenuAllowedToOpen'
local fields={'GameplayInputMode','GameplayLockOnTargetingInputMode','MountedInputMode'}
local state={items={}}
local verified=false
local last_error
local function valid(o)return o~=nil and o:IsValid()end
local function name(o)return o:GetFullName()end
local function address(o)
    local v=o:GetAddress()
    assert(type(v)=='number'and v%1==0 and v>0 and v<=9007199254740991,'Invalid spell mode object address')
    return v
end
local function find(path)return StaticFindObject(path:gsub('^[^ ]+ ',''))end
local function required(path)
    local o=find(path);assert(valid(o),'Spell wheel API unavailable: '..path);return o
end
local function verify()
    if verified then return end
    local wanted={};for _,field in ipairs(fields)do wanted[field]=true end
    required(controllerClass):ForEachProperty(function(p)
        local field=p:GetFName():ToString()
        if wanted[field]then
            assert(p:GetClass():GetFName():ToString()=='ObjectProperty','Changed spell mode property: '..field)
            -- Pinned UE4SS shares Bool/ObjectProperty metatables. A missing
            -- subclass accessor requires the actual mapped object's IsA below.
            -- An existing accessor must still pass; its errors never fall back.
            local accessor=p.GetPropertyClass
            if accessor~=nil then
                assert(type(accessor)=='function'and name(accessor(p))=='Class '..modeClass,
                    'Changed spell mode declared class: '..field)
            end
            wanted[field]=nil
        end
    end)
    assert(next(wanted)==nil,'Configured spell mode fields unavailable')
    local found=false
    required(modeClass):ForEachProperty(function(p)
        if p:GetFName():ToString()==flag then
            assert(p:GetClass():GetFName():ToString()=='BoolProperty','Changed spell wheel flag type');found=true
        end
    end)
    assert(found,'Spell wheel flag unavailable');verified=true
end
local function identity(o,class)
    assert(valid(o)and o:IsA(required(class)),'Unexpected spell wheel object class')
    return {name=name(o),address=address(o)}
end
local function matches(a,b)return a.name==b.name and a.address==b.address end
local function resolve(item)
    local o=find(item.name)
    -- Successful lookup proving destruction/replacement relinquishes the old
    -- claim. Lookup/reflection errors throw and retain it for later cleanup.
    if not valid(o)or name(o)~=item.name or address(o)~=item.address then return nil end
    assert(o:IsA(required(modeClass)),'Owned spell mode class changed');return o
end
local function bool(o)
    local v=o[flag];assert(type(v)=='boolean','Spell wheel flag is not boolean');return v
end
local function snapshot(pc)
    local result,seen={},{}
    for _,field in ipairs(fields)do
        local o=pc[field];local item=identity(o,modeClass)
        local key=item.name..':'..tostring(item.address)
        if not seen[key]then
            item.original=bool(o);result[#result+1]=item;seen[key]=true
        end
    end
    return result
end
function M.pending()
    for _,item in ipairs(state.items)do if item.owned then return true end end
    return false
end
function M.status()
    local count=0;for _,item in ipairs(state.items)do if item.owned then count=count+1 end end
    return {active=state.active==true,pending=count>0,owned_count=count,ownership_lost=state.lost==true,error=last_error}
end
function M.shutdown()
    state.active=false
    local errors={}
    for _,item in ipairs(state.items)do
        if item.owned then
            local ok,why=pcall(function()
                local o=resolve(item)
                if not o then item.owned=false;return end
                if bool(o)==true then item.owned=false;return end
                -- Only a matching object still holding our false value is
                -- restored. A failed/no-op setter retains this cleanup debt.
                o[flag]=true
                assert(bool(o)==true,'Spell wheel restoration did not apply')
                item.owned=false
            end)
            if not ok then errors[#errors+1]=tostring(why)end
        end
    end
    if M.pending()then last_error=table.concat(errors,'; ');return false,last_error end
    state={items={}};last_error=nil;return true
end
function M.acquire(pc)
    local ok,why=pcall(function()
        verify();local owner=identity(pc,controllerClass)
        if state.controller and not matches(owner,state.controller)then
            local restored,error=M.shutdown();assert(restored,error)
        end
        assert(not state.lost,'Spell wheel lease lost ownership; release before reacquiring')
        if state.controller and not state.active then
            local restored,error=M.shutdown();assert(restored,error)
        end
        local items=snapshot(pc)
        if state.active then
            assert(#items==#state.items,'Configured spell modes changed during lease')
            for i,item in ipairs(items)do
                assert(matches(item,state.items[i]),'Configured spell mode identity changed during lease')
                if item.original~=false then
                    state.items[i].owned=false;state.active=false;state.lost=true
                    error('Spell wheel lease lost its blocked flag')
                end
            end
            return
        end
        state={controller=owner,items=items,active=false}
        for _,item in ipairs(items)do
            if item.original then
                local o=resolve(item);assert(o and bool(o)==true,'Spell mode changed before acquisition')
                -- Record before calling a setter: it may write and then throw.
                item.owned=true;o[flag]=false
                assert(bool(o)==false,'Spell wheel block did not apply')
            end
        end
        assert(matches(identity(pc,controllerClass),owner),'Spell controller changed during acquisition')
        state.active=true;last_error=nil
    end)
    if ok then return true end
    local reason=tostring(why)
    if not state.lost then
        local restored,cleanup=M.shutdown()
        if not restored then reason=reason..'; cleanup pending: '..tostring(cleanup)end
    end
    last_error=reason;return false,reason
end
local previous=rawget(_G,'__DragonwildsSkateSpellWheelBlock')
if previous~=nil then
    assert(type(previous)=='table'and type(previous.shutdown)=='function','Previous spell wheel lease is invalid')
    local ok,restored,why=pcall(previous.shutdown)
    assert(ok and restored,'Previous spell wheel cleanup failed: '..tostring(ok and why or restored))
end
_G.__DragonwildsSkateSpellWheelBlock=M
return M
