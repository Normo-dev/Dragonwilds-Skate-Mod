-- Read-only scene descriptors. Mesh geometry is read from the owned asset cache.
-- Only names and plain values survive a tick; components are reacquired by path.
local M={}
local folder=require('skate_config').mailbox
local json=require('json')
local pending,lastSignature,lastWorld,cachedNames,cachedWorld
local instanceClass,splineClass
local eventWorld,eventPawn,eventHooks,eventErrors=nil,nil,{},{}
local dirtyPackages,needsDiscovery={},false
local function valid(o) return o and o:IsValid() end
local function object_path(name) return name:match('^[^ ]+ (.+)$') or name end
local function find(name) return StaticFindObject(object_path(name)) end
local function package_key(name) return object_path(name):match('^([^:]+):') end
local function finite(x) return type(x)=='number' and x==x and x>-math.huge and x<math.huge end
local function number(x) assert(finite(x),'Non-finite transform');return x end
local function integer(x,maximum,label)
    assert(type(x)=='number' and x==math.floor(x) and x>=0 and x<=maximum,'Invalid '..label);return x
end
local function query_enabled(value) return value==1 or value==3 or value==5 end
local function query_policy(pawn,allowSuppressed)
    assert(valid(pawn) and valid(pawn.CapsuleComponent),'Player capsule unavailable')
    local capsule=pawn.CapsuleComponent
    local result={channel=integer(capsule:GetCollisionObjectType(),31,'player object channel'),
        enabled=integer(capsule:GetCollisionEnabled(),5,'player collision enabled'),responses={}}
    if allowSuppressed and result.enabled==0 then
        assert(pawn:GetActorEnableCollision()==false,'Player capsule is disabled without actor suppression')
    else assert(query_enabled(result.enabled),'Player capsule has no query collision') end
    for channel=0,31 do result.responses[channel+1]=integer(capsule:GetCollisionResponseToChannel(channel),2,'player collision response') end
    return result
end
local function policy_identity(pawn)
    assert(valid(pawn),'Player unavailable for collision policy')
    local world=pawn:GetWorld();assert(valid(world),'Player world unavailable for collision policy')
    local capsule=pawn.CapsuleComponent;assert(valid(capsule),'Player capsule unavailable for collision policy')
    return {pawn=pawn:GetFullName(),world=world:GetFullName(),capsule=capsule:GetFullName(),
        pawn_address=integer(pawn:GetAddress(),9007199254740991,'player identity'),
        world_address=integer(world:GetAddress(),9007199254740991,'world identity'),
        capsule_address=integer(capsule:GetAddress(),9007199254740991,'capsule identity')}
end
function M.capture_query_policy(pawn)
    -- Must run before bridge suppresses the player's native actor collision.
    -- Plain values only; callers clear this token when the skating entry ends.
    local result=policy_identity(pawn);local native=query_policy(pawn)
    result.schema='S3QUERY1';result.channel=native.channel;result.enabled=native.enabled;result.responses=native.responses
    return result
end
local function policy_key(policy) return tostring(policy.channel)..':'..tostring(policy.enabled)..':'..table.concat(policy.responses,',') end
local function captured_policy(pawn,source)
    assert(type(source)=='table' and getmetatable(source)==nil and source.schema=='S3QUERY1','Invalid captured player policy')
    local identity=policy_identity(pawn)
    for key,value in pairs(identity) do assert(source[key]==value,'Captured player policy identity changed: '..key) end
    local result={channel=integer(source.channel,31,'captured player channel'),
        enabled=integer(source.enabled,5,'captured player collision enabled'),responses={}}
    assert(query_enabled(result.enabled),'Captured player policy has no query collision')
    assert(type(source.responses)=='table' and getmetatable(source.responses)==nil,'Invalid captured player responses')
    for index=1,32 do result.responses[index]=integer(source.responses[index],2,'captured player response') end
    for key in pairs(source.responses) do assert(type(key)=='number' and key%1==0 and key>=1 and key<=32,'Invalid captured player response key') end
    local native=query_policy(pawn,true)
    if native.enabled==0 then native.enabled=result.enabled end
    assert(policy_key(native)==policy_key(result),'Native player policy changed since capture')
    return result,identity
end
function M.adopt_query_policy(pawn,source)
    -- Called by the owning bridge immediately before it suppresses collision.
    -- A walking-started scan may continue only with the exact policy and objects
    -- it began with. Never adopt after suppression, even with a valid old token.
    local native=query_policy(pawn)
    local policy,identity=captured_policy(pawn,source)
    assert(policy_key(native)==policy_key(policy),'Player policy changed before suppression')
    local p=pending
    if not p then return false,'No scene collection running' end
    for key,value in pairs(p.start_identity) do
        assert(identity[key]==value,'Pending scene policy identity changed: '..key)
    end
    assert(policy_key(policy)==policy_key(p.query_policy),'Pending scene query policy changed')
    p.policy_identity=identity
    return true
end
-- Publish a small independent observation while a component scan is running.
-- An empty package list observes no ordinary packages: persisted rows remain
-- authoritative until a later component scan actually observes their package.
function M.publish_buildings(pawn,options)
    assert(type(options)=='table','Building scene options unavailable')
    assert(type(options.generation)=='string' and options.generation:match('^[%w_-]+$') and #options.generation<=128,'Invalid scene generation')
    integer(options.revision,4294967295,'scene revision');assert(options.revision>0,'Invalid scene revision')
    local world=pawn:GetWorld();assert(valid(world),'Player world unavailable')
    local policy
    if options.queryPolicy then policy=captured_policy(pawn,options.queryPolicy) else policy=query_policy(pawn) end
    local result={schema='S3SC2',world=world:GetFullName(),packages={},objects={},partial=true,
        physicsSettings=options.physicsSettings,query_policy=policy,errors={},error_count=0,complete=true,
        components=0,instance_transforms=0,elapsed=0,changed=true,generation=options.generation,
        revision=options.revision,snapshot_name='scene-'..options.generation..'-'..options.revision..'.json',
        building_inventory='retain_verified'}
    local capture=options.building
    if capture then
        assert(capture.complete==true and capture.collision_verified==true,'Building capture is incomplete')
        assert(capture.world==result.world and capture.world_session==options.world_session,'Building capture belongs to another world/save')
        assert(type(capture.capture_id)=='string' and capture.capture_id:match('^[%w_-]+$'),'Invalid building capture identity')
        local name='building-state2-'..capture.capture_id..'.json'
        assert(capture.path==folder..name,'Building capture is not an immutable mailbox report')
        result.building_inventory='replace_verified';result.building_state_file=name;result.building_capture_id=capture.capture_id
    end
    local temporary=folder..result.snapshot_name..'.tmp'
    local f=assert(io.open(temporary,'wb'));assert(f:write(json.encode(result)));assert(f:close())
    assert(os.rename(temporary,folder..result.snapshot_name))
    return result
end
local function transform(t)
    return {Translation={X=number(t.Translation.X),Y=number(t.Translation.Y),Z=number(t.Translation.Z)},
        Rotation={X=number(t.Rotation.X),Y=number(t.Rotation.Y),Z=number(t.Rotation.Z),W=number(t.Rotation.W)},
        Scale3D={X=number(t.Scale3D.X),Y=number(t.Scale3D.Y),Z=number(t.Scale3D.Z)}}
end
local function transform_key(t)
    local v,q,s=t.Translation,t.Rotation,t.Scale3D
    return string.format('%.17g,%.17g,%.17g,%.17g,%.17g,%.17g,%.17g,%.17g,%.17g,%.17g',
        v.X,v.Y,v.Z,q.X,q.Y,q.Z,q.W,s.X,s.Y,s.Z)
end
local function issue(p,name,why)
    p.error_count=p.error_count+1
    if #p.errors<128 then p.errors[#p.errors+1]={name=name,error=tostring(why)} end
end
local function timer(options)
    if options.clock then return options.clock,true end
    local ok,transport=pcall(require,'shared_transport')
    if ok and type(transport.clock)=='function' then return transport.clock,true end
    -- Coarse wall time is adequate for reporting, never for a submillisecond budget.
    return os.time,false
end
local function basename(options)
    if options.generation==nil and options.revision==nil then return 'scene-collision.json' end
    local generation,revision=options.generation,options.revision
    assert(type(generation)=='string' and #generation>0 and #generation<=96 and
        generation:match('^[%w_%-]+$'),'Scene generation must be a plain identifier')
    assert(type(revision)=='number' and revision>=0 and revision<=4294967295 and
        revision==math.floor(revision),'Scene revision must be an unsigned integer')
    return 'scene-'..generation..'-'..string.format('%.0f',revision)..'.json'
end
local function discover_names()
    local names,seen={},{}
    for _,c in ipairs(FindAllOf('StaticMeshComponent') or {}) do
        if valid(c) then
            local name=c:GetFullName()
            -- A valid same-world component can be owned below a manager or
            -- subsystem instead of a level actor. Its typed world/owner checks
            -- below, not a spelling convention in its outer path, decide scope.
            if name and not seen[name] and
                not name:find('Default__',1,true) and not object_path(name):match('^/Temp/') then
                seen[name]=true;names[#names+1]=name
            end
        end
    end
    table.sort(names);return names
end
local function event_error(error)
    if #eventErrors<16 then eventErrors[#eventErrors+1]=tostring(error) end
end
local function mark_actor(param)
    if not eventWorld then return end
    local actor=param:get()
    if not valid(actor) then return end
    local name=actor:GetFullName()
    if name==eventPawn or name:find('Default__',1,true) or object_path(name):match('^/Temp/') then return end
    local world=actor:GetWorld()
    if not valid(world) or world:GetFullName()~=eventWorld then return end
    local package=package_key(name)
    if package then dirtyPackages[package]=true;needsDiscovery=true end
end
local function stream_loaded(context)
    if not eventWorld then return end
    local subsystem=context:get()
    if valid(subsystem) then
        local world=subsystem:GetWorld()
        if valid(world) and world:GetFullName()==eventWorld then needsDiscovery=true end
    end
end
function M.install_events(pawn)
    assert(valid(pawn),'Cannot monitor a scene without a pawn')
    local world=pawn:GetWorld();assert(valid(world),'Cannot monitor an unavailable world')
    local name=world:GetFullName()
    if eventWorld==name and eventPawn==pawn:GetFullName() then return true,{errors=eventErrors} end
    M.shutdown();eventWorld=name;eventPawn=pawn:GetFullName()
    -- BeginPlay has no individual unregister function in UE4SS. Keep a single
    -- dispatcher through a Lua module reload; shutdown makes it inert. UE4SS
    -- removes it when RestartCurrentMod destroys this mod's Lua state.
    local key='DragonwildsSkateSceneBeginPlayDispatcherV1'
    local dispatcher=rawget(_G,key)
    if not dispatcher then
        dispatcher={};rawset(_G,key,dispatcher)
        if type(RegisterBeginPlayPostHook)=='function' then
            local ok,why=pcall(RegisterBeginPlayPostHook,function(param)
                if dispatcher.handler then
                    local success,error=pcall(dispatcher.handler,param)
                    if not success and dispatcher.error_handler then dispatcher.error_handler(error) end
                end
            end)
            dispatcher.registered=ok;if not ok then event_error(why) end
        end
    end
    dispatcher.handler=mark_actor;dispatcher.error_handler=event_error
    local function hook(path,callback,post)
        if type(RegisterHook)~='function' or not valid(StaticFindObject(path)) then return end
        local ok,preId,postId
        if post then ok,preId,postId=pcall(RegisterHook,path,function() end,callback)
        else ok,preId,postId=pcall(RegisterHook,path,callback) end
        if ok then eventHooks[#eventHooks+1]={path,preId,postId} else event_error(preId) end
    end
    hook('/Script/SPUD.SpudSubsystem:OnActorDestroyed',function(_,actor)
        local ok,why=pcall(mark_actor,actor);if not ok then event_error(why) end
    end)
    hook('/Script/SPUD.SpudSubsystem:PostLoadStreamLevelGameThread',stream_loaded,true)
    hook('/Script/SPUD.SpudSubsystem:NotifyLevelLoadedExternally',stream_loaded,true)
    return dispatcher.registered==true,{errors=eventErrors,hooks=#eventHooks}
end
function M.take_dirty(maxPackages)
    if not eventWorld or pending then return nil end
    if needsDiscovery then
        local ok,names=pcall(discover_names)
        if not ok then event_error(names);return nil end
        local old,new,livePackages={},{},{}
        if cachedWorld==eventWorld then for _,name in ipairs(cachedNames or {}) do old[name]=true end end
        for _,name in ipairs(names) do
            new[name]=true;local package=package_key(name)
            if package then
                livePackages[package]=true
                if not old[name] then
                    local ok,belongs=pcall(function()
                        local c=find(name);if not valid(c) then return false end
                        local world=c:GetWorld();return valid(world) and world:GetFullName()==eventWorld
                    end)
                    if ok and belongs then dirtyPackages[package]=true elseif not ok then event_error(belongs) end
                end
            end
        end
        for name in pairs(old) do
            local package=package_key(name)
            -- An absent streamed package is not a deletion. Explicit actor
            -- destruction events above still retain its package tombstone.
            if not new[name] and livePackages[package] then dirtyPackages[package]=true end
        end
        cachedNames=names;cachedWorld=eventWorld;needsDiscovery=false
    end
    local packages={};for package in pairs(dirtyPackages) do packages[#packages+1]=package end
    table.sort(packages)
    local limit=math.max(1,math.min(64,math.floor(maxPackages or 8)))
    while #packages>limit do packages[#packages]=nil end
    if #packages==0 then return nil end
    for _,package in ipairs(packages) do dirtyPackages[package]=nil end
    return packages
end
function M.shutdown()
    local dispatcher=rawget(_G,'DragonwildsSkateSceneBeginPlayDispatcherV1')
    if dispatcher and dispatcher.handler==mark_actor then dispatcher.handler=nil;dispatcher.error_handler=nil end
    for _,hook in ipairs(eventHooks) do pcall(UnregisterHook,hook[1],hook[2],hook[3]) end
    eventHooks={};eventErrors={};eventWorld=nil;eventPawn=nil;dirtyPackages={};needsDiscovery=false
    M.cancel()
end
function M.busy() return pending~=nil end
function M.cancel()
    if pending and pending.building_module then pcall(pending.building_module.cancel) end
    if pending and pending.output then
        if pending.output.file then pending.output.file:close() end
        os.remove(pending.output.temporary)
    end
    pending=nil
end
function M.begin(pawn,options)
    options=options or {}
    if pending then return false,'Scene collection already running' end
    assert(valid(pawn),'Cannot collect a scene without a live pawn')
    local world=pawn:GetWorld();assert(valid(world),'Pawn world is unavailable')
    local clock,hasTimer=timer(options)
    local p={names={},index=1,objects={},encoded={},errors={},error_count=0,packages={},signature={},
        clock=clock,hasTimer=hasTimer,started=clock(),world=world:GetFullName(),
        pawn=pawn:GetFullName(),snapshot_name=basename(options),generation=options.generation,
        revision=options.revision,physicsSettings=options.physicsSettings,
        excluded={},instances=0,frames=0,operations=0,skipped=0,frame_cache_hits=0,captureBuildings=options.captureBuildings==true,
        captureNativeBuildings=options.captureNativeBuildings==true,buildingInventory=options.buildingInventory}
    assert(p.buildingInventory==nil or p.buildingInventory=='retain_verified','Invalid component-scan building declaration')
    p.start_identity=policy_identity(pawn)
    if options.queryPolicy~=nil then
        p.query_policy,p.policy_identity=captured_policy(pawn,options.queryPolicy)
    else p.query_policy=query_policy(pawn) end
    p.excluded[p.pawn]=true
    for _,name in ipairs(options.exclude_owners or {}) do p.excluded[name]=true end
    local requested
    if options.packages~=nil then
        assert(type(options.packages)=='table','Partial scene packages must be a list')
        requested={}
        for _,package in ipairs(options.packages) do
            assert(type(package)=='string' and package:sub(1,1)=='/' and not package:find(':',1,true),'Invalid partial scene package')
            requested[package]=true;p.packages[package]=true
        end
        assert(next(requested),'Partial scene package list cannot be empty')
        p.partial=true
    else
        -- Keep the main package even after its final player-built collider is removed.
        p.packages[object_path(p.world)]=true
    end
    if not valid(instanceClass) then instanceClass=StaticFindObject('/Script/Engine.InstancedStaticMeshComponent') end
    assert(valid(instanceClass),'InstancedStaticMeshComponent class is unavailable')
    if not valid(splineClass) then splineClass=StaticFindObject('/Script/Engine.SplineMeshComponent') end
    assert(valid(splineClass),'SplineMeshComponent class is unavailable')
    p.reused_names=options.reuse_names==true and cachedWorld==p.world and cachedNames~=nil
    if p.reused_names then p.names=cachedNames
    else
        -- Consume FindAllOf's UObject wrappers in this call; never retain those
        -- arrays across frames while streaming and garbage collection run.
        -- UE4SS FindAllOf includes inherited classes (see its findallof.md).
        -- ISM/HISM are included here; separate searches repeat the global scan.
        local ok,names=pcall(discover_names)
        if ok then p.names=names else issue(p,'StaticMeshComponent',names) end
        cachedNames=p.names;cachedWorld=p.world
    end
    if requested then
        local selected={}
        for _,name in ipairs(p.names) do if requested[package_key(name)] then selected[#selected+1]=name end end
        p.names=selected
    end
    p.discovery_seconds=clock()-p.started
    pending=p
    print('[SkateScene] Reading '..#p.names..' component names in bounded batches.\n')
    return true
end
local function frame_value(p,frame,kind,object,read)
    -- These tables live only inside one advance call. Engine getters supply a
    -- typed UWorld/AActor/UStaticMesh respectively; category maps stay separate.
    -- No object wrapper or cached value crosses the next engine frame.
    assert(valid(object),'Frame-local scene object is unavailable')
    local address=integer(object:GetAddress(),9007199254740991,'scene object address')
    assert(address>0,'Scene object address is null')
    local values=frame[kind];local saved=values[address]
    if saved and valid(saved.object) then
        p.frame_cache_hits=p.frame_cache_hits+1;return saved.value
    end
    local value=read(object);values[address]={object=object,value=value};return value
end
local function same_world(p,c,frame)
    local world=c:GetWorld()
    return valid(world) and frame_value(p,frame,'worlds',world,function(value)return value:GetFullName() end)==p.world
end
local function read_component(p,c,name,frame)
    if not same_world(p,c,frame) then return nil end
    local key=package_key(name);if key then p.packages[key]=true end
    local owner=c:GetOwner()
    if not valid(owner) then return nil end
    local ownerState=frame_value(p,frame,'owners',owner,function(value)
        return {name=value:GetFullName(),enabled=value:GetActorEnableCollision()}
    end)
    local ownerName=ownerState.name
    if p.excluded[ownerName] or ownerName:find('Default__',1,true) then return nil end
    -- Retain the package key even if all its components have collision disabled.
    if not ownerState.enabled then return nil end
    local enabled=integer(c:GetCollisionEnabled(),5,'component collision enabled')
    if not query_enabled(enabled) then return nil end
    local response=integer(c:GetCollisionResponseToChannel(p.query_policy.channel),2,'component player response')
    if response~=2 then return nil end
    local objectType=integer(c:GetCollisionObjectType(),31,'component object channel')
    local playerResponse=p.query_policy.responses[objectType+1]
    if playerResponse~=2 then return nil end
    local mesh=c.StaticMesh
    if not valid(mesh) then return nil end
    local meshName=frame_value(p,frame,'meshes',mesh,function(value)return value:GetFullName() end)
    local item={name=name,owner=ownerName,mesh=meshName,enabled=enabled,
        response_player=response,player_response=playerResponse,object_type=objectType,query_channel=p.query_policy.channel,
        collision_policy='query_and_player_block',owner_collision=true,mobility=c.Mobility,
        transform=transform(c:K2_GetComponentToWorld())}
    if c:IsA(splineClass) then
        -- A spline's static mesh is undeformed. Only its own matching cooked
        -- BodySetup can supply collision; unresolved state fails this snapshot.
        item.component_collision=require('skate_spline').capture(c,object_path(item.mesh))
    end
    if c:IsA(instanceClass) then
        item.instances={};item.instance_count=c:GetInstanceCount()
        assert(item.instance_count>=0 and item.instance_count==math.floor(item.instance_count),'Invalid instance count')
    end
    return item
end
local function commit(p,item,record)
    p.objects[#p.objects+1]=item
    local fields={item.name,item.owner,item.mesh,tostring(item.enabled),tostring(item.response_player),tostring(item.player_response),tostring(item.object_type),
        tostring(item.mobility),transform_key(item.transform),tostring(item.instance_count or -1),
        item.component_collision and json.encode(item.component_collision) or ''}
    p.signature[#p.signature+1]=table.concat(fields,'\0')..'\0'..
        (record and table.concat(record.keys,'\0') or '')
    if item.instances then
        local header={};for k,v in pairs(item) do if k~='instances' then header[k]=v end end
        p.encoded[#p.encoded+1]=json.encode(header):sub(1,-2)..',"instances":['..
            (record and table.concat(record.encoded,',') or '')..']}'
    else p.encoded[#p.encoded+1]=json.encode(item) end
end
local function instance_step(p,frame)
    local record=p.instance
    local item=record.object
    local c=frame.name==item.name and frame.component or find(item.name)
    if frame.name~=item.name then
        -- Once per component per frame: check it still belongs to this world and
        -- has not been disabled or replaced while its instances were collected.
        if not valid(c) or not same_world(p,c,frame) then p.skipped=p.skipped+1;p.instance=nil;return end
        local owner=c:GetOwner()
        if not valid(owner) or not owner:GetActorEnableCollision() or not query_enabled(c:GetCollisionEnabled()) or
            c:GetCollisionResponseToChannel(p.query_policy.channel)~=2 then p.skipped=p.skipped+1;p.instance=nil;return end
        if c:GetCollisionObjectType()~=item.object_type then
            issue(p,item.name,'Component object channel changed during collection');p.instance=nil;return
        end
        local mesh=c.StaticMesh
        if c:GetInstanceCount()~=item.instance_count or not valid(mesh) or
            frame_value(p,frame,'meshes',mesh,function(value)return value:GetFullName() end)~=item.mesh then
            issue(p,item.name,'Instances or mesh changed during collection; retry next scene')
            p.instance=nil;return
        end
        frame.name=item.name;frame.component=c
    end
    local out={}
    assert(c:GetInstanceTransform(record.index,out,true),'Instance transform unavailable')
    local t=transform(out)
    item.instances[#item.instances+1]=t
    record.encoded[#record.encoded+1]=json.encode(t)
    record.keys[#record.keys+1]=transform_key(t)
    record.index=record.index+1
    if record.index==item.instance_count then
        p.instances=p.instances+item.instance_count
        commit(p,item,record);p.instance=nil
    end
end
local function check_publish_policy(p)
    if p.policy_failed then return end
    local ok,current=pcall(function()
        local pawn=find(p.pawn)
        if p.policy_identity then
            local identity=policy_identity(pawn)
            for key,value in pairs(p.policy_identity) do assert(identity[key]==value,'Player collision policy identity changed during collection') end
        end
        local native=query_policy(pawn,p.policy_identity~=nil)
        -- Actor suppression makes this getter report NoCollision while the
        -- capsule's actual channel and response table remain available. Only
        -- that documented disabled value can use the pre-suppression mode.
        if p.policy_identity and native.enabled==0 then native.enabled=p.query_policy.enabled end
        return native
    end)
    if not ok or policy_key(current)~=policy_key(p.query_policy) then
        issue(p,'player collision policy',ok and 'Player collision policy changed during collection' or current)
        p.policy_failed=true
    end
end
local function start_publish(p)
    check_publish_policy(p)
    local packages={};for key in pairs(p.packages) do packages[#packages+1]=key end
    table.sort(packages)
    local defaultShape=p.physicsSettings and p.physicsSettings.engine_default
    local signature=tostring(defaultShape)..'\1'..policy_key(p.query_policy)..'\1'..table.concat(packages,'\0')..'\1'..table.concat(p.signature,'\1')
    local changed=p.world~=lastWorld or signature~=lastSignature
    local result={schema='S3SC2',world=p.world,packages=packages,physicsSettings=p.physicsSettings,query_policy=p.query_policy,
        errors=p.errors,error_count=p.error_count,complete=p.error_count==0,
        components=#p.names,instance_transforms=p.instances,skipped=p.skipped,
        collection_seconds=p.clock()-p.started,discovery_seconds=p.discovery_seconds,
        frames=p.frames,operations=p.operations,snapshot_name=p.snapshot_name,
        generation=p.generation,revision=p.revision,changed=changed,reused_names=p.reused_names,partial=p.partial==true,
        building_inventory=p.buildingInventory}
    local temporary=folder..p.snapshot_name..'.tmp'
    local f=assert(io.open(temporary,'wb'))
    p.output={file=f,temporary=temporary,metadata=result,signature=signature,started=p.clock(),
        payload=table.concat(p.encoded,','),position=1,building_index=1,building_position=1}
    p.encoded=nil;p.signature=nil
    assert(f:write('{"objects":['))
end
local function building_step(p)
    -- Optional diagnostic evidence only. A missing schema or getter must not
    -- invalidate the existing, independently verified component collision scan.
    if not p.building_started then
        p.building_started=true
        local ok,why=pcall(function()
            p.building_module=require('building_collision')
            assert(p.building_module.begin(find(p.pawn),{clock=p.clock,nativeReader=p.captureNativeBuildings}))
        end)
        if not ok then
            if p.building_module then pcall(p.building_module.cancel) end
            p.building_state={schema='S3BUILDINGSTATE1',complete=false,collision_verified=false,errors={tostring(why)}}
        end
    elseif not p.building_state then
        local ok,result=pcall(p.building_module.advance,1)
        if not ok then
            pcall(p.building_module.cancel)
            result={schema='S3BUILDINGSTATE1',complete=false,collision_verified=false,errors={tostring(result)}}
        end
        if result then p.building_state=result end
    elseif not p.building_encoded then
        -- Encode large state arrays one record at a time. Do not move a giant
        -- all-buildings JSON encoding into the final publication game frame.
        if not p.building_encoder then
            local envelope={};local fields={'managers','data_assets','pieces','building_hisms','type_metadata','type_metadata_errors',
                'native_pieces','native_piece_ids','native_missing_piece_ids'};local arrays={}
            for _,key in ipairs(fields) do arrays[key]=true end
            for key,value in pairs(p.building_state) do if not arrays[key] then envelope[key]=value end end
            p.building_encoder={fields=fields,field=1,index=1,fragments={',"building_state":'..json.encode(envelope):sub(1,-2)}}
        end
        local encoder=p.building_encoder;local key=encoder.fields[encoder.field]
        if not key then
            encoder.fragments[#encoder.fragments+1]='}';p.building_encoded=encoder.fragments;p.building_encoder=nil
        else
            local values=p.building_state[key] or {};local value=values[encoder.index]
            if encoder.index==1 then encoder.fragments[#encoder.fragments+1]=',"'..key..'":[' end
            if value then
                encoder.fragments[#encoder.fragments+1]=(encoder.index>1 and ',' or '')..json.encode(value)
                encoder.index=encoder.index+1
            else
                encoder.fragments[#encoder.fragments+1]=']';encoder.field=encoder.field+1;encoder.index=1
            end
        end
    else p.building_done=true end
end
local function publish_step(p)
    local output=p.output
    if output.position<=#output.payload then
        -- The recorded real inventory takes hundreds of milliseconds to encode
        -- in one call. Encode records while collecting; write bounded chunks here.
        local last=math.min(#output.payload,output.position+16383)
        assert(output.file:write(output.payload:sub(output.position,last)))
        output.position=last+1
        return
    end
    if p.building_encoded then
        if not output.objects_closed then assert(output.file:write(']'));output.objects_closed=true end
        local fragment=p.building_encoded[output.building_index]
        if fragment then
            local last=math.min(#fragment,output.building_position+16383)
            assert(output.file:write(fragment:sub(output.building_position,last)))
            output.building_position=last+1
            if output.building_position>#fragment then output.building_index=output.building_index+1;output.building_position=1 end
            return
        end
    end
    local result=output.metadata
    -- File chunks may span several engine frames. Recheck immediately before
    -- publishing completeness, not only before the first temporary-file write.
    check_publish_policy(p)
    result.error_count=p.error_count;result.complete=p.error_count==0
    result.elapsed=p.clock()-p.started
    result.publication_seconds=p.clock()-output.started
    result.frames=p.frames;result.operations=p.operations;result.frame_cache_hits=p.frame_cache_hits
    assert(output.file:write(output.objects_closed and ',' or '],',json.encode(result):sub(2)))
    assert(output.file:close());output.file=nil
    -- Revisioned snapshots are unique. Only the legacy read-only inventory name
    -- is replaced (Windows rename cannot overwrite an existing file).
    if p.snapshot_name=='scene-collision.json' then os.remove(folder..p.snapshot_name) end
    local renamed,whyRename=os.rename(output.temporary,folder..p.snapshot_name)
    if not renamed then error('Cannot publish scene: '..tostring(whyRename)) end
    if result.complete then lastSignature=output.signature;lastWorld=p.world end
    result.objects=p.objects
    result.building_state=p.building_state
    pending=nil
    print('[SkateScene] Saved '..#p.objects..' collidable components, '..p.instances..
        ' instances, errors='..p.error_count..', changed='..tostring(result.changed)..'.\n')
    return result
end
function M.advance(maxCalls,budgetSeconds)
    local p=pending;if not p then return end
    maxCalls=math.max(1,math.min(1024,math.floor(maxCalls or 64)))
    local started=p.clock()
    local frame={worlds={},owners={},meshes={}} -- discarded before returning to the engine
    p.frames=p.frames+1
    for operation=1,maxCalls do
        if operation>1 and p.hasTimer and budgetSeconds and p.clock()-started>=budgetSeconds then break end
        p.operations=p.operations+1
        if p.output then
            local ok,result=pcall(publish_step,p)
            if not ok then M.cancel();error(result) end
            if result then return result end
        elseif p.instance then
            local name=p.instance.object.name
            local ok,why=pcall(instance_step,p,frame)
            if not ok then issue(p,name,why);p.instance=nil end
        else
            local name=p.names[p.index]
            if not name then
                local ok,why
                if p.captureBuildings and not p.building_done then ok,why=pcall(building_step,p)
                else ok,why=pcall(start_publish,p) end
                if not ok then M.cancel();error(why) end
            else
                p.index=p.index+1
                local ok,item=pcall(function()
                    local c=find(name)
                    if valid(c) then return read_component(p,c,name,frame) end
                end)
                if not ok then issue(p,name,item)
                elseif item then
                    if item.instances and item.instance_count>0 then p.instance={object=item,index=0,encoded={},keys={}}
                    else commit(p,item) end
                end
            end
        end
    end
end
return M
