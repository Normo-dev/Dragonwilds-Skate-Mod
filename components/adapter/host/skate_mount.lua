-- A distinct tile in Dragonwilds' existing Mounts list. Native save data never
-- receives this transient asset: only the mod's sidecar records its selection.
-- Call ensure on the existing engine tick; retain names, not UObject wrappers.
local M={}
local state={ready=false,last_attempt=-math.huge,last_refresh=-math.huge,selected=false}
local hooks={}
local base='/Script/Dominion.DominionMountComponent:'
local function mark(stage)
    state.stage=stage
    if state.trace then pcall(state.trace,stage)end
end
local function valid(o)
    if o==nil then return false end
    local ok,v=pcall(function()return o:IsValid()end);return ok and v
end
local function name(o)if valid(o)then return o:GetFullName()end end
local function find(n)
    if not n then return end
    local o=StaticFindObject((n:gsub('^[^ ]+ ','')));if valid(o)then return o end
end
local function same(o,n)return n~=nil and valid(o)and name(o)==n end
local function own_world(o,w)
    local ok,v=pcall(function()return name(o:GetWorld())end);return ok and v==w
end
local function signature(path,types)
    local fn=StaticFindObject(path);assert(valid(fn),'Missing reflected function: '..path)
    local actual={};fn:ForEachProperty(function(p)actual[#actual+1]=p:GetClass():GetFName():ToString()end)
    assert(#actual==#types,'Changed signature: '..path)
    for i,t in ipairs(types)do assert(actual[i]==t,'Changed parameter type: '..path)end
end
local function change_selection(selected)
    if state.selected~=selected then
        state.selected=selected;state.selection_dirty=true;state.ui_dirty=true
        state.selections=(state.selections or 0)+1
    end
end
local function hook(path,pre,post)
    mark('hook:RegisterHook('..path..')')
    local a,b=RegisterHook(path,pre,post)
    assert(type(a)=='number'and type(b)=='number','Hook unavailable: '..path)
    hooks[#hooks+1]={path,a,b}
end
local function install_hooks()
    for _,method in ipairs({'ServerSetEquippedMount','SetEquippedMount'})do
        local stack={}
        hook(base..method,function(context,argument)
            local intercepted=false;local owns_argument=false
            local ok,why=pcall(function()
                if not state.ready or not same(context:get(),state.component)then return end
                local wanted=argument:get()
                if same(wanted,state.asset)then
                    owns_argument=true
                    local component=find(state.component)
                    mark('equip:GetEquippedMountData')
                    local native=component:GetEquippedMountData()
                    state.native_equipped=name(native)
                    -- RemoteUnrealParam.set replaces this call's parameter before
                    -- native code runs. Never assign the transient asset to the save.
                    mark('equip:replace-own-argument')
                    argument:set(valid(native)and native or nil)
                    assert(not same(argument:get(),state.asset),'Native mount argument was not replaced')
                    intercepted=true;state.intercept_depth=(state.intercept_depth or 0)+1
                    change_selection(true)
                elseif (state.intercept_depth or 0)==0 then
                    change_selection(false)
                end
            end)
            stack[#stack+1]=intercepted
            if not ok then
                -- A hook exception cannot cancel native code. The exact installed
                -- ObjectProperty handler also supports nullptr; try that independent
                -- value conversion if valid-UObject replacement unexpectedly fails.
                local cleared=true
                if owns_argument then cleared=pcall(function()argument:set(nil);assert(not valid(argument:get()))end)end
                state.error='Mount selection: '..tostring(why)..(cleared and''or'; PARAMETER CLEAR FAILED')
                state.disable_requested=true
            end
        end,function()
            local intercepted=table.remove(stack)
            if intercepted then
                state.intercept_depth=math.max(0,(state.intercept_depth or 1)-1)
                if method=='SetEquippedMount'then return true end
            end
        end)
    end
    for _,method in ipairs({'CanEquip','CanEquipWithReason','IsMountUnlocked','IsMountEquipped'})do
        hook(base..method,function()end,function(context,argument)
            local ok,result=pcall(function()
                if state.ready and same(context:get(),state.component)and same(argument:get(),state.asset)then
                    if method=='CanEquipWithReason'then return 0 end -- verified ECanEquipResponse.Allowed
                    if method=='IsMountEquipped'then return state.selected end
                    return true
                end
            end)
            if ok then return result end
            state.error='Mount tile query: '..tostring(result)
        end)
    end
end
local function verify_parameter_setter(component)
    mark('preflight:GetEquippedMountData')
    local native=component:GetEquippedMountData()
    assert(valid(native),'Equip an ordinary mount before enabling the skateboard menu adapter')
    state.native_equipped=name(native)
    local called,verified=false,false
    local path=base..'CanEquip'
    mark('preflight:RegisterHook(CanEquip)')
    local a,b=RegisterHook(path,function(context,argument)
        if not same(context:get(),name(component))then return end
        called=true
        local ok=pcall(function()
            assert(same(argument:get(),name(native)),'Unexpected preflight argument')
            argument:set(native) -- read-only query: argument remains the identical UObject
            assert(same(argument:get(),name(native)),'Parameter readback mismatch')
        end)
        verified=ok
    end,function()end)
    assert(type(a)=='number'and type(b)=='number','Parameter preflight hook unavailable')
    mark('preflight:CanEquip(unchanged-native-argument)')
    local ok,why=pcall(function()component:CanEquip(native)end)
    mark('preflight:UnregisterHook(CanEquip)')
    UnregisterHook(path,a,b)
    assert(ok,why);assert(called and verified,'ObjectProperty parameter replacement failed its live preflight')
end
local function remove_tile()
    local list,entry=find(state.list),find(state.entry)
    if list and entry then
        -- Let the native ListView remove its own selected entry. Do not inspect
        -- heterogeneous item data or SpellBookScreen.SelectedMountEntryData.
        mark('cleanup:BP_GetSelectedItem')
        if same(list:BP_GetSelectedItem(),state.entry)then
            local previous=find(state.previous_selection)
            if previous then mark('cleanup:BP_SetSelectedItem(previous)');list:BP_SetSelectedItem(previous)
            else mark('cleanup:BP_ClearSelection');list:BP_ClearSelection()end
        end
        mark('cleanup:RemoveItem(owned-entry)');list:RemoveItem(entry)
    end
end
function M.shutdown()
    state.ready=false
    local selection_ok=true
    if state.menu_selection_adapter then
        selection_ok=state.menu_selection_adapter.shutdown()
        state.menu_selection_adapter=nil
    end
    for _,h in ipairs(hooks)do pcall(UnregisterHook,h[1],h[2],h[3])end
    hooks={}
    local ok,why=pcall(remove_tile)
    if not ok then state.error='Mount tile cleanup: '..tostring(why)end
    return ok and selection_ok
end
local previous=rawget(_G,'__DragonwildsSkateMount')
if previous and previous.shutdown then previous.shutdown()end
_G.__DragonwildsSkateMount=M

local function discover_screen(world)
    mark('discover:FindAllOf(WBP_SpellBookScreen_C)')
    for _,screen in ipairs(FindAllOf('WBP_SpellBookScreen_C')or{})do
        if valid(screen)and own_world(screen,world)then
            -- Exact class/property verified by both completed live probes.
            mark('discover:SpellBookScreen.MountsList')
            if valid(screen.MountsList)then return screen end
        end
    end
end
local function make_tile(pawn,screen,component)
    local assetBase=StaticFindObject('/Script/Dominion.MountDataAsset')
    local itemClass=StaticFindObject('/Script/Dominion.MountSlotEntryData')
    assert(valid(assetBase)and valid(itemClass),'Mount tile classes unavailable')
    mark('template:GetEquippedMountData')
    local native=component:GetEquippedMountData()
    assert(valid(native)and native:IsA(assetBase),'Valid native mount template unavailable')
    local list=find(state.list)
    local itemTemplate
    mark('template:GetNumItems')
    local count=list:GetNumItems()
    assert(type(count)=='number'and count>=0 and count<=512,'Unexpected native mount list size')
    for index=0,count-1 do
        mark('template:GetItemAt('..index..')')
        local candidate=list:GetItemAt(index)
        if valid(candidate)and not same(candidate,state.entry)and candidate:IsA(itemClass)then
            itemTemplate=candidate;break
        end
    end
    assert(itemTemplate,'Native mount row template unavailable')
    local assetClass=native:GetClass()
    local rowClass=itemTemplate:GetClass()
    state.asset_template=name(native);state.entry_template=name(itemTemplate)
    -- The installed UE4SS SoftObjectProperty marshaller uses an incompatible
    -- FSoftObjectPtr prefix for this game. Never read or write any soft pointer
    -- through Lua, including icons. Native construction copies complete authored
    -- templates using the engine's own property implementations and layouts.
    -- Exact e3ba1016 LuaMod.cpp passes argument 8 to parameters.Template.
    mark('create:StaticConstructObject(native-mount-template)')
    local asset=StaticConstructObject(assetClass,pawn,FName('None'),0x40,0,false,false,native)
    assert(valid(asset),'Could not create transient skateboard mount')
    assert(name(asset:GetClass())==name(assetClass),'Mount template class changed')
    state.asset=name(asset)
    mark('create:owned-MountDataAsset-fields')
    asset.DisplayName=FText('Skateboard')
    asset.Description=FText('Ride the Dragonwilds Skate skateboard.')
    asset.CarryWeight=0
    mark('create:StaticConstructObject(native-row-template)')
    local item=StaticConstructObject(rowClass,screen,FName('None'),0x40,0,false,false,itemTemplate)
    assert(valid(item),'Could not create skateboard mount list entry')
    assert(name(item:GetClass())==name(rowClass),'Mount row template class changed')
    state.entry=name(item)
    mark('create:owned-MountSlotEntryData-fields')
    item.MountData=asset;item.DisplayName=FText('Skateboard')
    item.LockState=2;item.bIsEquipped=state.selected -- UnlockedAndEquippable, live verified enum
    mark('create:AddItem(owned-entry)');list:AddItem(item)
    mark('create:GetIndexForItem(owned-entry)')
    assert(list:GetIndexForItem(item)>=0,'Native list rejected skateboard entry')
end
local function install(pawn,pc)
    state.world=name(pawn:GetWorld());state.pawn=name(pawn);state.controller=name(pc)
    assert(own_world(pc,state.world),'Controller world mismatch')
    signature('/Script/Dominion.DominionPlayerCharacter:GetMountComponent',{'ObjectProperty'})
    signature(base..'GetEquippedMountData',{'ObjectProperty'})
    signature(base..'ServerSetEquippedMount',{'ObjectProperty'})
    signature(base..'SetEquippedMount',{'ObjectProperty','BoolProperty','BoolProperty'})
    for _,method in ipairs({'CanEquip','IsMountUnlocked','IsMountEquipped'})do
        signature(base..method,{'ObjectProperty','BoolProperty'})
    end
    signature(base..'CanEquipWithReason',{'ObjectProperty','EnumProperty'})
    for _,method in ipairs({'AddItem','RemoveItem','BP_SetSelectedItem'})do
        signature('/Script/UMG.ListView:'..method,{'ObjectProperty'})
    end
    signature('/Script/UMG.ListView:GetIndexForItem',{'ObjectProperty','IntProperty'})
    signature('/Script/UMG.ListView:GetItemAt',{'IntProperty','ObjectProperty'})
    signature('/Script/UMG.ListView:GetNumItems',{'IntProperty'})
    signature('/Script/UMG.ListView:BP_GetSelectedItem',{'ObjectProperty'})
    signature('/Script/UMG.ListView:BP_ClearSelection',{})
    signature('/Script/UMG.ListViewBase:RequestRefresh',{})
    mark('install:GetMountComponent')
    local component=pawn:GetMountComponent();assert(valid(component),'Mount component unavailable')
    local screen=discover_screen(state.world);assert(screen,'Mount equipment screen unavailable')
    verify_parameter_setter(component)
    state.component=name(component);state.screen=name(screen);state.list=name(screen.MountsList)
    mark('install:BP_GetSelectedItem')
    state.previous_selection=name(screen.MountsList:BP_GetSelectedItem())
    install_hooks()
    make_tile(pawn,screen,component)
    state.ready=true;state.error=nil;state.ui_dirty=true
end
function M.ensure(pawn,pc,options)
    options=options or {}
    state.trace=options.trace
    if not valid(pawn)or not valid(pc)then return false,M.status()end
    if state.disable_requested then
        M.shutdown();return false,M.status()
    end
    local world=name(pawn:GetWorld())
    if state.pawn and(state.pawn~=name(pawn)or state.world~=world)then
        M.shutdown();state={ready=false,last_attempt=-math.huge,last_refresh=-math.huge,selected=false}
        state.trace=options.trace
    end
    if state.ready and(not find(state.component)or not find(state.screen))then M.shutdown()end
    local now=options.clock and options.clock()or 0
    if not state.metadata_loaded then
        local ok,value=pcall(function()return options.load_selection and options.load_selection()end)
        state.metadata_loaded=true;if ok then state.selected=value==true else state.error=tostring(value)end
    end
    if not state.ready then
        if now-state.last_attempt<2 then return false,M.status()end
        state.last_attempt=now
        local ok,why=pcall(install,pawn,pc)
        if not ok then M.shutdown();state.error=tostring(why);return false,M.status()end
    end
    -- The authoritative input mode tells us when the player is in gameplay.
    -- Hidden mount rows need no texture writes, row discovery or preview reset.
    -- The first menu tick refreshes immediately; equipment hooks remain active.
    if options.gameplay_active~=true and now-state.last_refresh>=0.2 then
        state.last_refresh=now
        local ok,why=pcall(function()
            local list,screen,entry=find(state.list),find(state.screen),find(state.entry)
            mark('refresh:GetEquippedMountData')
            local native=find(state.component):GetEquippedMountData()
            assert(valid(native),'Native mount was unequipped; skateboard tile paused')
            state.native_equipped=name(native)
            if not entry or not find(state.asset)then
                make_tile(pawn,screen,find(state.component));entry=find(state.entry)
            else
                mark('refresh:GetIndexForItem(owned-entry)')
                if list:GetIndexForItem(entry)<0 then mark('refresh:AddItem(owned-entry)');list:AddItem(entry)end
            end
            -- Staged behind an explicit opt-in. The menu-only installed build
            -- keeps its existing behavior until the real click observer is proven.
            if options.equip_click_hook==true then
                mark('refresh:gated-equip-selection')
                local selection=require('mount_menu_selection');state.menu_selection_adapter=selection
                local changed,status=selection.update({world=state.world,screen=state.screen,list=state.list,
                    component=state.component,entry=state.entry,asset=state.asset,
                    choose=change_selection,selected=function()return state.selected end},now)
                state.equip_click=status
                assert(status.enabled and not status.error,status.error or'Equip observer unavailable')
                if changed then state.ui_dirty=true end
            elseif state.menu_selection_adapter then
                assert(state.menu_selection_adapter.shutdown(),'Equip observer cleanup failed')
                state.menu_selection_adapter=nil;state.equip_click=nil
            end
            mark('refresh:owned-entry.bIsEquipped')
            if entry.bIsEquipped~=state.selected then entry.bIsEquipped=state.selected;state.ui_dirty=true end
            if state.ui_dirty then mark('refresh:RequestRefresh');list:RequestRefresh();state.ui_dirty=false end
            if options.icon_path then
                state.icon=require('mount_icon').update(pawn,list,screen,entry,find(state.asset),options.icon_path,now)
            end
        end)
        if not ok then M.shutdown();state.error='Mount list refresh: '..tostring(why)end
    end
    if state.selection_dirty and options.save_selection then
        local ok,why=pcall(options.save_selection,state.selected)
        if ok then state.selection_dirty=false else state.error='Mount preference: '..tostring(why)end
    end
    return state.ready,M.status()
end
function M.selected()return state.ready and state.selected end
function M.consume_toggle()return false end -- menu-only validation; mount input routing follows independently
function M.status()
    return {adapter_revision='native_template_v1',ready=state.ready,error=state.error,selected=state.selected,selections=state.selections or 0,
        asset=state.asset,entry=state.entry,menu_only=true,stage=state.stage,icon=state.icon,equip_click=state.equip_click}
end
function M.diagnostics()
    -- Deliberately no engine calls. The previous heterogeneous list/selected-item/
    -- unlocked-struct inventory crashed in UE4SS's reflected object-property read.
    -- These are the names already obtained by the tested runtime operations.
    return {status=M.status(),native_equipped=state.native_equipped,owned_asset=state.asset,
        owned_entry=state.entry,inspection='tracked state only; no native inventory claim'}
end
return M
