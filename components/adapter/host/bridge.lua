-- Host adapter only. All skating is supplied by skate-host in the local worker.
local UE=require("UEHelpers")
local json=require("json")
local frameCodec=require('frame_codec')
local transport=require('shared_transport')
local scene=require('scene_collision')
local input=require('skate_input')
local mount=require('skate_mount')
local mountControl=require('mount_control')
local gameplayGate=require('gameplay_input_gate')
local spellWheel=require('spell_wheel_block')
local quickWheel=require('quick_wheel_block')
local entryFloor=require('skate_floor')
local walkingCamera=require('skate_camera')
local config=require('skate_config')
local buildingProbeStarted=false
local clock=transport.clock
local box=config.mailbox
local function valid(o) return o and o:IsValid() end
local function log(s) print("[SkateBridge] "..s.."\n") end
local function read(name)
    if name=='status.json' then
        local text=transport.readStatus()
        if not text then return nil end
        local ok,data=pcall(json.decode,text);if ok then return data end
        return nil
    end
    local f=io.open(box..name,"r"); if not f then return nil end
    local text=f:read("*a"); f:close()
    local ok,data=pcall(json.decode,text); if ok then return data end
end
local function write(name,data)
    if name=="host.json" then
        assert(transport.writeHost(json.encode(data)),'Shared host channel unavailable')
        return
    end
    -- Readers reject partial JSON. No delete/rename while a Windows reader may
    -- have the destination open; host heartbeats rotate through four slots.
    local f=assert(io.open(box..name,"w")); f:write(json.encode(data)); f:close()
end
local identity={Rotation={X=0,Y=0,Z=0,W=1},Translation={X=0,Y=0,Z=0},Scale3D={X=1,Y=1,Z=1}}
local pawn,pc,anchor,generation,capture,actor,proc,cameraActor
local poseLeader,boardParts
local rootPose={Translation={X=0,Y=0,Z=0},Rotation={X=0,Y=0,Z=0,W=1},Scale3D={X=1,Y=1,Z=1}}
local boardPoseCache={}
local function update_transform(t,v,n)
    t.Translation.X=v[n+1];t.Translation.Y=v[n+2];t.Translation.Z=v[n+3]
    t.Rotation.X=v[n+4];t.Rotation.Y=v[n+5];t.Rotation.Z=v[n+6];t.Rotation.W=v[n+7]
    t.Scale3D.X=v[n+8];t.Scale3D.Y=v[n+9];t.Scale3D.Z=v[n+10]
end
local active=false
local prepared=false
local epoch=0
local seq=0
local saved=nil
local restorePending,pawnIdentity,controllerIdentity
local restoreRetryAt=0
local lastRestoreError
local ridingQueryPolicy
local currentSpawn,currentHeading
local entryMode='activate'
local selectedCamera='high'
local entryOrigin='debug'
local gameplayInputAllowed=false
local mountOptions
local created=false
local lastTick=-1
local pendingToggle=false
local entryStarted,entryPosition
local entrySupport,entryRetry=false,false
local enterAfterRefresh=false
local lastFrameTime=0
local worldSeen={}
local preparationFailures=0
local hud,hudText
local loadingHudSince,loadingHudShown
local loadingProgress,loadingProgressAt,loadingProgressToken
local statusText="Dragonwilds Skate: preparing"
local autoEnter=false
local lastFrameSerial=nil
local lastSafePosition,lastGroundCheck
local worldKillZ
local worldSession
local worldWarmup
-- This is diagnostic evidence, not a verified dynamic collider source yet.
-- Capture it once per world per adapter load, rather than on every scene scan.
local buildingEvidenceSessions={}
local lastStatusError
local lastStatusHeartbeat
local lastMountError
local lastControlError
local lastControlStatus=-math.huge
local timing={count=0,bones=0,mesh=0,read=0,index=0,file=0,decode=0,started=clock()}
local function render_loading(text)
    if not valid(hudText) then
        local gi=UE.GetGameInstance()
        hud=StaticConstructObject(StaticFindObject("/Script/UMG.UserWidget"),gi)
        hud.WidgetTree=StaticConstructObject(StaticFindObject("/Script/UMG.WidgetTree"),hud)
        local canvas=StaticConstructObject(StaticFindObject("/Script/UMG.CanvasPanel"),hud.WidgetTree)
        hud.WidgetTree.RootWidget=canvas
        local border=StaticConstructObject(StaticFindObject("/Script/UMG.Border"),canvas)
        border:SetBrushColor({R=0.015,G=0.015,B=0.015,A=0.85})
        border:SetPadding({Left=12,Top=8,Right=12,Bottom=8})
        hudText=StaticConstructObject(StaticFindObject("/Script/UMG.TextBlock"),border)
        hudText.Font.Size=18
        hudText:SetColorAndOpacity({SpecifiedColor={R=1,G=0.85,B=0.3,A=1},ColorUseRule=0})
        border:SetContent(hudText)
        local slot=canvas:AddChildToCanvas(border)
        slot:SetAutoSize(true)
        slot:SetAnchors({Minimum={X=0.5,Y=0.28},Maximum={X=0.5,Y=0.28}})
        slot:SetAlignment({X=0.5,Y=0});slot:SetPosition({X=0,Y=0})
        canvas:SetVisibility(4);border:SetVisibility(4)
        hud:AddToViewport(50)
    end
    hudText:SetText(FText(text))
end
local function hide_loading()
    if valid(hud)then hud:RemoveFromParent()end
    hud=nil;hudText=nil;loadingHudShown=nil
end
local progressLabels={
    preparing_scene='Preparing object collision',checking_saved_world='Checking saved world collision',
    collision_load='Loading world collision',preparing_exposed_cells='Generating grind edges',
    loading_cached_edges='Loading saved grind edges',
    joining_grind_paths='Joining connected grind paths',loading_cached_grind_data='Loading saved grind paths',
    saving_grind_data='Saving grind cache',building_runtime_provider='Preparing skating collision',
    preparing_legacy_grinds='Preparing initial grind cache',ready='Finishing cache preparation'}
local function progress_number(value)
    return type(value)=='number'and value==value and value>=0 and value<=9007199254740991
end
local function loading_progress_text()
    local p=loadingProgress
    if not p or p.generation~=generation or not loadingProgressAt or clock()-loadingProgressAt>2 then return nil end
    local label=progressLabels[p.phase]
    if not label or not progress_number(p.elapsed_seconds)then return nil end
    local seconds=math.floor(p.elapsed_seconds);local minutes=math.floor(seconds/60);local hours=math.floor(minutes/60)
    local elapsed=hours>0 and string.format('%dh %02dm %02ds',hours,minutes%60,seconds%60)
        or string.format('%dm %02ds',minutes,seconds%60)
    local count=''
    if progress_number(p.completed)and p.completed%1==0 then
        if progress_number(p.total)and p.total>0 and p.total%1==0 and p.completed<=p.total then
            local percent=p.completed==p.total and 100 or math.min(99.9,math.floor(1000*p.completed/p.total+0.5)/10)
            count=string.format(' — %.0f / %.0f sections (%.1f%%)',p.completed,p.total,percent)
        else count=string.format(' — %.0f sections',p.completed)end
    end
    return 'Dragonwilds Skate: '..label..count..'\nElapsed '..elapsed
end
local function update_loading_hud()
    local text
    if valid(pawn)then
        if pendingToggle then text='Dragonwilds Skate: getting your skateboard ready...'
        elseif enterAfterRefresh then text='Dragonwilds Skate: updating nearby surfaces...'
        elseif not prepared and generation and preparationFailures<3 then text='Dragonwilds Skate: preparing skateboarding...'
        elseif restorePending or(not active and(spellWheel.pending()or quickWheel.pending()))then
            text='Dragonwilds Skate: finishing skateboard mode...'
        end
    end
    if not text then loadingHudSince=nil;hide_loading();return end
    if pendingToggle or enterAfterRefresh or not prepared then text=loading_progress_text()or text end
    -- Fast cached summons need no flashing banner. A sustained wait becomes
    -- visible, then disappears on the first ready frame. Background updates
    -- while the accepted world remains playable do not display a banner.
    loadingHudSince=loadingHudSince or clock()
    if clock()-loadingHudSince>=0.35 and(text~=loadingHudShown or not valid(hud)or not valid(hudText))then
        render_loading(text);loadingHudShown=text
    end
end
local function ready_hint(mountReady,mountStatus,controlReady,controlStatus,inputAllowed)
    if not mountReady then return 'Open Mounts and equip Skateboard' end
    if not mountStatus.selected then return 'Select Skateboard in Mounts, then press Equip' end
    if controlStatus.waiting_reason=='ordinary_mount_active' then return 'Dismount your ordinary mount first' end
    if controlStatus.error then return 'Mount controls unavailable; see adapter log' end
    if not inputAllowed then return 'Close menus to use your mount control' end
    if controlStatus.waiting_reason=='native_mount_blocked' then return 'Game mount restriction active in this area or effect' end
    if controlStatus.waiting_reason=='falling' then return 'Stand on solid ground to summon' end
    if controlStatus.waiting_reason=='interacting' then return 'Finish interacting before summoning' end
    if controlStatus.waiting_reason=='teleporting' then return 'Finish travelling before summoning' end
    if not controlReady then return 'Mount unavailable in the current state' end
    return 'Use your mount control | R3 / V camera'
end
local function message(text)
    statusText=text
    log(text)
end
local function supporting_floor()
    local hit,status=entryFloor.find(pawn)
    pcall(write,'entry-floor.json',status)
    if status.error then log('ENTRY FLOOR '..tostring(status.error))end
    return hit,status
end
local function floor_message(status,preparing)
    if status and(status.error or status.reason=='floor_query_unavailable')then
        return 'Dragonwilds Skate: ground check failed; see adapter log'
    end
    return preparing and 'Dragonwilds Skate: stand on solid ground before preparing' or 'Dragonwilds Skate: ground unavailable'
end
local function skate(p) return {(p.X-anchor.X)*0.01,(p.Z-anchor.Z)*0.01,-(p.Y-anchor.Y)*0.01} end
local function vector(p) return {X=p[1],Y=p[2],Z=p[3]} end
local function vectors(a) local r={} for i,v in ipairs(a) do r[i]=vector(v) end return r end
local function object_identity(object)
    assert(valid(object),'Adapter state owner unavailable')
    local address=object:GetAddress()
    assert(type(address)=='number'and address>0 and address%1==0,'Adapter state owner identity unavailable')
    return {address=address,name=object:GetFullName()}
end
local function same_owner(object,identity)
    return valid(object)and identity and object:GetAddress()==identity.address and object:GetFullName()==identity.name
end
local function wheels_pending()return spellWheel.pending()or quickWheel.pending()end
local function restore_owned(preserveMovement)
    -- Input modes are shared assets, so their spell-wheel lease must be
    -- released even when the character/controller has already been destroyed.
    local spellRestored,spellError=spellWheel.shutdown()
    local quickRestored,quickError=quickWheel.shutdown()
    local wheelRestored=spellRestored and quickRestored
    local wheelError=not spellRestored and spellError or quickError
    if not wheelRestored then
        restoreRetryAt=clock()+0.25
        if wheelError~=lastRestoreError then lastRestoreError=wheelError;log('WHEEL RESTORE '..tostring(wheelError))end
    end
    local state=restorePending
    if not state then return wheelRestored end
    if preserveMovement==true then state.deferMode=true
    elseif preserveMovement==false then state.deferMode=nil end
    local errors={}
    local function restore(field,fn)
        if not state[field]then return end
        local ok,why=pcall(fn)
        if ok then state[field]=nil else errors[#errors+1]=field..': '..tostring(why)end
    end
    local pawnOK,ownsPawn=pcall(same_owner,state.pawn,state.pawnIdentity)
    if pawnOK and not ownsPawn then
        -- A destroyed/replaced pawn no longer owns these mutations. Never apply
        -- its saved state to a new pawn, even if the engine reuses its name.
        state.changedMode=nil;state.changedCollision=nil;state.changedHidden=nil
    elseif pawnOK then
        restore('changedCollision',function()
            if state.pawn:GetActorEnableCollision()~=state.collision then state.pawn:SetActorEnableCollision(state.collision)end
            assert(state.pawn:GetActorEnableCollision()==state.collision,'Collision restoration did not take effect')
        end)
        restore('changedHidden',function()
            if state.pawn.bHidden~=state.hidden then state.pawn:SetActorHiddenInGame(state.hidden)end
            assert(state.pawn.bHidden==state.hidden,'Visibility restoration did not take effect')
        end)
        if state.changedMode then
            local ok,current=pcall(function()return state.pawn.CharacterMovement.MovementMode end)
            if not ok then errors[#errors+1]='movement state unavailable: '..tostring(current)
            elseif current~=0 then state.changedMode=nil -- native death/respawn already took ownership
            elseif not state.deferMode then
                restore('changedMode',function()
                    state.pawn.CharacterMovement:SetMovementMode(state.mode,0)
                    assert(state.pawn.CharacterMovement.MovementMode==state.mode,'Movement restoration did not take effect')
                end)
            end
        end
    else errors[#errors+1]='pawn identity unavailable'end
    local pcOK,ownsPC=pcall(same_owner,state.pc,state.pcIdentity)
    if pcOK and not ownsPC then
        state.ignoreMove=nil;state.ignoreLook=nil;state.viewChanged=nil
    elseif pcOK then
        -- Controller claims outlive the pawn during respawn. Release exactly
        -- the claims we acquired even when that pawn has already disappeared.
        restore('ignoreMove',function()state.pc:SetIgnoreMoveInput(false)end)
        restore('ignoreLook',function()state.pc:SetIgnoreLookInput(false)end)
        restore('viewChanged',function()
            -- Possession may already have moved to a respawned pawn. Return the
            -- view to that controller's current pawn, never to the old corpse.
            local target=state.pc.Pawn
            if valid(target)then state.pc:SetViewTargetWithBlend(target,0,0,0,false)end
        end)
    else errors[#errors+1]='controller identity unavailable'end
    local complete=not(state.changedMode or state.changedCollision or state.changedHidden or state.ignoreMove or state.ignoreLook or state.viewChanged)
    if complete and wheelRestored then restorePending=nil;lastRestoreError=nil;return true end
    restoreRetryAt=clock()+0.25
    local why=table.concat(errors,'; ')
    if why~=''and why~=lastRestoreError then lastRestoreError=why;log('RESTORE PENDING '..why)end
    return false
end
local function stopped(preserveMovement)
    active=false; pendingToggle=false;autoEnter=false
    entryStarted=nil;entryPosition=nil;entrySupport=nil
    ridingQueryPolicy=nil
    enterAfterRefresh=false
    entryOrigin='debug'
    if saved then assert(not restorePending,'Unrestored adapter state was overwritten');restorePending=saved;saved=nil end
    local restored=restore_owned(preserveMovement)
    if valid(actor) then pcall(function() actor:K2_DestroyActor() end) end
    if valid(cameraActor) then pcall(function() cameraActor:K2_DestroyActor() end) end
    actor=nil;proc=nil;poseLeader=nil;boardParts=nil;cameraActor=nil;created=false
    return restored
end
local pendingWindow,collisionCenter,collisionRadius
local worldRevision,lastSceneTime,lastFullSceneTime,lastAcceptedFingerprint
local latestScene,sceneDirty,lastRequestedRevision=nil,false,0
local wantedCenter,lastFullSceneCenter,physicsSettings
local queuedDirty,lastDirtyPoll,eventInstalledFor,forceFullScene={},0,nil,false
local sceneRetryAt=0
local buildings={nextAt=0,auditAt=math.huge,latestObserved=0}
local function building_enabled()return config.building_collision==true end
local function building_runner()return require('building_capture_runner')end
local function reset_buildings()
    if building_enabled() then
        -- A queued cache request can still be reading its immutable report.
        -- Leave its files intact when changing worlds/reloading the adapter.
        local runner=building_runner();runner.cancel()
        if buildings.auditRunner then buildings.auditRunner.cancel()end
        for _,key in ipairs({'result','auditResult'})do
            local result=buildings[key]
            if result and not(pendingWindow and pendingWindow.buildingCaptureId==result.capture_id)then
                local owner=key=='auditResult' and buildings.auditRunner or runner
                if owner and owner.discard then pcall(owner.discard,result)end
            end
        end
    end
    buildings={nextAt=0,auditAt=clock()+180,baselineMissing=false,latestObserved=0}
end
local function advance_building_pass(runner,key,nextKey,incremental)
    if not runner.busy() then
        if buildings[key] or clock()<buildings[nextKey] then return end
        local ok,begun,why=pcall(runner.begin,pawn,{state2=true,packets=true,unique_report=true,incremental=incremental})
        if not ok or begun==false then
            buildings[nextKey]=clock()+2;log('BUILDING START ERROR '..tostring(ok and why or begun));return
        end
    end
    local ok,result=pcall(runner.tick)
    if not ok then runner.cancel();result={complete=false,error=tostring(result)}end
    if not result then return end
    if result.complete~=true or result.collision_verified~=true or result.world_session~=worldSession or
            type(result.observed_serial)~='number' or result.observed_serial<1 or result.observed_serial%1~=0 then
        if runner.discard then pcall(runner.discard,result)end;buildings[nextKey]=clock()+2
        log('BUILDING CAPTURE ERROR '..tostring(result.error or 'Incomplete or mismatched world inventory'));return
    end
    if result.observed_serial<=buildings.latestObserved then
        -- Publication may take many frames after final native validation. Its
        -- finish time cannot order two independently collected observations.
        if runner.discard then pcall(runner.discard,result)end
        buildings[nextKey]=clock()+2;return
    end
    buildings.latestObserved=result.observed_serial;buildings[key]=result
    local otherKey=key=='result' and 'auditResult' or 'result'
    local previous=buildings[otherKey]
    if previous and not(pendingWindow and pendingWindow.buildingCaptureId==previous.capture_id)then
        local owner=otherKey=='auditResult' and buildings.auditRunner or building_runner()
        if owner.discard then pcall(owner.discard,previous)end
        buildings[otherKey]=nil;buildings[otherKey=='auditResult' and 'auditAt' or 'nextAt']=clock()+8
    end
    log('BUILDINGS ready capture='..result.capture_id..' elapsed_ms='..math.floor((result.elapsed_seconds or 0)*1000))
end
local function advance_buildings()
    if not building_enabled() or not generation then return end
    -- Both readers run during the expensive native-body capture. Once its
    -- final validation finishes, let that full audit publish and install before
    -- collecting newer descriptor-only observations. They contain no evidence
    -- that could supersede changed body filters on otherwise identical pieces.
    -- This barrier affects only collection: riding and the installed collision
    -- continue, and earlier native requests drain before the audit is queued.
    local auditPublishing=buildings.auditResult~=nil
    if not auditPublishing and buildings.auditRunner and buildings.auditRunner.busy()then
        local status=buildings.auditRunner.status()
        auditPublishing=status and status.phase=='write'
    end
    if auditPublishing then
        local priority=building_runner()
        -- A half-read delta may already contain body rows older than this
        -- audit. Resuming it would attach a newer completion serial to stale
        -- native evidence, so discard only that unfinished capture and begin
        -- again against the accepted baseline after the audit resolves.
        -- Completed/submitted immutable reports are not part of runner.busy().
        if priority.busy()then priority.cancel();buildings.nextAt=clock()end
    else advance_building_pass(building_runner(),'result','nextAt',true)end
    if buildings.auditRunner or clock()>=buildings.auditAt then
        if not buildings.auditRunner then buildings.auditRunner=building_runner().new()end
        advance_building_pass(buildings.auditRunner,'auditResult','auditAt',false)
    end
end
local function finish_building_request(window,accepted)
    local key=window.buildingKind=='audit' and 'auditResult' or 'result'
    local result=buildings[key]
    if result and window.buildingCaptureId==result.capture_id then
        local runner=key=='auditResult' and buildings.auditRunner or building_runner()
        if runner.discard then pcall(runner.discard,result)end
        buildings[key]=nil
        buildings[key=='auditResult' and 'auditAt' or 'nextAt']=clock()+(accepted and (key=='auditResult' and 180 or 8) or 2)
    end
end
local function remember_dirty(packages)
    for _,package in ipairs(packages or {}) do queuedDirty[package]=true end
end
local function next_dirty(urgent)
    if not urgent and clock()-lastDirtyPoll<0.5 then return nil,next(queuedDirty)~=nil end
    lastDirtyPoll=clock();remember_dirty(scene.take_dirty(8))
    local packages={};for package in pairs(queuedDirty) do packages[#packages+1]=package end
    table.sort(packages);while #packages>8 do packages[#packages]=nil end
    if #packages==0 then return nil end
    return packages
end
worldRevision=0;lastSceneTime=0;lastFullSceneTime=0
local function report_guard(reason,frame,proposed,ground)
    -- Diagnostics run only when an existing guard fires. A failed read/write
    -- must never interrupt restoration of the real player or camera.
    local ok,why=pcall(function()
        local function xyz(v) if v then return {v.X,v.Y,v.Z} end end
        local report={schema='S3GUARD1',reason=reason,clock=clock(),generation=generation,
            epoch=epoch,seq=seq,tick=frame.tick,state=frame.state,root=frame.root,velocity=frame.velocity,
            anchor=xyz(anchor),proposed=xyz(proposed),halfheight=saved and saved.halfheight,
            coverage_center=xyz(collisionCenter),coverage_radius_cm=collisionRadius,
            latest_scene_revision=latestScene and latestScene.revision,pending_window=pendingWindow,
            last_safe_position=xyz(lastSafePosition),active=active,prepared=prepared}
        if ground then
            report.ground={impact_point=xyz(ground.ImpactPoint),impact_normal=xyz(ground.ImpactNormal),
                normal=xyz(ground.Normal)}
            local componentOK,componentName=pcall(function()
                local component=ground.Component and ground.Component:Get()
                if valid(component) then return component:GetFullName() end
            end)
            if componentOK then report.ground.component=componentName
            else report.ground.component_error=tostring(componentName) end
        end
        local statusOK,status=pcall(read,'status.json')
        if statusOK then report.status=status else report.status_error=tostring(status) end
        local probeOK,probe=pcall(function()
            return require('ground_probe').inspect(pawn,proposed,saved and saved.halfheight)
        end)
        if probeOK then report.ground_probe=probe else report.ground_probe_error=tostring(probe) end
        local name='collision-guard-'..tostring(generation or 'unknown')..'-'..tostring(epoch)..'-'..
            tostring(frame.tick or lastTick)..'-'..reason..'.json'
        write(name,report)
        log('GUARD REPORT '..name)
    end)
    if not ok then pcall(log,'GUARD REPORT ERROR '..tostring(why)) end
end
local function request_window(cx,cy,z,revision,snapshot)
    local current=snapshot or latestScene
    if pendingWindow or not current then return false end
    if not revision or revision<=lastRequestedRevision then
        worldRevision=worldRevision+1;revision=worldRevision
    end
    local descriptor={mode='whole_world',world_session=worldSession,generation=generation,revision=revision,scene_revision=current.revision,
        anchor={anchor.X,anchor.Y,anchor.Z},center={cx,cy,z},
        heading=math.rad(pawn:K2_GetActorRotation().Yaw+90),scene_file=current.snapshot_name}
    assert(transport.writeCollision('S3C2'..json.encode(descriptor)),'Cannot publish cached map request')
    pendingWindow={revision=revision,center=descriptor.center,
        scene_revision=current.revision,packages=current.partial and current.packages or nil,
        fullScan=current._fullScan,globalScan=current._globalScan,buildingOnly=snapshot~=nil,
        buildingCaptureId=current.building_capture_id,buildingKind=current._buildingKind}
    lastRequestedRevision=revision;if not snapshot then sceneDirty=false end
    log('CACHED COLLISION requested revision='..revision..' scene='..current.revision..' objects='..#current.objects)
    if not prepared then message('Dragonwilds Skate: loading permanent world collision and grinding edges') end
    return true
end
local function request_buildings(result,kind)
    if pendingWindow then return false end
    worldRevision=worldRevision+1
    local snapshot=scene.publish_buildings(pawn,{generation=generation,revision=worldRevision,
        physicsSettings=physicsSettings,queryPolicy=active and ridingQueryPolicy or nil,
        world_session=worldSession,building=result})
    snapshot._buildingKind=kind
    local center=(active and wantedCenter) or pawn:K2_GetActorLocation()
    return request_window(center.X,center.Y,center.Z,worldRevision,snapshot)
end
local function begin_window(cx,cy,z,full,packages)
    if capture or pendingWindow or scene.busy() then return false end
    if clock()<sceneRetryAt then return false end
    worldRevision=worldRevision+1
    capture={revision=worldRevision,center={cx,cy,z},full=full~=false and packages==nil,packages=packages}
    local excluded={};if valid(actor) then excluded[#excluded+1]=actor:GetFullName() end
    local ok,begun,why=pcall(scene.begin,pawn,{generation=generation,revision=worldRevision,reuse_names=full==false or packages~=nil,packages=packages,
        exclude_owners=excluded,clock=clock,physicsSettings=physicsSettings,
        -- Building evidence is a large diagnostic inventory, not collision.
        -- Keep it opt-in instead of repeating it during every normal startup.
        captureBuildings=not building_enabled() and config.capture_building_diagnostics==true and worldSession~=nil and not buildingEvidenceSessions[worldSession],
        buildingInventory=building_enabled() and 'retain_verified' or nil,
        queryPolicy=active and ridingQueryPolicy or nil})
    if not ok or begun==false then
        capture=nil;remember_dirty(packages)
        sceneRetryAt=clock()+2
        if not packages then forceFullScene=true;lastSceneTime=clock()-6 end
        log('SCENE START ERROR '..tostring(ok and why or begun));return false
    end
    sceneRetryAt=0
    for _,package in ipairs(packages or {}) do queuedDirty[package]=nil end
    return true
end
local function begin_capture()
    local pos=pawn:K2_GetActorLocation()
    local ground,floorStatus=supporting_floor()
    if not ground then message(floor_message(floorStatus,true));return false end
    remember_dirty(capture and capture.packages);remember_dirty(pendingWindow and pendingWindow.packages)
    reset_buildings();scene.cancel();capture=nil;pendingWindow=nil
    -- One permanent coordinate system keeps collision, welded seams and grind
    -- caches identical across travel and future activations.
    anchor={X=0,Y=0,Z=0}
    local worldInfo=require('skate_world').read(pawn)
    worldSession=worldInfo.session;worldKillZ=worldInfo.kill_z
    require('host_rig').prepare(pawn,anchor)
    generation=tostring(os.time())..'-'..tostring(math.floor(clock()*1000))
    worldWarmup={mode='whole_world',generation=generation,world_session=worldSession,world=worldInfo.world,
        anchor={0,0,0},center={pos.X,pos.Y,ground.ImpactPoint.Z},heading=math.rad(pawn:K2_GetActorRotation().Yaw+90)}
    prepared=false;collisionCenter=nil;collisionRadius=nil;worldRevision=0
    preparationFailures=0
    latestScene=nil;sceneDirty=false;lastRequestedRevision=0
    wantedCenter=nil;lastFullSceneCenter=nil
    physicsSettings=require('physics_settings').capture()
    lastAcceptedFingerprint=nil;lastSceneTime=0;lastFullSceneTime=0;forceFullScene=false
    begin_window(pos.X,pos.Y,ground.ImpactPoint.Z,true)
    if building_enabled() then
        request_buildings(nil)
        message('Dragonwilds Skate: loading saved world collision')
    else message('Dragonwilds Skate: preparing cached map and object collision')end
    return true
end
local function advance_capture()
    local result=scene.advance(prepared and 64 or 256,prepared and 0.0015 or 0.004)
    if not result then return end
    local cap=capture;capture=nil
    if not cap then return end
    if result.building_state and worldSession then
        buildingEvidenceSessions[worldSession]=true
        log('BUILDING EVIDENCE '..tostring(result.snapshot_name)..' verified=false')
    end
    -- Frequent package edits must not postpone the periodic full-scene audit.
    if not cap.packages then lastSceneTime=clock() end
    if result.complete==false or (result.error_count and result.error_count>0) then
        -- A streaming object changing during collection invalidates this
        -- snapshot, not the permanent world already installed in the Session.
        -- Retry discovery automatically; keep the last complete object overlay.
        sceneRetryAt=clock()+2
        if cap.packages then remember_dirty(cap.packages)
        else forceFullScene=true;lastSceneTime=clock()-6 end
        if not prepared then message('Dragonwilds Skate: retrying object discovery') end
        log('SCENE ERROR '..json.encode(result.errors));return
    end
    if cap.full then
        lastFullSceneTime=lastSceneTime;forceFullScene=false
        lastFullSceneCenter={X=cap.center[1],Y=cap.center[2],Z=cap.center[3]}
    end
    latestScene=result;latestScene._fullScan=cap.full;latestScene._globalScan=not cap.packages
    sceneDirty=sceneDirty or result.changed~=false or not prepared
    log('SCENE ready revision='..result.revision..' scan_ms='..math.floor(result.elapsed*1000)..' changed='..tostring(result.changed))
    if pendingWindow or not sceneDirty or buildings.baselineMissing then return end
    local center=(active and wantedCenter) or pawn:K2_GetActorLocation()
    request_window(center.X,center.Y,center.Z,cap.revision)
end
local function make_mesh(frame)
    assert(frame.body,"Host pose packet unavailable")
    local topology=assert(read("board-topology.json"),"Converted board topology unavailable")
    actor,poseLeader=require('host_rig').create(pawn,anchor,topology.host_pose_layout)
    actor:SetActorHiddenInGame(true)
    boardParts={}
    boardPoseCache={}
    for i,section in ipairs(topology.parts) do
        local part=actor:AddComponentByClass(StaticFindObject("/Script/ProceduralMeshComponent.ProceduralMeshComponent"),false,identity,false)
        local uv,colors={},{}
        for j,p in ipairs(section.uv) do uv[j]={X=p[1],Y=p[2]} end
        for j,p in ipairs(section.colors) do colors[j]={R=p[1],G=p[2],B=p[3],A=p[4]} end
        part:CreateMeshSection_LinearColor(0,vectors(section.vertices),section.triangles,vectors(section.normals),uv,{},{},{},colors,{},false,false)
        -- The imported topology starts with the deck, followed by truck bodies
        -- and separately posed wheels. Their reflection/roughness differs.
        local surface=i==1 and 'deck' or (section.bone:find('WHEEL',1,true) and 'wheel' or 'truck')
        local material=require('board_material').get(pawn,section.texture,section.tint,surface)
        part:SetMaterial(0,material)
        part:SetCollisionEnabled(0)
        boardParts[i]=part
        boardPoseCache[i]={Translation={X=0,Y=0,Z=0},Rotation={X=0,Y=0,Z=0,W=1},Scale3D={X=1,Y=1,Z=1}}
    end
    created=true
    log("HOST CHARACTER AND IMPORTED BOARD parts="..#boardParts)
end
local function present(frame)
    local started=clock()
    if pendingToggle then
        local here=pawn:K2_GetActorLocation()
        local ground=supporting_floor()
        local dx,dy,dz=here.X-entryPosition.X,here.Y-entryPosition.Y,here.Z-entryPosition.Z
        local unchanged=same_owner(pawn,saved.pawnIdentity)and pawn:GetActorEnableCollision()==saved.collision and
            pawn.bHidden==saved.hidden and pawn.CharacterMovement.MovementMode==saved.mode
        if not ground or not unchanged or dx*dx+dy*dy+dz*dz>2500 then
            stopped(true);message('Dragonwilds Skate: summon cancelled — character moved off the starting ground');return
        end
    end
    local root=frame.root
    local proposed={X=anchor.X+root[13]*100,Y=anchor.Y-root[15]*100,Z=anchor.Z+root[14]*100+saved.halfheight}
    if lastSafePosition and worldKillZ and proposed.Z<worldKillZ+1000 then
        report_guard('world_kill_z',frame,proposed)
        -- Use the original Session activation/reset at the last grounded pose.
        -- The mod remains mounted, and its permanent collision stays loaded.
        currentSpawn=skate({X=lastSafePosition.X,Y=lastSafePosition.Y,Z=lastSafePosition.Z-saved.halfheight+25})
        epoch=epoch+1;entryMode='activate';entrySupport=nil;lastTick=-1;lastFrameTime=os.time()
        pawn:K2_SetActorLocation(lastSafePosition,false,{},true)
        message('Dragonwilds Skate: returned to last grounded position');return
    end
    if frame.state=='PhysicsGround' then
        lastSafePosition={X=proposed.X,Y=proposed.Y,Z=proposed.Z}
    end
    if not created then make_mesh(frame) end
    local t=rootPose;update_transform(t,frame.rt,0)
    t.Translation.X=t.Translation.X+anchor.X;t.Translation.Y=t.Translation.Y+anchor.Y;t.Translation.Z=t.Translation.Z+anchor.Z
    actor:K2_SetActorTransform(t,false,{},true)
    local boneStarted=clock()
    require('host_rig').present(poseLeader,frame.body)
    timing.bones=timing.bones+clock()-boneStarted
    for i,part in ipairs(boardParts) do local t=boardPoseCache[i];update_transform(t,frame.board,(i-1)*10);part:K2_SetRelativeTransform(t,false,{},true) end
    local switchCamera=false
    if frame.camera then
        if not valid(cameraActor) then
            cameraActor=pawn:GetWorld():SpawnActor(StaticFindObject("/Script/Engine.CameraActor"),anchor,{Pitch=0,Yaw=0,Roll=0})
            switchCamera=valid(cameraActor)
        end
        if valid(cameraActor) then
            local p=frame.camera.position;local b=frame.camera.basis
            local cp={X=anchor.X+p[1]*100,Y=anchor.Y-p[3]*100,Z=anchor.Z+p[2]*100}
            local forward={X=b[7],Y=-b[9],Z=b[8]};local up={X=b[4],Y=-b[6],Z=b[5]}
            local fov=frame.camera.fov
            if selectedCamera=='dragonwilds'then
                cp,forward,up,fov=walkingCamera.present(saved.cameraProfile,cp,forward,up,proposed,fov)
            end
            local mathlib=StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
            cameraActor:K2_SetActorLocation(cp,false,{},true)
            cameraActor:K2_SetActorRotation(mathlib:MakeRotFromXZ(forward,up),true)
            cameraActor.CameraComponent:SetFieldOfView(fov)
        end
    end
    -- Keep the walking character visible and collidable until a matching source
    -- pose, appearance, board and camera have all been prepared successfully.
    if pendingToggle then
        saved.presented=true
        saved.changedMode=true
        pawn.CharacterMovement:SetMovementMode(0,0)
        saved.changedCollision=true;pawn:SetActorEnableCollision(false)
        saved.changedHidden=true;pawn:SetActorHiddenInGame(true)
        actor:SetActorHiddenInGame(false)
        pendingToggle=false
        log('MOUNT FIRST FRAME epoch='..epoch..' elapsed_ms='..math.floor((clock()-entryStarted)*1000))
        entryStarted=nil;entryPosition=nil
    end
    local hit={};pawn:K2_SetActorLocation(proposed,false,hit,true)
    if switchCamera then
        saved.viewChanged=true;pc:SetViewTargetWithBlend(cameraActor,0,0,0,false)
    end
    local text="Dragonwilds Skate: ON | F8 exit | R3 / V camera | "..frame.state.." | "..(frame.controller_connected and "Controller + keyboard" or "Keyboard")
    statusText=text -- Detailed state stays in diagnostics, never in the play HUD.
    if frame.tick%120<6 then log("SOURCE POSE tick="..frame.tick.." state="..frame.state.." controller="..tostring(frame.controller_connected)) end
    wantedCenter={X=proposed.X,Y=proposed.Y,Z=proposed.Z-saved.halfheight}
    timing.count=timing.count+1;timing.mesh=timing.mesh+clock()-started
    if timing.count%120==0 then log('TIMING updates='..timing.count..' bones_ms='..(timing.bones*1000/timing.count)..' present_ms='..(timing.mesh*1000/timing.count)..' read_ms='..(timing.read*1000/timing.count)..' index_ms='..(timing.index*1000/timing.count)..' file_ms='..(timing.file*1000/timing.count)..' decode_ms='..(timing.decode*1000/timing.count)) end
end
local function toggle(entryChecked,origin)
    if active or pendingToggle or enterAfterRefresh then stopped();message("Dragonwilds Skate: off");return end
    if not entryChecked then entryOrigin=origin or 'debug';entryRetry=false end
    if not prepared then message("Dragonwilds Skate: still preparing; wait for ready");return end
    if restorePending or wheels_pending() then message('Dragonwilds Skate: restoring walking controls; try again shortly');return end
    -- The complete accepted world remains usable during background updates, as
    -- it does while riding. The relay validates this exact native floor against
    -- that world before summon; a mismatch falls back to the refresh gate below.
    next_dirty(true)
    if entryOrigin=='mount' then
        local allowed,reason=mountControl.can_enter(pawn,pc)
        if not gameplayInputAllowed or not allowed then
            stopped();message('Dragonwilds Skate: summon cancelled ('..tostring(reason or 'menu or pause')..')');return
        end
    end
    epoch=epoch+1;lastTick=-1;lastFrameSerial=nil;lastFrameTime=os.time()
    local pos=pawn:K2_GetActorLocation()
    local ground,floorStatus=supporting_floor()
    if not ground then pendingToggle=false;message(floor_message(floorStatus,false));return end
    local movementMode=pawn.CharacterMovement.MovementMode
    if pawn:GetActorEnableCollision()~=true or pawn.bHidden~=false or(movementMode~=1 and movementMode~=2)then
        message('Dragonwilds Skate: native walking state unavailable; summon cancelled');return
    end
    -- GetCollisionEnabled returns zero after the real actor is suppressed.
    -- Retain its verified capsule policy for background scans while riding.
    ridingQueryPolicy=scene.capture_query_policy(pawn)
    -- A scan may have begun while walking. Transfer the verified policy before
    -- suppressing the actor so its final validation can finish during skating.
    scene.adopt_query_policy(pawn,ridingQueryPolicy)
    -- Equipment can change while walking. Snapshot current visible components
    -- before hiding the real pawn; there is no appearance scan in the pose loop.
    require('host_rig').refresh_appearance(pawn)
    local spawn=skate(ground.ImpactPoint);spawn[2]=spawn[2]+0.25
    currentSpawn=spawn;currentHeading=math.rad(pawn:K2_GetActorRotation().Yaw+90);entryMode='summon'
    local normal=ground.ImpactNormal
    entrySupport={point=skate(ground.ImpactPoint),normal={normal.X,normal.Z,-normal.Y}}
    saved={mode=movementMode,hidden=pawn.bHidden,collision=pawn:GetActorEnableCollision(),halfheight=pawn.CapsuleComponent:GetScaledCapsuleHalfHeight(),
        pawn=pawn,pawnIdentity=object_identity(pawn),pc=pc,pcIdentity=object_identity(pc)}
    local cameraStatus
    saved.cameraProfile,cameraStatus=walkingCamera.capture(pawn,pc)
    if not saved.cameraProfile then log('WALKING CAMERA '..tostring(cameraStatus and cameraStatus.reason))end
    local wheelReady,wheelError=spellWheel.acquire(pc)
    if wheelReady then wheelReady,wheelError=quickWheel.acquire(pc)end
    if not wheelReady then
        stopped();log('WHEEL BLOCK '..tostring(wheelError))
        message('Dragonwilds Skate: wheel-control setup failed; movement restored');return
    end
    lastSafePosition={X=pos.X,Y=pos.Y,Z=pos.Z+30};lastGroundCheck=nil
    active=true;pendingToggle=true;entryStarted=clock()
    entryPosition={X=pos.X,Y=pos.Y,Z=pos.Z}
    pawn.CharacterMovement:StopMovementImmediately()
    pc:SetIgnoreMoveInput(true);saved.ignoreMove=true
    pc:SetIgnoreLookInput(true);saved.ignoreLook=true
    write("host.json",{generation=generation,seq=seq,epoch=epoch,active=true,paused=false,input_allowed=true,spawn=spawn,heading=currentHeading,entry_mode=entryMode,entry_floor=entrySupport})
    log("ENTER SOURCE epoch="..epoch)
    message("Dragonwilds Skate: mounting")
end
local keys={}
local function pressed(name)
    local down=pc:IsInputKeyDown({KeyName=FName(name)})
    local result=down and keys[name]==false;keys[name]=down;return result
end
local function leave_world()
    reset_buildings()
    stopped(true);mountControl.shutdown();mount.shutdown();scene.shutdown();mountOptions=nil
    capture=nil;pendingWindow=nil;prepared=false;generation=nil;anchor=nil;worldWarmup=nil
    collisionCenter=nil;collisionRadius=nil;latestScene=nil;sceneDirty=false;lastRequestedRevision=0
    wantedCenter=nil;lastFullSceneCenter=nil;worldSeen={};queuedDirty={};forceFullScene=false
    sceneRetryAt=0
    eventInstalledFor=nil;lastDirtyPoll=0;keys={};pawn=nil;pc=nil;pawnIdentity=nil;controllerIdentity=nil;preparationFailures=0
end
local function tick()
    local newpc=UE.GetPlayerController()
    if not valid(newpc) or not valid(newpc.Pawn) then leave_world();return end
    local newpawn=newpc.Pawn
    if not UE.GetKismetSystemLibrary():IsStandalone(newpawn) then leave_world();return end
    if newpawn:GetClass():GetFName():ToString()~="BP_PlayerCharacter_C" then leave_world();return end
    if pawn and(not same_owner(newpawn,pawnIdentity)or not same_owner(newpc,controllerIdentity))then
        leave_world()
    end
    pc=newpc;pawn=newpawn
    if not pawnIdentity then pawnIdentity=object_identity(pawn);controllerIdentity=object_identity(pc)end
    if config.capture_native_building_probe==true then
        local probe=require('building_capture_runner')
        if pressed('F10')then
            probe.cancel();if valid(hud)then hud:RemoveFromParent()end
            RestartCurrentMod();return
        end
        if not buildingProbeStarted then
            buildingProbeStarted=true
            local zoneOk,zoneResult=pcall(function()return require('mount_zone').inspect(pawn)end)
            pcall(write,'mount-zone-live-check.json',zoneOk and zoneResult or {ready=false,error=tostring(zoneResult)})
            local ok,why=probe.begin(pawn)
            message(ok and 'Dragonwilds Skate: reading built pieces' or 'Dragonwilds Skate: building capture failed; see log')
            if not ok then log('BUILDING CAPTURE ERROR '..tostring(why))end
        end
        local result=probe.tick()
        if result then
            log('BUILDING CAPTURE '..json.encode(result))
            message(result.complete and 'Dragonwilds Skate: building capture complete' or 'Dragonwilds Skate: building capture incomplete; see log')
        end
        return
    end
    if not mountOptions then
        local info=require('skate_world').read(pawn)
        mountOptions=require('mount_selection').options(info.session,clock,config.mount_icon)
        mountOptions.equip_click_hook=true
    end
    -- Use the previous tick for optional UI work. Initialize the mount API
    -- before enumerating controller flags in this pinned UE4SS build.
    mountOptions.gameplay_active=gameplayInputAllowed
    local mountReady,mountStatus=mount.ensure(pawn,pc,mountOptions)
    if mountStatus.error and mountStatus.error~=lastMountError then
        lastMountError=mountStatus.error;log('MOUNT ENTRY ERROR '..mountStatus.error)
    end
    local gameplayAllowed,gameplayStatus=gameplayGate.allowed(pawn,pc)
    local paused=gameplayStatus.paused==true
    -- CurrentInputMode is authoritative. Cached spell-widget activation and
    -- radial computation flags can remain set behind hidden parent widgets.
    local inputAllowed=gameplayAllowed
    gameplayInputAllowed=inputAllowed
    if (restorePending or(not active and wheels_pending()))and clock()>=restoreRetryAt then restore_owned(not gameplayAllowed or paused)end
    if (active or enterAfterRefresh) and gameplayStatus.input_mode==
        'DominionInputMode /Game/Gameplay/Inputs/Modes/DIM_Dead.DIM_Dead' then
        stopped(true);message('Dragonwilds Skate: off — character died')
    elseif pendingToggle and not paused and not inputAllowed then
        log('MOUNT INPUT BLOCKED '..json.encode(gameplayStatus))
        stopped();message('Dragonwilds Skate: summon cancelled — close menus and try again')
    elseif active and not pendingToggle and saved and saved.changedMode and pawn.CharacterMovement.MovementMode~=0 then
        stopped(true);message('Dragonwilds Skate: off — native character state changed')
    end
    if entryOrigin=='mount' and(not mountReady or not mountStatus.selected)and(active or enterAfterRefresh)then
        stopped();message('Dragonwilds Skate: off — another mount selected')
    end
    local controlReady,controlStatus=mountControl.update(pawn,pc,mountReady and mountStatus.selected==true,
        {clock=clock,input_allowed=inputAllowed,active=active})
    if not controlReady and entryOrigin=='mount'and(active or enterAfterRefresh)then
        stopped();message('Dragonwilds Skate: mount control unavailable; movement restored')
        log('MOUNT CONTROL '..tostring(controlStatus.error or controlStatus.waiting_reason))
    end
    if active then
        local wheelReady,wheelError=quickWheel.ensure(pc,inputAllowed)
        if not wheelReady then
            stopped();log('WHEEL BLOCK '..tostring(wheelError))
            message('Dragonwilds Skate: wheel controls unavailable; movement restored')
        end
    end
    if clock()-lastControlStatus>=2 then
        lastControlStatus=clock()
        controlStatus.input_gate=gameplayStatus
        controlStatus.menu_ready=mountReady;controlStatus.selected=mountStatus.selected
        controlStatus.menu_error=mountStatus.error
        pcall(write,'mount-control-status.json',controlStatus)
        if controlStatus.error~=lastControlError then
            lastControlError=controlStatus.error
            if lastControlError then log('MOUNT CONTROL '..lastControlError)end
        end
        if prepared and not active and not enterAfterRefresh and statusText:find('Dragonwilds Skate: ready |',1,true)==1 then
            local text='Dragonwilds Skate: ready | '..ready_hint(mountReady,mountStatus,controlReady,controlStatus,inputAllowed)
            if text~=statusText then message(text)end
        end
    end
    local name=pawn:GetFullName()
    if eventInstalledFor~=name then
        local ready,status=scene.install_events(pawn);eventInstalledFor=name
        if not ready or (status.errors and #status.errors>0) then log('SCENE EVENTS '..json.encode(status)) end
    end
    -- Keep reload ahead of automatic preparation, including a failed rig read.
    if pressed("F10") then
        if not stopped()then message('Dragonwilds Skate: cleanup is pending; reload cancelled');return end
        reset_buildings()
        mountControl.shutdown();mount.shutdown();scene.shutdown();if valid(hud) then hud:RemoveFromParent() end
        RestartCurrentMod();return
    end
    if not worldSeen[name] then worldSeen[name]=os.time()+2 end
    if type(worldSeen[name])=="number" and os.time()>=worldSeen[name] then
        -- Reserve the retry before preparation: reflection failures must not
        -- recapture the whole rig on every game frame or leave a stale floor HUD.
        worldSeen[name]=os.time()+5
        local okay,begun=pcall(begin_capture)
        if okay then worldSeen[name]=begun and true or os.time()+2
        else
            preparationFailures=preparationFailures+1
            log('PREPARATION ERROR '..tostring(begun))
            if preparationFailures>=3 then
                worldSeen[name]=true
                message('Dragonwilds Skate: preparation failed; see adapter log, F7 retries')
            else message('Dragonwilds Skate: preparation error; retrying shortly')end
        end
    end
    if mountControl.consume_toggle()then toggle(false,'mount')end
    if pressed("F8")then
        if active or enterAfterRefresh then toggle(false,'mount')
        elseif inputAllowed and mountReady and mountStatus.selected and controlReady then
            toggle(false,'mount')
        else message('Dragonwilds Skate: select Skateboard in Mounts and dismount your current mount first')end
    end
    if pressed("F7")then stopped();begin_capture() end
    -- Independent bounded building work runs even during a long SMC scan or
    -- native collision update. Remount never restarts this collection.
    advance_buildings()
    if scene.busy() then advance_capture() end
    local status=read("status.json")
    local acceptedProgress=false
    if status and status.generation==generation then
        local progress=status.progress
        if status.status~='error'and type(progress)=='table'and progress.generation==generation
                and progress_number(progress.revision)and progress.revision%1==0
                and((pendingWindow and progress.revision==pendingWindow.revision)or(progress.revision==0 and not prepared))then
            local token=tostring(status.heartbeat)..':'..tostring(progress.generation)..':'..tostring(progress.revision)
                ..':'..tostring(progress.phase)..':'..tostring(progress.elapsed_seconds)..':'..tostring(progress.completed)
            if token~=loadingProgressToken then loadingProgressAt=clock();loadingProgressToken=token end
            loadingProgress=progress;acceptedProgress=true
        end
        if status.camera_mode=='high'or status.camera_mode=='low'or status.camera_mode=='dragonwilds'then
            selectedCamera=status.camera_mode
        end
        local entry=status.entry
        if pendingToggle and entry and entry.generation==generation and entry.epoch==epoch and entry.phase=='waiting_collision' then
            local origin=entryOrigin;local retried=entryRetry
            stopped()
            if retried then
                message('Dragonwilds Skate: this ground is not ready for skating; movement restored')
            else
                entryOrigin=origin;entryRetry=true;enterAfterRefresh=true
                if not capture and not pendingWindow then
                    local here=pawn:K2_GetActorLocation();begin_window(here.X,here.Y,here.Z,true)
                end
                message('Dragonwilds Skate: updating collision beneath you; walking remains available')
            end
        end
        if status.heartbeat~=lastStatusHeartbeat then
            lastStatusHeartbeat=status.heartbeat
            if status.suspended and not pendingToggle then lastFrameTime=os.time() end
        end
        if status.status=='ready' and pendingWindow and status.collision_revision==pendingWindow.revision then
            loadingProgress=nil;loadingProgressAt=nil;loadingProgressToken=nil;acceptedProgress=false
            local center=status.coverage_center or pendingWindow.center
            collisionCenter={X=center[1],Y=center[2],Z=center[3]}
            assert(status.collision_scope=='whole_world','Worker did not load permanent world collision')
            collisionRadius=nil
            -- Native preparation can exceed an audit interval. Start the next
            -- quiet-period timer after acceptance rather than immediately
            -- repeating a scan whose backend only just finished.
            if pendingWindow.globalScan then lastSceneTime=clock()end
            if pendingWindow.fullScan then lastFullSceneTime=clock()end
            buildings.baselineMissing=false
            if pendingWindow.buildingCaptureId then finish_building_request(pendingWindow,true)end
            lastAcceptedFingerprint=pendingWindow.fingerprint;pendingWindow=nil
            lastStatusError=nil
            prepared=true
            log('PERMANENT WORLD ready revision='..status.collision_revision)
            if not active then
                local hint=ready_hint(mountReady,mountStatus,controlReady,controlStatus,inputAllowed)
                message('Dragonwilds Skate: ready | '..hint)
            end
        end
        local errorKey=tostring(status.failed_revision or '')..':'..tostring(status.error)
        if status.status=='error' and errorKey~=lastStatusError and
                (not building_enabled() or not status.failed_revision or (pendingWindow and status.failed_revision==pendingWindow.revision)) then
            if pendingWindow and pendingWindow.buildingOnly then
                -- A missing saved baseline is expected on a new save/install.
                -- Keep both fresh scans running; do not retry the same absent
                -- evidence or cancel an unrelated component collection.
                if pendingWindow.buildingCaptureId then
                    finish_building_request(pendingWindow,false)
                end
                pendingWindow=nil;buildings.baselineMissing=not prepared
                lastStatusError=errorKey
                if not prepared then message('Dragonwilds Skate: preparing this world\'s building collision')end
                log('BUILDING UPDATE ERROR '..tostring(status.error))
            else
            local failedPackages=(pendingWindow and pendingWindow.packages) or (capture and capture.packages)
            remember_dirty(pendingWindow and pendingWindow.packages);remember_dirty(capture and capture.packages)
            if not failedPackages then forceFullScene=true;lastSceneTime=clock()-6 end
            pendingWindow=nil;sceneDirty=false
            sceneRetryAt=clock()+2
            scene.cancel();capture=nil
            -- A rejected update keeps the installed collision and skating mode.
            -- Initial preparation remains unavailable until its retry succeeds.
            lastStatusError=errorKey;message('Dragonwilds Skate: retrying collision update');log(tostring(status.error))
            end
        end
    end
    if not acceptedProgress then loadingProgress=nil;loadingProgressAt=nil;loadingProgressToken=nil end
    if not pendingWindow then
        if building_enabled() and buildings.result then
            request_buildings(buildings.result)
        elseif building_enabled() and buildings.auditResult then
            request_buildings(buildings.auditResult,'audit')
        elseif sceneDirty and not buildings.baselineMissing then
            local center=(active and wantedCenter) or pawn:K2_GetActorLocation()
            request_window(center.X,center.Y,center.Z)
        elseif generation and not capture and not buildings.baselineMissing then
            local center=(active and wantedCenter) or pawn:K2_GetActorLocation()
            -- Finish the user's required entry snapshot before starting another
            -- periodic audit. Backend preparation can itself exceed the audit
            -- interval; restarting here would keep entry waiting indefinitely.
            -- Real dirty events still run before activation below.
            local auditDue=not enterAfterRefresh and clock()-lastFullSceneTime>60
            local packages,waiting
            if not auditDue then packages,waiting=next_dirty(enterAfterRefresh) end
            if auditDue then begin_window(center.X,center.Y,center.Z,true)
            elseif packages then begin_window(center.X,center.Y,center.Z,false,packages)
            elseif enterAfterRefresh and not waiting then enterAfterRefresh=false;toggle(true)
            elseif clock()-lastSceneTime>8 then
                local moved=lastFullSceneCenter and
                    (math.abs(center.X-lastFullSceneCenter.X)>100 or math.abs(center.Y-lastFullSceneCenter.Y)>100)
                local far=lastFullSceneCenter and
                    (math.abs(center.X-lastFullSceneCenter.X)>15000 or math.abs(center.Y-lastFullSceneCenter.Y)>15000)
                local full=forceFullScene or not lastFullSceneCenter or far or (moved and clock()-lastFullSceneTime>30)
                begin_window(center.X,center.Y,center.Z,full)
            end
        end
    end
    seq=seq+1
    if active then
        if not currentSpawn then
            -- Capture the activation request before replacing its heartbeat.
            for slot=0,3 do local old=read("host"..slot..".json");if old and old.epoch==epoch and old.active then currentSpawn=old.spawn;currentHeading=old.heading end end
        end
        local controls,cameraSerial=input.sample(pc,inputAllowed)
        write("host.json",{generation=generation,seq=seq,epoch=epoch,active=true,paused=paused,spawn=currentSpawn,heading=currentHeading,
            controls=controls,camera_serial=cameraSerial,input_allowed=inputAllowed,entry_mode=entryMode,entry_floor=entrySupport})
        local readStarted=clock()
        local blob=transport.readFrame()
        timing.file=timing.file+clock()-readStarted
        local decodeStarted=clock()
        local frame=nil
        if blob then local ok,data=pcall(frameCodec.decode,blob,lastFrameSerial);if ok then frame=data end end
        timing.decode=timing.decode+clock()-decodeStarted
        timing.read=timing.read+clock()-readStarted
        if frame and frame.generation==generation and frame.epoch==epoch and frame.tick~=lastTick then
            lastFrameSerial=frame.serial;lastTick=frame.tick;lastFrameTime=os.time();present(frame)
        elseif pendingToggle and not paused and clock()-entryStarted>5 then
            stopped();message('Dragonwilds Skate: summon did not respond; movement restored')
        elseif not paused and os.time()-lastFrameTime>20 then stopped();message("Dragonwilds Skate: worker timeout; movement restored") end
    elseif generation then
        local _,cameraSerial=input.sample(pc,false)
        write("host.json",{generation=generation,seq=seq,epoch=epoch,active=false,paused=true,camera_serial=cameraSerial,
            world_warmup=not prepared and worldWarmup or nil})
    end
end
-- Keep this mod on one Lua execution path. Do not load the old asynchronous
-- diagnostics alongside this high-frequency adapter.
assert(EngineTickAvailable,"Engine tick is required for the host adapter")
local lastError=nil
LoopInGameThreadAfterFrames(1,function()
    local ok,err=pcall(tick)
    if not ok then stopped();mountControl.shutdown();if tostring(err)~=lastError then lastError=tostring(err);log("ADAPTER ERROR "..lastError) end end
    local shown,why=pcall(update_loading_hud)
    if not shown then log('LOADING PANEL ERROR '..tostring(why))end
end)
log("Host adapter loaded; F8 toggles after collision and source session are ready")
