-- First-run gate. Until locally prepared data exists, only the small read-only
-- collision policy and one requested mapping dump run. No mount/scene/physics
-- adapter is imported here before the external launcher can verify the data.
local M={}
local UE=require('UEHelpers')
local json=require('json')
local config=require('skate_config')
local state={next_poll=0,captures={},errors=0,started=false}
local loopHandle,hudName,textName,lastMessage,reloadDown
local tick
local function valid(value)
    if value==nil then return false end
    local ok,result=pcall(function()return value:IsValid()end);return ok and result
end
local function name(value)if valid(value)then return value:GetFullName()end end
local function find(path)
    if not path then return end
    local value=StaticFindObject((path:gsub('^[^ ]+ ','')));if valid(value)then return value end
end
local function path(root,file)
    assert(type(root)=='string'and root~=''and not root:find('\0',1,true),'Missing setup directory')
    return root:gsub('\\','/'):gsub('/+$','')..'/'..file
end
local function relative(value)
    assert(type(value)=='string'and value~=''and not value:find('[%z:]'),'Invalid map manifest path')
    value=value:gsub('\\','/')
    assert(value:sub(1,1)~='/','Map manifest must be relative to map_data')
    for part in value:gmatch('[^/]+')do assert(part~='..'and part~='.','Map manifest leaves map_data')end
    return value
end
local function read_json(file,limit)
    local stream=io.open(file,'rb');if not stream then return nil,'missing'end
    local size=stream:seek('end');stream:seek('set',0)
    if not size or size>limit then stream:close();return nil,'JSON exceeds setup size limit'end
    local data=stream:read('*a');stream:close()
    local ok,value=pcall(json.decode,data)
    if not ok or type(value)~='table'then return nil,'Invalid setup JSON'end
    return value
end
local function write(file,value)
    local stream=assert(io.open(path(config.mailbox,file),'w'))
    local ok,why=pcall(function()assert(stream:write(json.encode(value)))end)
    local closed,closeError=stream:close()
    assert(ok,why);assert(closed,closeError)
end
local function hide_hud()
    local hud=find(hudName);if hud then pcall(function()hud:RemoveFromParent()end)end
    hudName=nil;textName=nil
end
local function show(pawn,message)
    if message~=lastMessage then print('[SkateSetup] '..message..'\n');lastMessage=message end
    if not valid(pawn)then return end
    local ok,why=pcall(function()
        local text=find(textName)
        if not text then
            local hud=StaticConstructObject(StaticFindObject('/Script/UMG.UserWidget'),UE.GetGameInstance())
            hud.WidgetTree=StaticConstructObject(StaticFindObject('/Script/UMG.WidgetTree'),hud)
            local canvas=StaticConstructObject(StaticFindObject('/Script/UMG.CanvasPanel'),hud.WidgetTree)
            hud.WidgetTree.RootWidget=canvas
            local border=StaticConstructObject(StaticFindObject('/Script/UMG.Border'),canvas)
            border:SetBrushColor({R=0.015,G=0.015,B=0.015,A=0.85})
            border:SetPadding({Left=12,Top=8,Right=12,Bottom=8})
            text=StaticConstructObject(StaticFindObject('/Script/UMG.TextBlock'),border)
            text.Font.Size=18;text:SetColorAndOpacity({SpecifiedColor={R=1,G=0.85,B=0.3,A=1},ColorUseRule=0})
            border:SetContent(text)
            local slot=canvas:AddChildToCanvas(border);slot:SetAutoSize(true)
            slot:SetAnchors({Minimum={X=0.5,Y=0.28},Maximum={X=0.5,Y=0.28}})
            slot:SetAlignment({X=0.5,Y=0});slot:SetPosition({X=0,Y=0})
            canvas:SetVisibility(4);border:SetVisibility(4);hud:AddToViewport(50)
            hudName=name(hud);textName=name(text)
        end
        text:SetText(FText(message))
    end)
    if not ok then state.hud_error=tostring(why)end
end
local function publish(pawn,phase,message,record)
    state.status={schema='S3SETUP1',phase=phase,message=message,updated_at_unix=os.time(),
        game_root=config.game_root,game_build=config.game_build,
        paths={assets=config.assets,map_data=config.map_data,mailbox=config.mailbox},
        assets=state.assets,map=state.map,world=valid(pawn)and name(pawn:GetWorld())or nil,
        pawn=name(pawn),capture=record,launcher=state.launcher,error=state.error,
        validation='Headers only; the launcher verifies package, asset layout and complete map files. The source Session validates imported assets.'}
    local ok,why=pcall(write,'setup-status.json',state.status)
    if not ok then state.status.status_write_error=tostring(why)end
    show(pawn,message)
    return ok
end
local function check_assets()
    if state.assets and state.assets.state~='missing'then return end
    local file=path(config.assets,'private/game.json')
    local document,why=read_json(file,1024*1024)
    state.assets={path=file,state='missing'}
    if not document then
        if why~='missing'then state.assets.state='invalid';state.assets.error=why end
    elseif document.version~=1 then
        state.assets.state='invalid';state.assets.error='Unsupported Skate asset manifest version'
    else state.assets.state='ready';state.assets.version=document.version end
end
local function fingerprint(value)return type(value)=='string'and #value==64 and value:match('^%x+$')~=nil end
local function check_map()
    if state.map and state.map.state~='missing'then return end
    local file=path(config.map_data,'compact-world-base.json')
    state.map={path=file,state='missing'}
    local pointer,why=read_json(file,16*1024*1024)
    if not pointer then
        if why~='missing'then state.map.state='invalid';state.map.error=why end
        return
    end
    local ok,problem=pcall(function()
        assert(pointer.schema==1 and fingerprint(pointer.source_fingerprint),'Invalid compact-world pointer schema or fingerprint')
        local manifestFile=path(config.map_data,relative(pointer.manifest))
        state.map.manifest=manifestFile
        local manifest,reason=read_json(manifestFile,32*1024*1024)
        if not manifest and reason=='missing'then return end
        assert(manifest,reason)
        assert(manifest.magic=='S3W1'and manifest.schema==1,'Unsupported compact-world manifest')
        assert(manifest.source_fingerprint==pointer.source_fingerprint,'Compact-world fingerprint mismatch')
        assert(type(manifest.completeness)=='table'and manifest.completeness.complete==true,'Compact-world preparation is incomplete')
        state.map.state='ready';state.map.schema=manifest.schema;state.map.magic=manifest.magic
        state.map.source_fingerprint=manifest.source_fingerprint
    end)
    if not ok then state.map.state='invalid';state.map.error=tostring(problem)end
end
local function capture_key(pawn)
    return tostring(config.game_root or '')..'|'..tostring(config.game_build or '')..'|'..
        tostring(name(pawn:GetWorld()))..'|'..tostring(name(pawn))
end
local function capture_policy(pawn)
    local key=capture_key(pawn);local record=state.captures[key]
    if not record then
        record={key=key,attempts=0,next_attempt=0};state.captures[key]=record
        local previous=read_json(path(config.mailbox,'collision-policy-probe.json'),4*1024*1024)
        local provenance=previous and previous.setup_provenance
        if previous and previous.schema=='S3COLLISIONPOLICY1'and previous.complete==true and
            provenance and provenance.key==key then
            record.complete=true;record.attempts=1;record.usmap_requested_at_unix=provenance.usmap_requested_at_unix
            record.dump_attempted=provenance.dump_attempted==true;record.dump_outcome=provenance.dump_outcome
            record.error=provenance.dump_error
        end
    end
    if record.complete and record.dump_attempted then return record end
    if record.attempts>=3 or os.time()<record.next_attempt then return record end
    record.attempts=record.attempts+1;record.next_attempt=os.time()+5
    publish(pawn,'capturing_policy','Skateboard setup: collecting local collision settings.',record)
    local ok,result=pcall(function()return require('collision_policy').capture(pawn)end)
    if not ok or type(result)~='table'or result.complete~=true then
        record.error=ok and'Collision settings capture is incomplete'or tostring(result)
        record.capture_errors=type(result)=='table'and result.errors or nil
        if type(result)=='table'then pcall(write,'collision-policy-probe.json',result)end
        return record
    end
    record.complete=true;record.error=nil;record.capture_errors=nil
    -- Persist the attempt BEFORE invoking DumpUSMAP. Its void return does not
    -- prove a mapping file exists; the offline setup verifies the output file.
    record.dump_attempted=true;record.usmap_requested_at_unix=os.time();record.dump_outcome='requested'
    result.setup_provenance={key=key,game_root=config.game_root,game_build=config.game_build,
        captured_at_unix=os.time(),dump_attempted=true,usmap_requested_at_unix=record.usmap_requested_at_unix,
        dump_outcome=record.dump_outcome}
    local saved,saveError=pcall(write,'collision-policy-probe.json',result)
    if not saved then
        record.complete=false;record.dump_attempted=false;record.error='Cannot save collision settings: '..tostring(saveError)
        return record
    end
    if not publish(pawn,'dumping_mappings','Skateboard setup: saving local asset mappings.',record)then
        record.error='Cannot save setup status; mapping dump not started';record.dump_outcome='not_started'
        result.setup_provenance.dump_outcome=record.dump_outcome;result.setup_provenance.dump_error=record.error
        pcall(write,'collision-policy-probe.json',result);return record
    end
    local dumped,dumpError=pcall(function()assert(type(DumpUSMAP)=='function','DumpUSMAP unavailable');DumpUSMAP()end)
    record.dump_outcome=dumped and'returned'or'error';record.error=not dumped and tostring(dumpError)or nil
    result.setup_provenance.dump_outcome=record.dump_outcome;result.setup_provenance.dump_error=record.error
    pcall(write,'collision-policy-probe.json',result)
    return record
end
local function cancel_loop()
    if not loopHandle then return true end
    if CancelDelayedAction(loopHandle)~=true then return false end
    loopHandle=nil;return true
end
function M.shutdown()
    local ok,result=pcall(cancel_loop);hide_hud();return ok and result
end
local function start_loop()
    assert(not loopHandle,'Duplicate bootstrap loop')
    assert(EngineTickAvailable and type(CancelDelayedAction)=='function','Setup requires cancellable engine ticks')
    loopHandle=LoopInGameThreadAfterFrames(1,function()
        local ok,why=pcall(tick)
        if not ok then
            state.errors=state.errors+1;state.error=tostring(why);state.next_poll=os.time()+5
            if state.errors>=3 then state.halted=true end
            local readable,pc=pcall(UE.GetPlayerController);local pawn=readable and valid(pc)and pc.Pawn or nil
            pcall(publish,pawn,'setup_error','Skateboard setup: configuration error. See setup-status.json.',nil)
        end
    end)
    assert(type(loopHandle)=='number','Bootstrap loop handle unavailable')
end
local function handoff(pawn)
    if state.start_attempted then return end
    state.start_attempted=true
    publish(pawn,'starting','Skateboard setup: starting prepared mod.',nil)
    local ok,why=pcall(function()
        local transport=require('shared_transport')
        local result=transport.startRelay();state.launcher=result
        assert(result=='manual'or(type(result)=='string'and result:match('^started:%d+$')),
            'Relay startup failed: '..tostring(result))
        assert(cancel_loop(),'Could not cancel bootstrap before starting bridge')
        hide_hud()
        require('bridge')
        state.started=true
    end)
    if not ok then
        state.error=tostring(why);state.halted=true
        publish(pawn,'startup_error','Skateboard setup: startup failed. See setup-status.json.',nil)
        if not loopHandle then start_loop()end -- F10 remains available; no repeated launch
    else publish(nil,'bridge_started','Skateboard setup: prepared mod started.',nil)end
end
tick=function()
    local pc=UE.GetPlayerController();local pawn=valid(pc)and pc.Pawn or nil
    if valid(pc)then
        local down=pc:IsInputKeyDown({KeyName=FName('F10')})
        if down and reloadDown==false then
            assert(M.shutdown(),'Could not stop bootstrap for reload');RestartCurrentMod();return
        end
        reloadDown=down
    else reloadDown=nil end
    if state.halted or state.started or os.time()<state.next_poll then return end
    state.next_poll=os.time()+2
    check_assets();check_map()
    if state.assets.state=='ready'and state.map.state=='ready'then handoff(pawn);return end
    local playable=valid(pawn)and pawn:GetClass():GetFName():ToString()=='BP_PlayerCharacter_C'and
        UE.GetKismetSystemLibrary():IsStandalone(pawn)
    if not playable then
        hide_hud();publish(nil,'needs_solo_world','Skateboard setup: enter a solo world to collect setup settings.',nil);return
    end
    local record=capture_policy(pawn)
    if record.error then
        publish(pawn,'capture_error','Skateboard setup: could not finish settings. See setup-status.json.',record);return
    end
    local missingAssets=state.assets.state~='ready';local missingMap=state.map.state~='ready'
    local detail=missingAssets and(missingMap and'import owned Skate assets and prepare the map'or'import owned Skate assets')or'prepare the map'
    local phase=(state.assets.state=='invalid'or state.map.state=='invalid')and'invalid_prepared_data'or'needs_offline_setup'
    publish(pawn,phase,'Skateboard setup: close the game and run offline setup to '..detail..'.',record)
end
function M.status()return state.status end
local previous=rawget(_G,'__DragonwildsSkateBootstrap')
if previous and previous.shutdown then assert(previous.shutdown(),'Previous bootstrap cleanup failed')end
_G.__DragonwildsSkateBootstrap=M
start_loop()
return M
