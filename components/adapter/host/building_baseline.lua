-- Untrusted planning hint, never collision authority. Python independently
-- verifies every retained ID against committed native evidence.
local M={}
local json=require('json')
local cached_id,cached_value
function M.read(folder)
 local file=io.open(folder..'building-baseline.id','rb');if not file then return end
 local id=file:read(66);file:close()
 if not id or #id~=65 or not id:match('^[a-f0-9]+\n$')then return end;id=id:sub(1,64)
 if cached_id==id then return cached_value end
 file=io.open(folder..'building-baseline-'..id..'.rows','rb');if not file then return end
 local raw=file:read(8388609);file:close();if not raw or #raw>8388608 then return end
 local newline=raw:find('\n',1,true);if not newline or newline>8192 then return end
 local okay,value=pcall(json.decode,raw:sub(1,newline-1))
 if not okay or type(value)~='table'or value.schema~='S3BUILDINGBASELINE1'or value.identifier~=id then return end
 value.descriptors={};value.native_piece_ids={}
 local pos,count,done=newline+1,0,false
 local loader={}
 function loader.advance()
  if done then return value end
  if pos>#raw then
   done=true
   cached_id,cached_value=id,value;return value
  end
  local last=raw:find('\n',pos,true)
  if not last or last-pos>4096 then done=true;value={};return value end
  local success,row=pcall(json.decode,raw:sub(pos,last-1));pos=last+1
  if not success or type(row)~='table'or type(row.id)~='number'or row.id%1~=0 or row.id<0 or row.id>4294967295
   or type(row.native)~='boolean'or type(row.descriptor)~='table'or value.descriptors[tostring(row.id)]~=nil then
   done=true;value={};return value
  end
  count=count+1;if count>50000 then done=true;value={};return value end
  value.descriptors[tostring(row.id)]=row.descriptor
  if row.native then value.native_piece_ids[#value.native_piece_ids+1]=row.id end
  return nil
 end
 return nil,loader
end
return M
