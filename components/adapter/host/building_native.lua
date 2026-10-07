-- Version-pinned read-only game-thread snapshot of native lightweight pieces.
-- The separate helper uses bounded ReadProcessMemory reads, never engine calls.
local M={}
local ids_call,read_call,bodies_call
local function integer(n,max,label)
    assert(type(n)=='number' and n%1==0 and n>=0 and n<=max,'Invalid '..label);return n
end
local function finite(n)
    assert(type(n)=='number' and n==n and n>-math.huge and n<math.huge,'Non-finite native building transform');return n
end
local function address(manager)
    assert(manager and manager:IsValid(),'Native building manager unavailable')
    return integer(manager:GetAddress(),9007199254740991,'native building manager address')
end
local function load()
    if ids_call then return end
    local file=require('skate_config').building_reader_library
    assert(type(file)=='string' and #file>0 and not file:find('\0',1,true),'Building reader library is not configured')
    local a,why=package.loadlib(file,'skate_building_ids');assert(type(a)=='function','Building ID reader unavailable: '..tostring(why))
    local b,why2=package.loadlib(file,'skate_building_read');assert(type(b)=='function','Building row reader unavailable: '..tostring(why2))
    ids_call,read_call=a,b
end
local function header(bytes,magic,managerAddress)
    assert(type(bytes)=='string','Native building reader returned no snapshot')
    if bytes:sub(1,7)=='DWBERR1' then error('Native building reader: '..bytes:sub(8)) end
    assert(#bytes>=23 and bytes:sub(1,7)==magic,'Malformed native building snapshot')
    local version,returned,count,pos=string.unpack('<I4I8I4',bytes,8)
    assert(version==1 and returned==managerAddress,'Native building snapshot identity/version mismatch')
    return count,pos
end
local function boolean(n)assert(n==0 or n==1,'Invalid native building flag');return n==1 end
function M.ids(manager)
    load();local a=address(manager)
    local bytes=ids_call(string.pack('<c5I8','DWBQ1',a))
    local count,pos=header(bytes,'DWBIDS1',a)
    assert(count<=50000 and #bytes==23+count*4,'Invalid native building ID count/size')
    local result={};local last=-1
    for i=1,count do
        local id;id,pos=string.unpack('<I4',bytes,pos)
        assert(id>last,'Native building IDs are not unique and ordered');last=id;result[i]=id
    end
    assert(address(manager)==a,'Native building manager identity changed')
    return result
end
function M.read(manager,ids)
    load();assert(type(ids)=='table' and getmetatable(ids)==nil and #ids>=1 and #ids<=16,'Invalid native building request IDs')
    local a=address(manager);local parts={string.pack('<c5I8I4','DWBQ1',a,#ids)};local seen={}
    for _,id in ipairs(ids)do
        integer(id,4294967295,'native building ID');assert(not seen[id],'Duplicate native building request ID')
        seen[id]=true;parts[#parts+1]=string.pack('<I4',id)
    end
    for k in pairs(ids)do assert(type(k)=='number' and k%1==0 and k>=1 and k<=#ids,'Invalid native building request key')end
    local bytes=read_call(table.concat(parts));local count,pos=header(bytes,'DWBROW1',a)
    assert(count==#ids and #bytes==23+count*108,'Invalid native building row count/size')
    local result={}
    for i=1,count do
        local id,data,derived,qx,qy,qz,qw,tx,ty,tz,sx,sy,sz,preview,ghost,defer,enabled,entities
        id,data,derived,qx,qy,qz,qw,tx,ty,tz,sx,sy,sz,preview,ghost,defer,enabled,entities,pos=
            string.unpack('<I4I8I8ddddddddddBBBBI4',bytes,pos)
        assert(id==ids[i],'Native building response order/ID mismatch')
        integer(data,9007199254740991,'building asset address');integer(derived,9007199254740991,'building derived asset address')
        assert(data>0 and derived>0,'Native building asset pointer is absent')
        local norm=finite(qx)^2+finite(qy)^2+finite(qz)^2+finite(qw)^2
        assert(math.abs(norm-1)<0.0001,'Native building quaternion is not normalized')
        finite(tx);finite(ty);finite(tz);finite(sx);finite(sy);finite(sz)
        assert(sx>0 and sy>0 and sz>0,'Mirrored/zero native building scale needs explicit handling')
        integer(entities,1024,'building entity count')
        result[i]={id=id,data_asset_address=data,derived_asset_address=derived,
            transform={Rotation={X=qx,Y=qy,Z=qz,W=qw},Translation={X=tx,Y=ty,Z=tz},Scale3D={X=sx,Y=sy,Z=sz}},
            preview=boolean(preview),ghosted=boolean(ghost),can_defer_collision_update=boolean(defer),
            collision_enabled=boolean(enabled),entity_count=entities}
    end
    assert(address(manager)==a,'Native building manager identity changed')
    return result
end
function M.body_packet(manager,subsystem,ids)
    load()
    if not bodies_call then
        local fn,why=package.loadlib(require('skate_config').building_reader_library,'skate_building_read_bodies')
        assert(type(fn)=='function','Building body reader unavailable: '..tostring(why));bodies_call=fn
    end
    assert(type(ids)=='table' and getmetatable(ids)==nil and #ids>=1 and #ids<=4,'Invalid body request IDs')
    local a,s=address(manager),address(subsystem)
    local parts={string.pack('<c5I8I8I4','DWBQ2',a,s,#ids)};local seen={}
    for _,id in ipairs(ids)do
        integer(id,4294967295,'building ID');assert(not seen[id],'Duplicate body request ID');seen[id]=true
        parts[#parts+1]=string.pack('<I4',id)
    end
    for k in pairs(ids)do assert(type(k)=='number' and k%1==0 and k>=1 and k<=#ids,'Invalid body request key')end
    local bytes=bodies_call(table.concat(parts))
    assert(type(bytes)=='string','Building body reader returned no snapshot')
    if bytes:sub(1,7)=='DWBERR1'then error('Native building body reader: '..bytes:sub(8))end
    assert(#bytes>=31 and #bytes<=400000 and bytes:sub(1,7)=='DWBBDY2','Malformed native body snapshot')
    local version,ma,sa,count,pos=string.unpack('<I4I8I8I4',bytes,8)
    assert(version==1 and ma==a and sa==s and count==#ids,'Native body identity/version/count mismatch')
    assert(address(manager)==a and address(subsystem)==s,'Native body manager/subsystem identity changed')
    return bytes,a,s,count,pos
end
function M.bodies(manager,subsystem,ids)
    local bytes,a,s,count,pos=M.body_packet(manager,subsystem,ids)
    local result={}
    for i=1,count do
        assert(#bytes-pos+1>=108,'Truncated native body piece')
        local raw=bytes:sub(pos,pos+107);pos=pos+108
        -- Reuse the exact ROW1 transform decoder without a second native call.
        local previous=read_call
        read_call=function()return 'DWBROW1'..string.pack('<I4I8I4',1,a,1)..raw end
        local ok,rows=pcall(M.read,manager,{ids[i]});read_call=previous
        assert(ok,rows);local row=rows[1];row.entities={}
        assert(#bytes-pos+1>=row.entity_count*96,'Truncated native body policies')
        for index=0,row.entity_count-1 do
            local ei,handle,present,tag,actor,created,dirty,desired,enabled,object_type
            ei,handle,present,tag,actor,created,dirty,desired,enabled,object_type,pos=
                string.unpack('<I4I8BBBBBBBB',bytes,pos)
            assert(ei==index,'Native entity order mismatch')
            -- FMassEntityHandle is an opaque 64-bit index/serial pair, not an
            -- address. Preserve its exact Lua integer bits as diagnostic text.
            assert(math.type(handle)=='integer','Invalid native entity handle')
            local values={};for channel=1,32 do values[channel],pos=string.unpack('<B',bytes,pos);integer(values[channel],2,'native response')end
            local profile,body,owner,body_index,resolved_owner,external
            profile,body,owner,body_index,resolved_owner,external,pos=string.unpack('<I8I8I8I4I8I8',bytes,pos)
            for _,pointer in ipairs({body,owner,resolved_owner,external})do integer(pointer,9007199254740991,'native body pointer')end
            integer(desired,5,'native desired collision');integer(enabled,5,'native effective collision');integer(object_type,31,'native object channel')
            assert(not boolean(dirty),'Native entity collision is dirty')
            if boolean(present)then
                assert(owner==a and body_index==row.id,'Native body source identity mismatch')
                if enabled==1 or enabled==3 or enabled==5 then assert(body>0,'Native query body has no BodySetup')end
            end
            row.entities[#row.entities+1]={entity_index=ei,handle=tostring(handle),physics_present=boolean(present),
                building_mesh=boolean(tag),actor_enabled=boolean(actor),created=boolean(created),dirty=false,
                desired_enabled=desired,collision={enabled=enabled,object_type=object_type,responses=values},
                profile_name_bits=tostring(profile),body_setup_address=body,source_object_address=owner,body_index=body_index,
                resolved_owner_address=resolved_owner,resolved_external_address=external,collision_verified=true}
        end
        result[#result+1]=row
    end
    assert(pos==#bytes+1,'Trailing native body snapshot data')
    assert(address(manager)==a and address(subsystem)==s,'Native body manager/subsystem identity changed')
    return result,bytes
end
return M
