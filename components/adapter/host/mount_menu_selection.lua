-- Disabled unless skate_mount.ensure receives equip_click_hook=true. This
-- observes the real Equip button and stores a mod-side choice only. Native
-- unlocked/equipped mount data is never assigned, and no soft pointer is read.
local M={}
local state={clicks=0,rows={},last_discovery=-math.huge}
local clickPath='/Script/CommonUI.CommonButtonBase:HandleButtonClicked'
local function valid(o)
    if o==nil then return false end
    local ok,v=pcall(function()return o:IsValid()end);return ok and v
end
local function name(o)if valid(o)then return o:GetFullName()end end
local function find(path)
    if not path then return end
    local o=StaticFindObject((path:gsub('^[^ ]+ ','')));if valid(o)then return o end
end
local function same(o,path)return path~=nil and name(o)==path end
local function signature(path,types)
    local fn=StaticFindObject(path);assert(valid(fn),'Missing mount selection API: '..path)
    local actual={};fn:ForEachProperty(function(p)actual[#actual+1]=p:GetClass():GetFName():ToString()end)
    assert(#actual==#types,'Changed mount selection signature: '..path)
    for index,kind in ipairs(types)do assert(actual[index]==kind,'Changed mount selection parameter: '..path)end
end
local function verify()
    signature(clickPath,{})
    for _,method in ipairs({'IsInteractionEnabled','GetLocked'})do
        signature('/Script/CommonUI.CommonButtonBase:'..method,{'BoolProperty'})
    end
    signature('/Script/CommonUI.CommonButtonBase:SetIsLocked',{'BoolProperty'})
    signature('/Script/UMG.Widget:GetVisibility',{'EnumProperty'})
    signature('/Script/UMG.Widget:SetVisibility',{'EnumProperty'})
    signature('/Script/UMG.ListView:BP_GetSelectedItem',{'ObjectProperty'})
    signature('/Script/UMG.ListView:GetNumItems',{'IntProperty'})
    signature('/Script/UMG.ListView:GetItemAt',{'IntProperty','ObjectProperty'})
    signature('/Script/Dominion.DominionMountComponent:GetEquippedMountData',{'ObjectProperty'})
end
local function resolve()
    local config=assert(state.config,'Mount selection is not bound')
    local screen,list,component=find(config.screen),find(config.list),find(config.component)
    assert(screen and list and component,'Mount selection objects unavailable')
    local panel=screen.MountDetailsPanel;assert(valid(panel),'Mount details panel unavailable')
    local button=panel.EquipButton;assert(valid(button),'Mount Equip button unavailable')
    return config,list,component,button
end
local function is_row(item,class)
    class=class or StaticFindObject('/Script/Dominion.MountSlotEntryData')
    return valid(item)and valid(class)and item:IsA(class)
end
local function observe_click(context)
    local ok,why=pcall(function()
        local config,list,_,button=resolve()
        if not same(context:get(),name(button))then return end
        if not button:IsInteractionEnabled()or button:GetLocked()then return end
        local selected=list:BP_GetSelectedItem()
        if not is_row(selected)then return end
        local owns=same(selected,config.entry)
        if owns then
            -- This row may be recycled/replaced between engine ticks. Matching
            -- its current hard asset reference prevents accepting a stale tile.
            if not same(selected.MountData,config.asset)or selected.LockState~=2 then return end
        end
        state.clicks=state.clicks+1;state.last_clicked=name(selected)
        config.choose(owns,'equip_button')
    end)
    if not ok then state.click_error='Equip observer: '..tostring(why)end
    -- No return override. The native callback still rejects our locked transient
    -- asset, while ordinary mounts retain their complete native equip behavior.
end
local function visible_rows(config,now)
    if now-state.last_discovery>=2 then
        state.last_discovery=now;state.rows={}
        local class=StaticFindObject('/Script/Dominion.MountSlotBase')
        assert(valid(class),'Mount row widget class unavailable')
        for _,row in ipairs(FindAllOf('WBP_MountSlot_C')or{})do
            if valid(row)and row:IsA(class)and name(row:GetWorld())==config.world then
                state.rows[#state.rows+1]=name(row)
            end
        end
    end
    return state.rows
end
local function paint(selected,now)
    local config,list,component=resolve()
    local native=name(component:GetEquippedMountData())
    assert(native,'Native equipped mount unavailable')
    assert(native~=config.asset,'Transient skateboard entered native equipment unexpectedly')
    local changed=false
    local itemClass=StaticFindObject('/Script/Dominion.MountSlotEntryData')
    local count=list:GetNumItems();assert(type(count)=='number'and count>=0 and count<=512,'Unexpected mount row count')
    for index=0,count-1 do
        local item=list:GetItemAt(index)
        if is_row(item,itemClass)then
            local own=same(item,config.entry)
            local desired=selected and own or(not selected and not own and same(item.MountData,native))
            if item.bIsEquipped~=desired then item.bIsEquipped=desired;changed=true end
        end
    end
    if not selected and state.presentation_active then state.last_discovery=-math.huge end
    local rowCount=0
    for _,path in ipairs(visible_rows(config,now))do
        local row=find(path)
        if row and name(row:GetWorld())==config.world then
            local own=same(row.MountData,config.asset)
            local desired=selected and own or(not selected and not own and same(row.MountData,native))
            if row.bIsEquipped~=desired then row.bIsEquipped=desired;changed=true end
            local indicator=row.EquippedIndicator
            if valid(indicator)then
                -- HitTestInvisible / Collapsed: presentation only. This writes a
                -- hard Image widget, never the template asset's soft icon fields.
                local visibility=desired and 3 or 1
                if indicator:GetVisibility()~=visibility then indicator:SetVisibility(visibility);changed=true end
            end
            rowCount=rowCount+1
        end
    end
    state.visible_rows=rowCount;state.presentation_active=selected
    return changed
end
local function repair_owned_lock(now)
    local config=state.config;local changed=false;local badgeError
    local entry=find(config.entry)
    assert(is_row(entry)and same(entry.MountData,config.asset),'Owned mount entry changed')
    if entry.LockState~=2 then entry.LockState=2;changed=true end
    for _,path in ipairs(visible_rows(config,now))do
        local row=find(path)
        if row and name(row:GetWorld())==config.world then
            local owned=same(row.MountData,config.asset)
            if owned then
                if row.LockState~=2 then row.LockState=2;changed=true end
                if row:GetLocked()then row:SetIsLocked(false);changed=true end
                local icon=row.LockIcon
                if valid(icon)and icon:GetVisibility()~=1 then icon:SetVisibility(1);changed=true end
                state.badge_adapter=state.badge_adapter or require('mount_badge_layer')
            end
            if state.badge_adapter then
                local moved,why=state.badge_adapter.update(row,owned)
                if why then badgeError=why end
                if moved then changed=true end
            end
        end
    end
    state.badge_error=badgeError
    return changed
end
function M.shutdown()
    if state.hook then pcall(UnregisterHook,clickPath,state.hook[1],state.hook[2]);state.hook=nil end
    local ok,why=true,nil
    if state.presentation_active and state.config then ok,why=pcall(paint,false,state.now or 0)end
    if state.badge_adapter then
        local restored,errors=state.badge_adapter.shutdown()
        if not restored then state.badge_error=table.concat(errors,'; ');ok=false end
        state.badge_adapter=nil
    end
    if not ok then state.error='Mount checkmark cleanup: '..tostring(why or state.badge_error)end
    state.config=nil;state.presentation_active=false
    return ok
end
local previous=rawget(_G,'__DragonwildsSkateMountMenuSelection')
if previous and previous.shutdown then previous.shutdown()end
_G.__DragonwildsSkateMountMenuSelection=M

-- All config object references are plain full-name strings. choose/selected
-- are Lua callbacks operating only on the owning module's sidecar preference.
function M.update(config,now)
    local ok,changed=pcall(function()
        assert(type(config.choose)=='function'and type(config.selected)=='function','Mount selection callbacks unavailable')
        if state.config and(state.config.world~=config.world or state.config.screen~=config.screen)then
            assert(M.shutdown(),'Could not restore previous mount menu')
            state.rows={};state.last_discovery=-math.huge;state.native_equipped=nil
        end
        state.config=config;state.now=now
        if not state.hook then
            verify()
            local a,b=RegisterHook(clickPath,observe_click,function()end)
            assert(type(a)=='number'and type(b)=='number','Mount Equip observer unavailable')
            state.hook={a,b}
        end
        local _,_,component=resolve()
        local native=name(component:GetEquippedMountData())
        assert(native,'Native equipped mount unavailable')
        if state.native_equipped and native~=state.native_equipped and config.selected()then
            config.choose(false,'native_equipment_changed')
        end
        state.native_equipped=native
        local selected=config.selected()==true
        local repaired=repair_owned_lock(now)
        if selected or state.presentation_active then return paint(selected,now)or repaired end
        return repaired
    end)
    if not ok then state.error=tostring(changed);return false,M.status()end
    state.error=nil;return changed,M.status()
end
function M.status()
    return {enabled=state.hook~=nil,hook_path=clickPath,error=state.error,click_error=state.click_error,clicks=state.clicks,
        last_clicked=state.last_clicked,native_equipped=state.native_equipped,badge_error=state.badge_error,
        presentation_active=state.presentation_active==true,visible_rows=state.visible_rows or 0}
end
return M
