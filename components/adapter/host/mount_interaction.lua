-- Read-only equivalent of the native mount interaction gate. Names/types come
-- from the owned runtime reflection and native registration, never offsets.
local M={}
local verified=false
local playerElementClass
local function valid(value)
    if value==nil then return false end
    local ok,result=pcall(function()return value:IsValid()end);return ok and result
end
local function name(value)if valid(value)then return value:GetFullName()end end
local function kind(property)return property:GetClass():GetFName():ToString()end
local function class(path)
    local result=StaticFindObject(path);assert(valid(result),'Missing interaction reflection: '..path)
    return result
end
local function declared_class(property)
    -- Pinned UE4SS BoolProperty/ObjectProperty wrappers share a Lua metatable.
    -- A missing object-only accessor is handled with mapped-object IsA checks,
    -- never by changing that metatable or reading fields at native offsets.
    assert(kind(property)=='ObjectProperty','Changed interaction object property')
    local accessor=property.GetPropertyClass
    if accessor==nil then return nil end
    assert(type(accessor)=='function','Changed interaction class accessor')
    local result=name(accessor(property))
    assert(result and result:match('^Class /Script/'),'Interaction object class unavailable')
    return result
end
local function verify()
    if verified then return end
    local getter=class('/Script/Dominion.DominionPlayerCharacter:GetInteractableDetector')
    local count=0
    getter:ForEachProperty(function(property)
        count=count+1
        assert(kind(property)=='ObjectProperty'and property:GetFName():ToString()=='ReturnValue',
            'Changed native interactable detector getter')
        local declared=declared_class(property)
        assert(not declared or declared=='Class /Script/Dominion.InteractableDetectorComponent',
            'Changed native interactable detector class')
    end)
    assert(count==1,'Changed native interactable detector signature')
    local detector=class('/Script/Dominion.InteractableDetectorComponent');local current=false
    detector:ForEachProperty(function(property)
        if property:GetFName():ToString()=='CurrentInteractable'then
            local declared=declared_class(property)
            assert(not declared or declared=='Class /Script/Dominion.InteractionComponent',
                'Changed current interactable field class')
            current=true
        end
    end)
    assert(current,'Current interactable field unavailable')
    local component=class('/Script/Dominion.InteractionComponent');local players=false
    component:ForEachProperty(function(property)
        if property:GetFName():ToString()=='PlayersCurrentlyInteracting'then
            assert(kind(property)=='ArrayProperty','Changed interacting player array')
            local inner=property:GetInner()
            assert(kind(inner)=='ObjectProperty','Changed interacting player reference type')
            local valueClass=declared_class(inner)
            playerElementClass=valueClass and valueClass:gsub('^Class ','')or'/Script/Dominion.DominionPlayerCharacter'
            players=true
        end
    end)
    assert(players,'Interacting players field unavailable')
    verified=true
end

-- Returns a boolean, or nil plus an error so entry can fail closed. No UObject
-- or property wrappers are retained between calls, and no game state is changed.
function M.is_interacting(pawn)
    local ok,result=pcall(function()
        verify()
        assert(valid(pawn)and pawn:IsA(class('/Script/Dominion.DominionPlayerCharacter')),
            'Playable interaction owner unavailable')
        assert(pawn:IsA(class(playerElementClass)),'Interacting array class cannot hold this player')
        local world=name(pawn:GetWorld());assert(world,'Playable world unavailable')
        local detector=pawn:GetInteractableDetector()
        if not valid(detector)then return false end
        assert(detector:IsA(class('/Script/Dominion.InteractableDetectorComponent'))and
            name(detector:GetWorld())==world,'Unexpected interaction detector')
        local component=detector.CurrentInteractable
        if not valid(component)then return false end
        assert(component:IsA(class('/Script/Dominion.InteractionComponent'))and
            name(component:GetWorld())==world,'Unexpected interaction component')
        local playerName=name(pawn);local found=false
        local function visit(value)
            if not valid(value)then return end
            assert(value:IsA(class(playerElementClass)),'Unexpected interacting player class')
            if name(value)==playerName then found=true end
        end
        local values=component.PlayersCurrentlyInteracting
        if type(values)=='table'and type(values.ForEach)~='function'then
            for _,value in ipairs(values)do visit(value)end
        else
            assert(values~=nil,'Interacting player array unavailable')
            values:ForEach(function(_,value)visit(value:get())end)
        end
        return found
    end)
    if not ok then return nil,tostring(result)end
    return result
end
return M
