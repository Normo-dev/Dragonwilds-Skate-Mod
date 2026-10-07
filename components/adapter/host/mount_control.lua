-- Native mount suppression and the game's remapped mount keys. No game input is
-- synthesized, no native equipped mount is replaced, and no soft pointers occur.
local M={}
local state={ready=false,owned=false,queued=false,released=false}
local tagName='Player.Status.MountBlocked'
local skateboardShelterTag='Player.Status.Sheltered.BaseBuilding'
local mountBase='/Script/Dominion.DominionMountComponent:'
local tagsBase='/Script/Dominion.GameplayTagsComponent:'
local function valid(o)
    if o==nil then return false end
    local ok,v=pcall(function()return o:IsValid()end);return ok and v
end
local function name(o)if valid(o)then return o:GetFullName()end end
local function find(path)
    if not path then return end
    local o=StaticFindObject((path:gsub('^[^ ]+ ','')))
    if valid(o)then return o end
end
local function tag()return {TagName=FName(tagName)}end
local function signature(path,types)
    local f=StaticFindObject(path);assert(valid(f),'Missing mount control API: '..path)
    local actual={};f:ForEachProperty(function(p)actual[#actual+1]=p:GetClass():GetFName():ToString()end)
    assert(#actual==#types,'Changed mount control API: '..path)
    for i,t in ipairs(types)do assert(actual[i]==t,'Changed mount control parameter: '..path)end
end
local function verify()
    if state.verified then return end
    signature('/Script/Dominion.DominionPlayerCharacter:GetMountComponent',{'ObjectProperty'})
    signature('/Script/Dominion.DominionCharacterBase:GetGameplayTagsComponent',{'ObjectProperty'})
    for _,method in ipairs({'AddLocalTag','RemoveLocalTag'})do signature(tagsBase..method,{'StructProperty'})end
    signature(tagsBase..'CountTag',{'StructProperty','IntProperty'})
    signature(tagsBase..'HasTag',{'StructProperty','BoolProperty','BoolProperty'})
    for _,method in ipairs({'CanMount','IsProhibited','IsMounted'})do signature(mountBase..method,{'BoolProperty'})end
    signature(mountBase..'GetMountState',{'EnumProperty'})
    signature(mountBase..'GetEquippedMountData',{'ObjectProperty'})
    signature('/Script/Dominion.DominionPlayerCharacter:IsTeleporting',{'BoolProperty'})
    signature('/Script/Engine.Pawn:GetMovementComponent',{'ObjectProperty'})
    signature('/Script/Engine.NavMovementComponent:IsFalling',{'BoolProperty'})
    signature('/Script/Engine.SubsystemBlueprintLibrary:GetLocalPlayerSubSystemFromPlayerController',{'ObjectProperty','ClassProperty','ObjectProperty'})
    signature('/Script/EnhancedInput.EnhancedInputSubsystemInterface:QueryKeysMappedToAction',{'ObjectProperty','ArrayProperty'})
    signature('/Script/Engine.PlayerController:IsInputKeyDown',{'StructProperty','BoolProperty'})
    signature('/Script/GameplayTags.BlueprintGameplayTagLibrary:IsGameplayTagValid',{'StructProperty','BoolProperty'})
    state.verified=true
end
local function each(values,fn)
    if type(values)=='table'and type(values.ForEach)~='function'then
        for _,value in ipairs(values)do fn(value)end
    elseif values then values:ForEach(function(_,value)fn(value:get())end)end
end
local function blocker_names(component)
    local class=StaticFindObject('/Script/Dominion.DominionMountComponent')
    assert(valid(class),'Native mount class unavailable')
    local fields={}
    class:ForEachProperty(function(p)
        local field=p:GetFName():ToString()
        if field=='ProhibitTags'or field=='ProhibitAndDismountTags'then
            assert(p:GetClass():GetFName():ToString()=='StructProperty'and
                name(p:GetStruct())=='ScriptStruct /Script/GameplayTags.GameplayTagContainer','Changed native mount tag container')
            fields[field]=true
        end
    end)
    assert(fields.ProhibitTags and fields.ProhibitAndDismountTags,'Native mount blocker fields unavailable')
    local container=StaticFindObject('/Script/GameplayTags.GameplayTagContainer');assert(valid(container),'Tag container reflection unavailable')
    local verified=false
    container:ForEachProperty(function(p)
        if p:GetFName():ToString()=='GameplayTags'then
            assert(p:GetClass():GetFName():ToString()=='ArrayProperty','Changed gameplay tag array')
            local inner=p:GetInner()
            assert(inner:GetClass():GetFName():ToString()=='StructProperty'and
                name(inner:GetStruct())=='ScriptStruct /Script/GameplayTags.GameplayTag','Changed gameplay tag element')
            verified=true
        end
    end)
    assert(verified,'Gameplay tag array reflection unavailable')
    local tagStruct=StaticFindObject('/Script/GameplayTags.GameplayTag')
    assert(valid(tagStruct),'Gameplay tag reflection unavailable')
    local tagField=false
    tagStruct:ForEachProperty(function(p)
        if p:GetFName():ToString()=='TagName'then
            assert(p:GetClass():GetFName():ToString()=='NameProperty','Changed gameplay tag name field')
            tagField=true
        end
    end)
    assert(tagField,'Gameplay tag name field unavailable')
    local names,seen={},{};local containsOwn=false
    for _,field in ipairs({'ProhibitTags','ProhibitAndDismountTags'})do
        each(component[field].GameplayTags,function(value)
            local valueName=value.TagName:ToString()
            assert(valueName~=''and valueName~='None','Invalid native mount blocker tag')
            if valueName==tagName then containsOwn=true
            elseif not seen[valueName]then names[#names+1]=valueName;seen[valueName]=true end
        end)
    end
    assert(containsOwn and #names>0,'Native mount blocker list is incomplete')
    table.sort(names);return names
end
local function resolve(pawn,pc,adopt)
    verify()
    local class=StaticFindObject('/Script/Dominion.DominionPlayerCharacter')
    assert(valid(pawn)and valid(pc)and valid(class)and pawn:IsA(class),'Playable character unavailable')
    local world=name(pawn:GetWorld());assert(world and name(pc:GetWorld())==world,'Player world mismatch')
    local component=pawn:GetMountComponent();local tags=pawn:GetGameplayTagsComponent()
    assert(valid(component)and valid(tags),'Native mount/tag components unavailable')
    local library=StaticFindObject('/Script/Engine.Default__SubsystemBlueprintLibrary')
    local inputClass=StaticFindObject('/Script/EnhancedInput.EnhancedInputLocalPlayerSubsystem')
    assert(valid(library)and valid(inputClass),'Native input subsystem API unavailable')
    local subsystem=library:GetLocalPlayerSubSystemFromPlayerController(pc,inputClass)
    local action=StaticFindObject('/Game/Gameplay/Inputs/Actions/Basic/IA_Mount.IA_Mount')
    assert(valid(subsystem)and valid(action),'Native mount action unavailable')
    local tagLibrary=StaticFindObject('/Script/GameplayTags.Default__BlueprintGameplayTagLibrary')
    assert(valid(tagLibrary)and tagLibrary:IsGameplayTagValid(tag()),'Native mount blocker tag unavailable')
    local blockers=blocker_names(component)
    if adopt then
        state.pawn=name(pawn);state.world=world;state.controller=name(pc)
        state.component=name(component);state.tags=name(tags);state.subsystem=name(subsystem);state.action=name(action)
        state.blocker_names=blockers
    end
    return component,tags,subsystem,action,blockers
end
local function mapped_keys(subsystem,action)
    local out,seen={},{}
    each(subsystem:QueryKeysMappedToAction(action),function(key)
        -- UE4SS's returned TArray table contains LocalUnrealParam elements;
        -- TArray:ForEach supplies already-unwrapped UScriptStruct values here.
        -- Read the name immediately and retain only its plain Lua string.
        if not(type(key)=='table'and rawget(key,'KeyName')~=nil)then
            local checked,kind=pcall(function()return key:type()end)
            assert(checked and(kind=='LocalUnrealParam'or kind=='RemoteUnrealParam'or kind=='UScriptStruct'),
                'Unexpected native mapped-key value')
            if kind~='UScriptStruct'then key=key:get()end
        end
        assert(key and key.KeyName,'Native mapped key has no name')
        local value=key.KeyName:ToString()
        assert(type(value)=='string','Native mapped key name is not a string')
        if value~=''and value~='None'and not seen[value]then seen[value]=true;out[#out+1]=value end
    end)
    table.sort(out);return out
end
local function release()
    state.ready=false;state.queued=false;state.released=false;state.held_since=nil
    state.suppressed=false
    if not state.owned then return true end
    local tags=find(state.tags)
    if not tags then state.owned=false;return true end -- destroyed world owns its destroyed component
    local before=tags:CountTag(tag())
    state.release_before_count=before
    if before>0 then
        tags:RemoveLocalTag(tag())
        -- A completed removal relinquishes our stack even if the diagnostic
        -- readback fails. Retrying removal would consume someone else's stack.
        state.owned=false
        local after=tags:CountTag(tag())
        state.release_after_count=after;state.count=after
        -- CountTag is not a total of all owners. In the pinned native build it
        -- returns the local-map count when present, otherwise the native-map
        -- count. Removing our last local stack can therefore expose a native
        -- count of one (or more) with no visible decrement. Preserve it: a
        -- second RemoveLocalTag must never be used to chase this readback.
        -- Evidence: native-tag-store-body-full.txt, CountTag 0x6624700;
        -- RemoveLocalTag 0x6634550 only mutates the local map.
    end
    state.owned=false;return true
end
function M.shutdown()
    local ok,why=pcall(release)
    if not ok then state.error='Mount blocker cleanup: '..tostring(why)end
    return ok
end
local previous=rawget(_G,'__DragonwildsSkateMountControl')
if previous and previous.shutdown then assert(previous.shutdown(),'Previous mount blocker cleanup failed')end
_G.__DragonwildsSkateMountControl=M

-- Diagnostic reads are deliberately separate from eligibility and lease state.
-- CountTag alone cannot identify the author: it prefers the local map and only
-- falls back to the replicated/native map when the local entry is absent.
local replicatedVerified=false
local function replicated_fields()
    if replicatedVerified then return end
    local function field(path,fieldName,kind,structName,array)
        local owner=StaticFindObject(path);assert(valid(owner),'Missing diagnostic type: '..path)
        local found=false
        owner:ForEachProperty(function(p)
            if p:GetFName():ToString()~=fieldName then return end
            assert(p:GetClass():GetFName():ToString()==kind,'Changed diagnostic field: '..fieldName)
            if array then
                p=p:GetInner()
                assert(p:GetClass():GetFName():ToString()=='StructProperty','Changed diagnostic array element')
            end
            if structName then assert(name(p:GetStruct())=='ScriptStruct '..structName,'Changed diagnostic struct: '..fieldName)end
            found=true
        end)
        assert(found,'Missing diagnostic field: '..fieldName)
    end
    field('/Script/Dominion.GameplayTagsComponent','ReplicatedGameplayTagStackContainer','StructProperty',
        '/Script/Dominion.DominionGameplayTagStackContainer')
    field('/Script/Dominion.DominionGameplayTagStackContainer','Stacks','ArrayProperty',
        '/Script/Dominion.DominionGameplayTagStack',true)
    field('/Script/Dominion.DominionGameplayTagStack','Tag','StructProperty','/Script/GameplayTags.GameplayTag')
    field('/Script/Dominion.DominionGameplayTagStack','StackCount','IntProperty')
    field('/Script/GameplayTags.GameplayTag','TagName','NameProperty')
    replicatedVerified=true
end
local function replicated_tags(tags)
    replicated_fields()
    local values=tags.ReplicatedGameplayTagStackContainer.Stacks
    assert(values~=nil and #values<=256,'Replicated tag diagnostic limit exceeded')
    local rows,seen={},{};local count=0
    each(values,function(value)
        local tagText=value.Tag.TagName:ToString();local stackCount=value.StackCount
        assert(type(tagText)=='string'and tagText~=''and tagText~='None','Invalid replicated tag name')
        assert(type(stackCount)=='number'and stackCount%1==0 and stackCount>=0,'Invalid replicated tag count')
        assert(not seen[tagText],'Duplicate replicated tag stack');seen[tagText]=true
        rows[#rows+1]={tag=tagText,count=stackCount}
        if tagText==tagName then count=stackCount end
    end)
    table.sort(rows,function(a,b)return a.tag<b.tag end)
    return {mount_blocked_count=count,stacks=rows,
        evidence=count>0 and'replicated_stack_present'or'no_replicated_stack_observed'}
end
local function diagnostics(pawn,component,tags,blockers)
    local out={schema='S3MOUNTDIAGNOSTIC1',read_only=true,errors={}}
    local function read(key,fn)
        local ok,value=pcall(fn)
        if ok then out[key]=value else out.errors[#out.errors+1]={field=key,error=tostring(value)}end
    end
    read('count_tag',function()return tags:CountTag(tag())end)
    read('mount_state',function()return component:GetMountState()end)
    read('mounted',function()return component:IsMounted()end)
    read('can_mount',function()return component:CanMount()end)
    read('prohibited',function()return component:IsProhibited()end)
    read('equipped_mount_available',function()return valid(component:GetEquippedMountData())end)
    read('teleporting',function()return pawn:IsTeleporting()end)
    read('falling',function()
        local movement=pawn:GetMovementComponent();assert(valid(movement),'Movement unavailable')
        return movement:IsFalling()
    end)
    read('interacting',function()
        local value,why=require('mount_interaction').is_interacting(pawn)
        assert(value~=nil,why);return value
    end)
    read('other_blockers',function()
        local rows={};assert(#blockers<=64,'Unexpected native blocker count')
        for _,blocker in ipairs(blockers)do
            local value={TagName=FName(blocker)}
            rows[#rows+1]={tag=blocker,present=tags:HasTag(value,false),count=tags:CountTag(value)}
        end
        return rows
    end)
    read('replicated',function()return replicated_tags(tags)end)
    -- Absence in the replicated array does not prove adapter ownership.
    return out
end

-- Optional read-only diagnostic. This performs no tag mutation or input action.
function M.inspect(pawn,pc)
    local result={schema='S3MOUNTCONTROL1',read_only=true}
    local ok,why=pcall(function()
        local component,tags,subsystem,action,blockers=resolve(pawn,pc)
        result.tag=tagName;result.count=tags:CountTag(tag())
        result.can_mount=component:CanMount();result.prohibited=component:IsProhibited()
        result.mounted=component:IsMounted();result.keys=mapped_keys(subsystem,action)
        result.action=name(action);result.subsystem=name(subsystem)
        result.blocker_names=blockers
        result.diagnostics=diagnostics(pawn,component,tags,blockers)
    end)
    if not ok then result.error=tostring(why)end
    return result
end

local function player_reason(pawn,component,tags)
    if not valid(component:GetEquippedMountData())then return 'native_mount_unavailable'end
    if component:GetMountState()~=1 then return 'native_mount_transition'end
    if pawn:IsTeleporting()then return 'teleporting'end
    local movement=pawn:GetMovementComponent();assert(valid(movement),'Native movement component unavailable')
    if movement:IsFalling()then return 'falling'end
    local shelterException=false
    for _,name in ipairs(state.blocker_names)do
        if tags:HasTag({TagName=FName(name)},false)then
            -- This eligibility path belongs only to the selected skateboard.
            -- Keep the native shelter tag, so ordinary mounts stay prohibited.
            -- An unknown child tag alone does not acquire this exception.
            if name==skateboardShelterTag and tags:HasTag({TagName=FName(name)},true)then
                shelterException=true
            else return name end
        end
    end
    local interacting,interactionError=require('mount_interaction').is_interacting(pawn)
    assert(interacting~=nil,interactionError)
    if interacting then return 'interacting'end
    return nil,shelterException
end

-- A skateboard can start in a verified static no-mount area. This does not
-- remove that area's native tag or make ordinary mounts eligible. Unknown
-- replicated owners (including cyclone effects) still block entry. Matching
-- the complete replicated count prevents a known area from masking an extra
-- effect that uses the same tag.
local function native_restriction(pawn,tags,shelterException)
    local count=replicated_tags(tags).mount_blocked_count
    local report={replicated_count=count,area_exception=false,shelter_exception=shelterException==true}
    state.native_restriction=report
    if count==0 then return end
    local zones=require('mount_zone').inspect(pawn)
    report.zones=zones
    if zones.ready==true and zones.mount_blocked_count==count then
        report.area_exception=true;return
    end
    return 'native_mount_blocked'
end
local function entry_reason(pawn,component,tags)
    local count=tags:CountTag(tag())
    if count~=1 then return count>1 and'another_mount_blocker'or'adapter_blocker_unavailable'end
    local reason,shelterException=player_reason(pawn,component,tags)
    return reason or native_restriction(pawn,tags,shelterException)
end

-- Recheck immediately before entering after asynchronous collision preparation.
-- Read-only: this never acquires/releases a tag, polls keys, or consumes a toggle.
function M.can_enter(pawn,pc)
    local ok,reason=pcall(function()
        if not state.ready or not state.owned then return 'adapter_blocker_unavailable'end
        if not valid(pawn)or not valid(pc)or state.pawn~=name(pawn)or state.controller~=name(pc)or
            state.world~=name(pawn:GetWorld())or state.world~=name(pc:GetWorld())then return 'player_changed'end
        local component,tags=find(state.component),find(state.tags)
        if not component or not tags then return 'native_components_unavailable'end
        if not component:IsProhibited()or component:CanMount()then return 'native_suppression_unconfirmed'end
        return entry_reason(pawn,component,tags)
    end)
    if not ok then return false,tostring(reason)end
    return reason==nil,reason
end

-- One synchronous, no-input causality check. The trial owns its one local tag
-- only between the add and protected cleanup in this call. It never enables the
-- production input route or changes the native equipped mount.
function M.suppression_trial(pawn)
    local result={schema='S3MOUNTSUPPRESSION1',complete=false,mutation_performed=false,
        input_allowed=false,tag=tagName,restored=false}
    if state.owned then result.waiting_reason='existing_adapter_lease';return result end
    local component,tags
    local function snapshot()
        return {tag_count=tags:CountTag(tag()),can_mount=component:CanMount(),
            prohibited=component:IsProhibited(),mounted=component:IsMounted()}
    end
    local ok,why=pcall(function()
        -- Keep this independent of the unenabled input/entry implementation.
        signature('/Script/Dominion.DominionPlayerCharacter:GetMountComponent',{'ObjectProperty'})
        signature('/Script/Dominion.DominionCharacterBase:GetGameplayTagsComponent',{'ObjectProperty'})
        for _,method in ipairs({'AddLocalTag','RemoveLocalTag'})do signature(tagsBase..method,{'StructProperty'})end
        signature(tagsBase..'CountTag',{'StructProperty','IntProperty'})
        for _,method in ipairs({'CanMount','IsProhibited','IsMounted'})do signature(mountBase..method,{'BoolProperty'})end
        signature('/Script/GameplayTags.BlueprintGameplayTagLibrary:IsGameplayTagValid',{'StructProperty','BoolProperty'})
        local class=StaticFindObject('/Script/Dominion.DominionPlayerCharacter')
        assert(valid(class)and valid(pawn)and pawn:IsA(class),'Playable character unavailable')
        component=pawn:GetMountComponent();tags=pawn:GetGameplayTagsComponent()
        assert(valid(component)and valid(tags),'Native mount/tag components unavailable')
        local library=StaticFindObject('/Script/GameplayTags.Default__BlueprintGameplayTagLibrary')
        assert(valid(library)and library:IsGameplayTagValid(tag()),'Native mount blocker tag unavailable')
        result.before=snapshot()
        if result.before.mounted or not result.before.can_mount or result.before.prohibited or result.before.tag_count~=0 then
            result.waiting_reason=result.before.mounted and'ordinary_mount_active'or'native_mount_unavailable'
            result.restored=true;return
        end
        state.tags=name(tags) -- retained only for cleanup retry if RemoveLocalTag fails
        local added,addError=pcall(function()tags:AddLocalTag(tag())end)
        state.owned=added
        result.mutation_performed=added
        if not added then
            local counted,count=pcall(function()return tags:CountTag(tag())end)
            if counted and count==result.before.tag_count+1 then
                state.owned=true;result.mutation_performed=true
            end
            error(addError)
        end
        result.after_add=snapshot()
        result.causal_suppression=result.after_add.tag_count==result.before.tag_count+1 and
            result.after_add.prohibited and not result.after_add.can_mount and not result.after_add.mounted
        assert(result.causal_suppression,'Native CanMount suppression was not causally confirmed')
    end)
    if not ok then result.error=tostring(why)end
    if result.mutation_performed or state.owned then
        local cleaned,cleanupError=pcall(release)
        if not cleaned then result.cleanup_error=tostring(cleanupError)end
        local checked,checkError=pcall(function()
            result.after_restore=snapshot()
            local a,b=result.after_restore,result.before
            result.restored=cleaned and a.tag_count==b.tag_count and a.can_mount==b.can_mount and
                a.prohibited==b.prohibited and a.mounted==b.mounted
        end)
        if not checked then result.restore_read_error=tostring(checkError)end
    end
    result.complete=ok and result.causal_suppression==true and result.restored
    result.cleanup_pending=state.owned
    return result
end

-- Call on the existing engine tick. input_allowed must explicitly be true and
-- must include the bridge's UI, paused-game and gameplay readiness checks.
-- 0.25 seconds and one-shot behavior match the owned IMC_Combat/MountedMovement.
function M.update(pawn,pc,selected,options)
    options=options or{}
    if not selected then M.shutdown();return false,M.status()end
    local ok,why=pcall(function()
        local now=assert(options.clock,'A monotonic mount input clock is required')()
        if state.pawn and(state.pawn~=name(pawn)or state.world~=name(pawn:GetWorld()))then
            assert(M.shutdown(),'Could not release previous character mount blocker')
            state={ready=false,owned=false,queued=false,released=false}
        end
        local component,tags,subsystem,action=find(state.component),find(state.tags),find(state.subsystem),find(state.action)
        if not component or not tags or not subsystem or not action then component,tags,subsystem,action=resolve(pawn,pc,true)end
        if not state.owned then
            local before=tags:CountTag(tag());state.before_can_mount=component:CanMount()
            state.before_count=before;state.before_prohibited=component:IsProhibited();state.before_mounted=component:IsMounted()
            state.ready=false;state.suppressed=false;state.error=nil
            local waiting=state.before_mounted and'ordinary_mount_active'or nil
            if not waiting and(before~=0 or not state.before_can_mount or state.before_prohibited)then
                local shelterException
                waiting,shelterException=player_reason(pawn,component,tags)
                if not waiting then
                    -- Bounded provenance checks while waiting; re-evaluate
                    -- immediately before actual entry through can_enter.
                    if now<(state.next_area_check or -math.huge)then return end
                    state.next_area_check=now+2
                    waiting=native_restriction(pawn,tags,shelterException)
                    if not waiting and not state.native_restriction.area_exception and not shelterException then
                        waiting=before>0 and'native_mount_blocked'or'native_mount_unavailable'
                    end
                end
            end
            if waiting then
                state.waiting_reason=waiting
                state.released=false;state.held_since=nil;state.queued=false
                if now-(state.last_diagnostic or -math.huge)>=2 then
                    state.diagnostics=diagnostics(pawn,component,tags,state.blocker_names)
                    state.last_diagnostic=now
                end
                return
            end
            state.waiting_reason=nil
            -- AddLocalTag/RemoveLocalTag operate on one stack (verified native
            -- wrappers), unlike the Unique/Clear APIs. Never clear another owner.
            local added,addError=pcall(function()tags:AddLocalTag(tag())end)
            state.owned=added -- completed AddLocalTag owns one stack before readback
            assert(added,addError)
            local after=tags:CountTag(tag())
            -- CountTag prefers the local map. With a native area stack already
            -- present, a successful local add legitimately reads 1 -> 1. A
            -- pre-existing foreign local stack reads >=2; release only ours.
            assert(after==1,'Another local mount blocker is active')
            state.next_area_check=nil
            state.released=false;state.held_since=nil;state.last_keys=-math.huge
        end
        state.count=tags:CountTag(tag())
        state.prohibited=component:IsProhibited();state.after_can_mount=component:CanMount()
        state.suppressed=state.count>0 and state.prohibited and not state.after_can_mount
        assert(state.suppressed,'Native CanMount suppression was not confirmed')
        state.ready=true;state.error=nil
        if now-(state.last_keys or -math.huge)>=0.5 then
            local keys=mapped_keys(subsystem,action);local joined=table.concat(keys,'|')
            if joined~=state.key_signature then state.released=false;state.held_since=nil end
            state.keys=keys;state.key_signature=joined;state.last_keys=now
        end
        local allowed=options.input_allowed==true and(options.active==true or not component:IsMounted())
        if not allowed then state.released=false;state.held_since=nil;state.queued=false;return end
        local down=false
        for _,key in ipairs(state.keys or{})do
            if pc:IsInputKeyDown({KeyName=FName(key)})then down=true;break end
        end
        state.down=down
        if not down then state.released=true;state.held_since=nil;state.fired=false
        elseif state.released and not state.fired then
            state.held_since=state.held_since or now
            if now-state.held_since>=0.25 then
                state.fired=true
                local reason
                if options.active~=true then
                    reason=entry_reason(pawn,component,tags)
                end
                state.entry_blocked=reason
                if not reason then state.queued=true;state.toggles=(state.toggles or 0)+1 end
            end
        end
    end)
    if not ok then
        local failure=tostring(why);local cleaned=M.shutdown()
        state.error=failure..(cleaned and''or'; '..tostring(state.error))
        return false,M.status()
    end
    return state.ready,M.status()
end
function M.consume_toggle()
    local value=state.ready and state.suppressed and state.queued==true
    state.queued=false;return value
end
function M.status()
    return {schema='S3MOUNTCONTROL1',ready=state.ready,error=state.error,owns_one_stack=state.owned,
        tag=tagName,tag_count=state.count,native_prohibited=state.prohibited,suppressed=state.suppressed,
        before_can_mount=state.before_can_mount,after_can_mount=state.after_can_mount,
        before_tag_count=state.before_count,before_prohibited=state.before_prohibited,before_mounted=state.before_mounted,
        release_before_tag_count=state.release_before_count,release_after_tag_count=state.release_after_count,
        waiting_reason=state.waiting_reason,entry_blocked=state.entry_blocked,blocker_names=state.blocker_names,
        diagnostics=state.diagnostics,
        native_restriction=state.native_restriction,
        keys=state.keys,toggles=state.toggles or 0,down=state.down,queued=state.queued}
end
return M
