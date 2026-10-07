-- Read only the configured gameplay keys through PlayerController, not global OS
-- keyboard state. Values use the existing Skate Xbox control packet layout.
local M={}
local bindings=require('skate_bindings')
local cameraDown=false
local cameraSerial=0
function M.sample(pc,allowed)
    local function down(key)return pc:IsInputKeyDown({KeyName=FName(key)})end
    local camera=down(bindings.camera)
    if allowed and camera and not cameraDown then cameraSerial=cameraSerial+1 end
    cameraDown=camera
    local pad={buttons=0,triggers={0,0},left={0,0},right={0,0}}
    if allowed then
        for _,item in ipairs(bindings.buttons)do
            if down(item[1])then pad.buttons=pad.buttons|item[2]end
        end
        for index,key in ipairs(bindings.triggers)do if down(key)then pad.triggers[index]=255 end end
        for stick,keys in pairs(bindings.sticks)do
            pad[stick][1]=(down(keys[2]) and 32767 or 0)-(down(keys[1]) and 32767 or 0)
            pad[stick][2]=(down(keys[3]) and 32767 or 0)-(down(keys[4]) and 32767 or 0)
        end
    end
    return pad,cameraSerial
end
return M
