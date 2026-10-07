-- Read-only reflected engine collision defaults. No world edits or data dump.
local function valid(o)return o and o:IsValid()end
local function physics_settings()
    local result={classes={},enum_values={},errors={}}
    -- UClass:GetCDO and UEnum:ForEachName are read-only UE4SS APIs. The setting
    -- is reflected on PhysicsSettingsCore and inherited by engine PhysicsSettings.
    local enumNames={}
    for _,path in ipairs({'/Script/PhysicsCore.ECollisionTraceFlag','/Script/Engine.ECollisionTraceFlag'}) do
        local e=StaticFindObject(path)
        if valid(e) then
            local ok,why=pcall(function()
                e:ForEachName(function(name,value)
                    local label=name:ToString()
                    enumNames[value]=label
                    result.enum_values[#result.enum_values+1]={value=value,name=label}
                end)
            end)
            if ok then result.enum_path=path;break
            else result.errors[#result.errors+1]={path=path,error=tostring(why)} end
        end
    end
    for _,path in ipairs({'/Script/Engine.PhysicsSettings','/Script/PhysicsCore.PhysicsSettingsCore'}) do
        local item={path=path,found=false};result.classes[#result.classes+1]=item
        local ok,why=pcall(function()
            local class=StaticFindObject(path)
            if not valid(class) then return end
            item.found=true
            class:ForEachProperty(function(p)
                if p:GetFName():ToString()=='DefaultShapeComplexity' then
                    item.reflected_property=p:GetFullName()
                    item.property_type=p:GetClass():GetFName():ToString()
                end
            end)
            local cdo=class:GetCDO()
            if not valid(cdo) then item.error='Class default object unavailable';return end
            item.cdo=cdo:GetFullName()
            local value=cdo.DefaultShapeComplexity
            item.lua_type=type(value)
            if type(value)=='number' then
                item.default_shape_complexity=value
                item.default_shape_complexity_name=enumNames[value]
                if path=='/Script/Engine.PhysicsSettings' then
                    result.engine_default=value;result.engine_default_name=enumNames[value]
                end
            else item.raw_value=tostring(value) end
        end)
        if not ok then item.error=tostring(why) end
    end
    return result
end
return {capture=physics_settings}
