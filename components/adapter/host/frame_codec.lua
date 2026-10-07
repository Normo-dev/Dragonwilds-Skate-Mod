-- Binary numeric transport; this module never executes a received frame.
local M={}
local header='<c4I8I4I8I2I2I2I2B'
local formats={}
local function slice(values,start,count)
    local out={}
    for i=1,count do out[i]=values[start+i-1] end
    return out
end
function M.decode(blob,previousSerial)
    if #blob<33 then return nil end
    local magic,serial,epoch,tick,ng,ns,nb,nd,connected,pos=string.unpack(header,blob)
    if magic~='S3D2' or ng>128 or ns>256 or nb>4096 or nd>4096 or nb%10~=0 or nd%10~=0 then return nil end
    local generation=blob:sub(pos,pos+ng-1);pos=pos+ng
    local state=blob:sub(pos,pos+ns-1);pos=pos+ns
    local count=32+nb+nd+10
    if #blob~=pos-1+count*8 then return nil end
    -- Shared memory may hold the same source pose for several engine ticks.
    -- Validate its header/length, but do not unpack hundreds of unchanged bones.
    if serial==previousSerial then return nil end
    local format=formats[count]
    if not format then format='<'..string.rep('d',count);formats[count]=format end
    local values={string.unpack(format,blob,pos)}
    return {serial=serial,epoch=epoch,tick=tick,generation=generation,state=state,
            controller_connected=connected==1,root=slice(values,1,16),velocity=slice(values,17,3),
            camera={position=slice(values,20,3),basis=slice(values,23,9),fov=values[32]},
            body=slice(values,33,nb),board=slice(values,33+nb,nd),rt=slice(values,33+nb+nd,10)}
end
return M
