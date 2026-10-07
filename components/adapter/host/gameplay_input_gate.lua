-- The game's own current input mode governs both keyboard and controller UI.
-- These are live-verified hard UObject properties, not widget visibility guesses
-- or soft references. No inventory, hooks, mutation, or retained UObject wrappers.
local M={}
local verified=false
local controllerPath='/Script/Dominion.DominionPlayerController'
local modePath='/Script/Dominion.DominionInputMode'
local allowedFields={'GameplayInputMode','MountedInputMode','GameplayLockOnTargetingInputMode'}
local mappedClassFallback=false
local function valid(value)
    if value==nil then return false end
    local ok,result=pcall(function()return value:IsValid()end);return ok and result
end
local function name(value)if valid(value)then return value:GetFullName()end end
local function find(path)
    local value=StaticFindObject(path);assert(valid(value),'Input gate API unavailable: '..path)
    return value
end
local function kind(property)return property:GetClass():GetFName():ToString()end
local function verify()
    if verified then return end
    local wanted={CurrentInputMode=true}
    for _,field in ipairs(allowedFields)do wanted[field]=true end
    find(controllerPath):ForEachProperty(function(property)
        local field=property:GetFName():ToString()
        if wanted[field]then
            assert(kind(property)=='ObjectProperty','Changed native input mode property: '..field)
            -- In the pinned UE4SS build BoolProperty and ObjectProperty share
            -- one Lua metatable name. If a bool wrapper was constructed first,
            -- the object-only accessor is absent. Native GetClass still gives
            -- the exact FField class; mapped mode objects are checked below.
            -- An available accessor remains authoritative: errors or a changed
            -- declared class never fall back to accepting the live value.
            local accessor=property.GetPropertyClass
            if accessor==nil then mappedClassFallback=true
            else
                assert(type(accessor)=='function'and name(accessor(property))=='Class '..modePath,
                    'Changed native input mode property class: '..field)
            end
            wanted[field]=nil
        end
    end)
    assert(next(wanted)==nil,'Native controller input mode fields unavailable')
    local cursor=false
    find('/Script/Engine.PlayerController'):ForEachProperty(function(property)
        if property:GetFName():ToString()=='bShowMouseCursor'then
            assert(kind(property)=='BoolProperty','Changed native cursor property');cursor=true
        end
    end)
    assert(cursor,'Native cursor property unavailable')
    local inputConfig=false
    find(modePath):ForEachProperty(function(property)
        if property:GetFName():ToString()=='InputConfig'then
            assert(kind(property)=='EnumProperty'and
                name(property:GetEnum())=='Enum /Script/Dominion.EDominionWidgetInputMode',
                'Changed native input configuration enum')
            inputConfig=true
        end
    end)
    assert(inputConfig,'Native input configuration unavailable')
    local parameters={}
    find('/Script/Engine.GameplayStatics:IsGamePaused'):ForEachProperty(function(property)
        parameters[#parameters+1]=kind(property)
    end)
    assert(#parameters==2 and parameters[1]=='ObjectProperty'and parameters[2]=='BoolProperty',
        'Changed native pause getter')
    verified=true
end

function M.allowed(pawn,pc)
    local status={schema='S3GAMEPLAYINPUT1',ready=false,allowed=false}
    local ok,why=pcall(function()
        verify()
        assert(valid(pawn)and pawn:IsA(find('/Script/Dominion.DominionPlayerCharacter')),
            'Playable input owner unavailable')
        assert(valid(pc)and pc:IsA(find(controllerPath)),'Gameplay controller unavailable')
        local world=name(pawn:GetWorld())
        assert(world and world==name(pc:GetWorld()),'Input owner world mismatch')
        local paused=find('/Script/Engine.Default__GameplayStatics'):IsGamePaused(pawn)
        local cursor=pc.bShowMouseCursor
        assert(type(paused)=='boolean'and type(cursor)=='boolean','Native input state is not boolean')
        status.paused=paused;status.cursor=cursor;status.ready=true
        local current=pc.CurrentInputMode
        if not valid(current)then status.reason='input_mode_unavailable';return end
        assert(current:IsA(find(modePath)),'Unexpected native input mode class')
        status.input_mode=name(current)
        local configuredModes={}
        for _,field in ipairs(allowedFields)do
            local configured=pc[field]
            if valid(configured)then
                assert(configured:IsA(find(modePath)),'Unexpected configured input mode class: '..field)
                configuredModes[field]=name(configured)
            end
        end
        status.property_class_validation=mappedClassFallback and'native_property_and_mapped_class'or'declared_and_mapped_class'
        -- Death/respawn cleanup needs this identity even when the death screen
        -- has a cursor or pauses the world. Input remains blocked below.
        if paused then status.reason='paused';return end
        if cursor then status.reason='mouse_cursor';return end
        local inputConfig=current.InputConfig
        assert(type(inputConfig)=='number'and inputConfig%1==0 and inputConfig>=0 and inputConfig<=5,
            'Invalid native input configuration value')
        status.input_config=inputConfig
        -- Owned EDominionWidgetInputMode.Game is 2. Other Game modes (death,
        -- build, fishing, spell placement) still fail the identity allowlist.
        if status.input_config~=2 then status.reason='ui_or_cutscene_mode';return end
        for _,field in ipairs(allowedFields)do
            if configuredModes[field]==status.input_mode then
                status.allowed=true;status.mode_field=field;return
            end
        end
        status.reason='non_gameplay_mode'
    end)
    if not ok then
        status.ready=false;status.allowed=false;status.reason='input_gate_unavailable';status.error=tostring(why)
    end
    return status.allowed,status
end
return M
