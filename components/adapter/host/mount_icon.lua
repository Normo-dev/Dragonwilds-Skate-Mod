-- Hard Texture2D references only. Never marshal a SoftObjectProperty through Lua.
-- The native mount tile keeps its authored ImageSlicer framing/material.
local M={}
local state={}
local function valid(o)
    if o==nil then return false end
    local ok,v=pcall(function()return o:IsValid()end);return ok and v
end
local function name(o)if valid(o)then return o:GetFullName()end end
local function find(path)
    if not path then return end
    local o=StaticFindObject((path:gsub('^[^ ]+ ','')))
    if valid(o)then return o end
end
local function signature(path,types)
    local fn=StaticFindObject(path);assert(valid(fn),'Missing icon API: '..path)
    local found={};fn:ForEachProperty(function(p)found[#found+1]=p:GetClass():GetFName():ToString()end)
    assert(#found==#types,'Changed icon API: '..path)
    for i,t in ipairs(types)do assert(found[i]==t,'Changed icon parameter: '..path)end
end
local function prepare(pawn,path)
    if not state.verified then
        signature('/Script/UMG.ListViewBase:GetDisplayedEntryWidgets',{'ArrayProperty'})
        signature('/Script/UMG.ListView:BP_GetSelectedItem',{'ObjectProperty'})
        signature('/Script/Engine.KismetRenderingLibrary:ImportFileAsTexture2D',{'ObjectProperty','StrProperty','ObjectProperty'})
        signature('/Script/Engine.MaterialInstanceDynamic:SetTextureParameterValue',{'NameProperty','ObjectProperty'})
        signature('/Script/UMG.Image:SetBrushFromTexture',{'ObjectProperty','BoolProperty'})
        state.verified=true
    end
    local texture=state.path==path and find(state.texture)
    if texture then return texture end
    local file=io.open(path,'rb');assert(file,'Mount icon file unavailable');file:close()
    local rendering=StaticFindObject('/Script/Engine.Default__KismetRenderingLibrary')
    assert(valid(rendering),'Texture import library unavailable')
    texture=rendering:ImportFileAsTexture2D(pawn,path)
    assert(valid(texture),'Could not import skateboard icon')
    state.path=path;state.texture=name(texture)
    return texture
end
local function each(values,fn)
    if values==nil then return end
    if type(values)=='table'and type(values.ForEach)~='function'then
        for _,value in ipairs(values)do fn(value)end
    else
        values:ForEach(function(_,value)fn(value:get())end)
    end
end
function M.update(pawn,list,screen,entry,asset,path,now)
    if not path or path==''then return {enabled=false}end
    now=now or 0
    local result={enabled=true,row_count=0,preview=false,displayed_count=0,owned_rows=0,discovery='displayed'}
    local ok,why=pcall(function()
        local assetName=name(asset);assert(assetName,'Owned mount asset unavailable')
        local texture=prepare(pawn,path)
        local rowClass=StaticFindObject('/Script/Dominion.MountSlotBase')
        assert(valid(rowClass),'Native mount row class unavailable')
        local seen={}
        local function apply_row(row)
            local rowName=name(row)
            if rowName and seen[rowName]then return end
            if rowName then seen[rowName]=true end
            if valid(row)and row:IsA(rowClass)and name(row.MountData)==assetName then
                result.owned_rows=result.owned_rows+1
                -- Native row initialization uses this same MID and parameter.
                -- Its callback will restore a recycled row's normal mount icon.
                local material=row.MountIconMaterialInstance
                if valid(material)then
                    material:SetTextureParameterValue(FName('Texture to cut'),texture)
                    result.row_count=result.row_count+1
                end
            end
        end
        each(list:GetDisplayedEntryWidgets(),function(row)
            result.displayed_count=result.displayed_count+1;apply_row(row)
        end)
        if result.row_count==0 then
            -- This UE4SS build returns an empty Lua array for some native const
            -- TArray& return values, including GetDisplayedEntryWidgets. Discover
            -- only the exact mount-row class, cache names and re-acquire each tick.
            -- Same-world filtering excludes CDOs and other game/menu sessions.
            result.discovery='cached mount-row class'
            local world=name(pawn:GetWorld())
            assert(world,'Player world unavailable for mount-row discovery')
            if state.row_world~=world or not state.rows or now-(state.last_discovery or -math.huge)>=2 then
                state.rows={};state.row_world=world;state.last_discovery=now
                for _,row in ipairs(FindAllOf('WBP_MountSlot_C')or{})do
                    if valid(row)and name(row:GetWorld())==world then
                        state.rows[#state.rows+1]=name(row)
                    end
                end
            end
            result.cached_rows=#state.rows
            for _,rowName in ipairs(state.rows)do apply_row(find(rowName))end
        end
        if name(list:BP_GetSelectedItem())==name(entry)then
            local panel=screen.MountDetailsPanel
            if valid(panel)then
                local preview=panel.MountPreviewImage
                if valid(preview)then
                    preview:SetBrushFromTexture(texture,false)
                    result.preview=true
                end
            end
        end
        result.texture=state.texture
    end)
    if not ok then result.error=tostring(why)end
    return result
end
return M
