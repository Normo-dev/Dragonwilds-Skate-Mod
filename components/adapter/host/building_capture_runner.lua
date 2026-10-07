-- One opt-in, bounded read-only diagnostic. No actor, save, or collision writes.
local sequence=0
local observed_sequence=0
local function create()
local M={}
local pending
local config=require('skate_config')
local folder=config.mailbox
local json=require('json')
local clock=os.clock
do local ok,t=pcall(require,'shared_transport');if ok and type(t.clock)=='function'then clock=t.clock end end
local arrays={'errors','managers','data_assets','pieces','building_hisms','type_metadata','type_metadata_errors',
    'native_pieces','native_piece_ids','native_missing_piece_ids','inventory','inventory_piece_ids','retained_loaded_piece_ids','derived_assets','body_bindings'}
local published={}
local function abort(p,why)
    p.capture.cancel()
    if p.file then p.file:close();p.file=nil;os.remove(p.temporary)end
    if p.packet_file then p.packet_file:close();p.packet_file=nil end
    if p.packet_path then os.remove(p.packet_path)end
    pending=nil;return {complete=false,error=tostring(why)}
end
function M.begin(pawn,options)
    if pending then return false,'Building capture already running' end
    options=options or{}
    local state2=options.state2;if state2==nil then state2=config.building_state_probe==true end
    local packets=options.packets;if packets==nil then packets=config.building_state_packets==true end
    assert(type(state2)=='boolean'and type(packets)=='boolean','Invalid building capture options')
    packets=packets and state2
    sequence=sequence+1
    local p={started=clock(),phase='capture',rows=0,capture_ticks=0,capture_active_seconds=0,capture_max_tick_seconds=0,
        state2=state2,unique_report=options.unique_report==true,capture=state2 and require('building_state')or require('building_collision'),
        capture_id=string.format('%d-%d-%d',os.time(),math.floor(clock()*1000000),sequence)};pending=p
    p.target=folder..(state2 and(options.unique_report==true and 'building-state2-'..p.capture_id..'.json'or'building-state2.json')or'building-native-state.json');p.temporary=p.target..'.tmp'
    if state2 and type(p.capture.new)=='function'then p.capture=p.capture.new()end
    if options.unique_report==true then
        local existing=io.open(p.target,'rb');if existing then existing:close();pending=nil;return false,'Building report identity already exists'end
    end
    local ok,why=pcall(function()
        local capture_options={clock=clock,nativeReader=true,incremental=options.incremental==true}
        if capture_options.incremental then
            capture_options.baseline,capture_options.baseline_loader=require('building_baseline').read(folder)
        end
        if packets then
            local available=false
            for attempt=1,100 do
                sequence=sequence+1;p.packet_name=string.format('building-packets-%d-%d-%d.bin',os.time(),math.floor(clock()*1000000),sequence)
                local candidate=folder..p.packet_name;local existing=io.open(candidate,'rb')
                if existing then existing:close()else p.packet_path=candidate;available=true;break end
            end
            assert(available,'Cannot reserve a new private building packet file')
            p.packet_file=assert(io.open(p.packet_path,'wb'));assert(p.packet_file:write('S3BPK1'))
            p.packet_bytes=6;p.packet_count=0
            capture_options.packet_sink=function(bytes)
                assert(type(bytes)=='string'and #bytes>=31 and #bytes<=400000,'Invalid building packet size')
                assert(p.packet_bytes+4+#bytes<=67108864,'Building packet stream exceeds limit')
                assert(p.packet_file:write(string.pack('<I4',#bytes),bytes))
                p.packet_bytes=p.packet_bytes+4+#bytes;p.packet_count=p.packet_count+1
            end
        end
        p.capture.begin(pawn,capture_options)
    end)
    if not ok then abort(p,why);return false,tostring(why)end
    return true
end
function M.busy()return pending~=nil end
function M.status()
    if not pending then return nil end
    return {phase=pending.phase,rows_written=pending.rows,elapsed_seconds=clock()-pending.started}
end
function M.cancel()
    if pending then abort(pending,'Cancelled')end
end
function M.discard(result)
    -- Only completed unique files from this runner instance are eligible.
    -- Caller must wait until its cache/native request no longer references them.
    if type(result)~='table'then return false end
    local own=published[result.capture_id]
    if not own or own.report~=result.path then return false end
    -- Validation and deletion run in Python, outside the game thread. The Lua
    -- operation writes only a small receipt for these exact owned filenames.
    local receipt={schema='S3BUILDINGCLEANUP1',capture_id=result.capture_id,
        report=own.report:sub(#folder+1),packet=own.packet and own.packet:sub(#folder+1)or nil}
    local path=folder..'building-cleanup-'..result.capture_id..'.json'
    local file=assert(io.open(path..'.tmp','wb'));assert(file:write(json.encode(receipt)));assert(file:close())
    assert(os.rename(path..'.tmp',path))
    published[result.capture_id]=nil;return true
end
function M.tick()
    local p=pending;if not p then return nil end
    local ok,result=pcall(function()
        if p.phase=='capture'then
            local before=clock()
            local value=p.capture.advance(256,0.001)
            local elapsed=clock()-before;p.capture_ticks=p.capture_ticks+1
            p.capture_active_seconds=p.capture_active_seconds+elapsed;p.capture_max_tick_seconds=math.max(p.capture_max_tick_seconds,elapsed)
            if not value then return nil end
            if value.complete and value.collision_verified then
                observed_sequence=observed_sequence+1;p.observed_serial=observed_sequence;value.observed_serial=observed_sequence
            end
            if p.packet_file then
                assert(p.packet_file:close());p.packet_file=nil
                if value.complete and value.collision_verified then
                    value.piece_packets={schema='S3BUILDINGPACKETS1',path=p.packet_name,bytes=p.packet_bytes,
                        packet_count=p.packet_count,piece_count=#value.pieces}
                    value.pieces={}
                else os.remove(p.packet_path);p.packet_path=nil end
            end
            p.result=value;p.result.capture_id=p.capture_id;p.result.capture_runner={schema=1,elapsed_seconds=clock()-p.started,capture_ticks=p.capture_ticks,
                capture_active_seconds=p.capture_active_seconds,capture_max_tick_seconds=p.capture_max_tick_seconds}
            local excluded={};for _,k in ipairs(arrays)do excluded[k]=true end
            local envelope={};for k,v in pairs(value)do if not excluded[k]then envelope[k]=v end end
            p.file=assert(io.open(p.temporary,'wb'));assert(p.file:write(json.encode_precise(envelope):sub(1,-2)))
            p.phase='write';p.field=1;p.index=1;return nil
        end
        local started=clock()
        for _=1,(p.unique_report and 256 or 32) do
            if clock()-started>=0.001 then break end
            local key=arrays[p.field]
            if not key then
                assert(p.file:write('}'));assert(p.file:close());p.file=nil
                -- Only this module's fixed diagnostic report is replaced.
                if not p.unique_report then os.remove(p.target)
                else local existing=io.open(p.target,'rb');if existing then existing:close();error('Unique building report appeared during publication')end end
                assert(os.rename(p.temporary,p.target));pending=nil
                if p.unique_report then published[p.capture_id]={report=p.target,packet=p.packet_path}end
                return {complete=p.result.complete==true,collision_verified=p.result.collision_verified==true,
                    error=p.result.error or(p.result.errors and p.result.errors[1]),capture_failure_phase=p.result.capture_failure_phase,
                    path=p.target,packet_path=p.packet_path,capture_id=p.capture_id,observed_serial=p.observed_serial,world=p.result.world,world_session=p.result.world_session,
                    native_piece_count=p.result.piece_packets and p.result.piece_packets.piece_count or #(p.state2 and p.result.pieces or p.result.native_pieces or{}),elapsed_seconds=clock()-p.started,
                    capture_active_seconds=p.capture_active_seconds,capture_max_tick_seconds=p.capture_max_tick_seconds}
            end
            if p.index==1 then assert(p.file:write(',"'..key..'":['))end
            local value=(p.result[key]or{})[p.index]
            if value~=nil then
                assert(p.file:write((p.index>1 and ','or'')..json.encode_precise(value)));p.rows=p.rows+1;p.index=p.index+1
            else assert(p.file:write(']'));p.field=p.field+1;p.index=1 end
        end
        return nil
    end)
    if not ok then return abort(p,result)end
    return result
end
return M
end
local default=create();default.new=create;return default
