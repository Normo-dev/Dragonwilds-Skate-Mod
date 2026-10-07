-- Lift only the existing owned mount's native Image above its artwork. All
-- UObject references retained between ticks are names; no brush/soft-ref copies.
local M={}
local records={};local verified=false
local function valid(o)return o~=nil and o:IsValid()end
local function name(o)if valid(o)then return o:GetFullName()end end
local function find(path)
    local o=StaticFindObject((path:gsub('^[^ ]+ ','')));if valid(o)then return o end
end
local function kind(p)return p:GetClass():GetFName():ToString()end
local function signature(path,expected)
    local fn=StaticFindObject(path);assert(valid(fn),'Missing badge API: '..path)
    local actual={};fn:ForEachProperty(function(p)actual[#actual+1]=kind(p)end)
    assert(#actual==#expected,'Changed badge API: '..path)
    for i,want in ipairs(expected)do assert(('|'..want..'|'):find('|'..actual[i]..'|',1,true),'Changed badge parameter: '..path)end
end
local function verify()
    if verified then return end
    signature('/Script/UMG.Widget:GetParent',{'ObjectProperty'})
    signature('/Script/UMG.PanelWidget:GetChildrenCount',{'IntProperty'})
    signature('/Script/UMG.PanelWidget:GetChildAt',{'IntProperty','ObjectProperty'})
    signature('/Script/UMG.PanelWidget:RemoveChild',{'ObjectProperty','BoolProperty'})
    signature('/Script/UMG.Overlay:AddChildToOverlay',{'ObjectProperty','ObjectProperty'})
    signature('/Script/UMG.OverlaySlot:SetPadding',{'StructProperty'})
    signature('/Script/UMG.OverlaySlot:SetHorizontalAlignment',{'EnumProperty|ByteProperty'})
    signature('/Script/UMG.OverlaySlot:SetVerticalAlignment',{'EnumProperty|ByteProperty'})
    local class=StaticFindObject('/Script/UMG.OverlaySlot');assert(valid(class),'OverlaySlot class unavailable')
    local fields={}
    class:ForEachProperty(function(p)
        local key=p:GetFName():ToString()
        if key=='Padding'then
            assert(kind(p)=='StructProperty'and name(p:GetStruct())=='ScriptStruct /Script/SlateCore.Margin','Changed overlay padding field')
            fields[key]=true
        elseif key=='HorizontalAlignment'or key=='VerticalAlignment'then
            assert(kind(p)=='ByteProperty'or kind(p)=='EnumProperty','Changed overlay alignment field');fields[key]=true
        end
    end)
    assert(fields.Padding and fields.HorizontalAlignment and fields.VerticalAlignment,'Overlay layout fields unavailable')
    verified=true
end
local function order(panel)
    local result={};local count=panel:GetChildrenCount()
    assert(type(count)=='number'and count>=1 and count<=32,'Unexpected mount overlay size')
    for i=0,count-1 do local child=panel:GetChildAt(i);assert(valid(child),'Mount overlay child unavailable');result[#result+1]=name(child)end
    return result
end
local function equal(a,b)
    if #a~=#b then return false end
    for i,v in ipairs(a)do if b[i]~=v then return false end end;return true
end
local function move_to_end(panel,widget)
    local slot=widget.Slot;local class=StaticFindObject('/Script/UMG.OverlaySlot')
    assert(valid(slot)and slot:IsA(class),'Native badge slot is not an OverlaySlot')
    local source=slot.Padding;local padding={Left=source.Left,Top=source.Top,Right=source.Right,Bottom=source.Bottom}
    for _,value in pairs(padding)do assert(type(value)=='number'and value==value and math.abs(value)<100000,'Invalid overlay padding')end
    local horizontal,vertical=slot.HorizontalAlignment,slot.VerticalAlignment
    for _,value in ipairs({horizontal,vertical})do assert(type(value)=='number'and value%1==0 and value>=0 and value<=3,'Invalid overlay alignment')end
    assert(panel:RemoveChild(widget),'Could not detach native badge child')
    local replacement=panel:AddChildToOverlay(widget)
    assert(valid(replacement)and replacement:IsA(class),'Could not reattach native badge child')
    replacement:SetPadding(padding)
    replacement:SetHorizontalAlignment(horizontal);replacement:SetVerticalAlignment(vertical)
end
local function restore(path)
    local saved=records[path];if not saved then return false end
    local panel=find(saved.panel)
    if not panel then records[path]=nil;return false end
    local current=order(panel)
    if equal(current,saved.original)then records[path]=nil;return false end
    assert(equal(current,saved.raised),'Mount overlay changed outside the badge adapter; restoration skipped')
    -- The badge was moved from its original position to the last position.
    -- Moving each following original sibling to the end restores exact order,
    -- while retaining every sibling's current layout through native slot APIs.
    for i=saved.index+1,#saved.original do
        local child=find(saved.original[i]);assert(child,'Original overlay sibling unavailable')
        move_to_end(panel,child)
    end
    assert(equal(order(panel),saved.original),'Native overlay restoration did not preserve order')
    records[path]=nil;return true
end
function M.update(row,owned)
    local path=name(row);if not path then return false,'Mount row unavailable'end
    local ok,result=pcall(function()
        if not owned then return restore(path)end
        verify()
        if records[path]then return false end
        local indicator=row.EquippedIndicator;assert(valid(indicator),'Native equipped indicator unavailable')
        local panel=indicator:GetParent();local class=StaticFindObject('/Script/UMG.Overlay')
        assert(valid(panel)and valid(class)and panel:IsA(class),'Native badge parent is not an Overlay')
        local original=order(panel);local index
        for i,child in ipairs(original)do if child==name(indicator)then index=i;break end end
        assert(index,'Native indicator is not in its Overlay')
        if index==#original then return false end
        local raised={};for i,child in ipairs(original)do if i~=index then raised[#raised+1]=child end end
        raised[#raised+1]=name(indicator)
        records[path]={panel=name(panel),original=original,raised=raised,index=index}
        move_to_end(panel,indicator)
        assert(equal(order(panel),raised),'Native indicator ordering did not update')
        return true
    end)
    if not ok then return false,tostring(result)end
    return result
end
function M.shutdown()
    local errors={};local paths={};for path in pairs(records)do paths[#paths+1]=path end
    for _,path in ipairs(paths)do local ok,why=pcall(restore,path);if not ok then errors[#errors+1]=tostring(why)end end
    return #errors==0,errors
end
return M
