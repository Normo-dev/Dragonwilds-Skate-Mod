-- Read-only save identity and safety boundary, verified by the live reflection
-- probe for Dragonwilds build25632050. No owner/password or save contents read.
local M={}
local function valid(o)return o and o:IsValid()end
local function finite(v)return type(v)=='number' and v==v and math.abs(v)<math.huge end
function M.read(pawn)
    local world=pawn:GetWorld();local name=world:GetFullName()
    local result={world=name}
    for _,object in ipairs(FindAllOf('PersistenceSubsystem') or {})do
        local ownWorld=valid(object) and object:GetWorld()
        if valid(ownWorld) and ownWorld:GetFullName()==name then
            local guid=object.WorldSaveSettings.WorldSaveGuid
            local words={guid.A,guid.B,guid.C,guid.D};local parts={};local nonzero=false
            for index,value in ipairs(words)do
                assert(finite(value) and value==math.floor(value),'World save GUID is invalid')
                local unsigned=value&0xffffffff
                parts[index]=string.format('%08x',unsigned)
                nonzero=nonzero or unsigned~=0
            end
            assert(#parts==4 and nonzero,'World save GUID is unavailable')
            local key='save-'..table.concat(parts)
            assert(not result.session or result.session==key,'Ambiguous active world save')
            result.session=key
        end
    end
    assert(result.session,'Cannot identify the active solo save')
    for _,settings in ipairs(FindAllOf('WorldSettings') or {})do
        local ownWorld=valid(settings) and settings:GetWorld()
        if valid(ownWorld) and ownWorld:GetFullName()==name then
            assert(finite(settings.KillZ),'World KillZ is unavailable')
            result.kill_z=settings.KillZ
            assert(not settings.bEnableWorldOriginRebasing,'This collision cache requires fixed world coordinates')
            break
        end
    end
    assert(result.kill_z,'Cannot read the active world safety boundary')
    return result
end
return M
