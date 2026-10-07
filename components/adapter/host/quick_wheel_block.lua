-- Private Enhanced Input context consumes only the quick-access radial's
-- currently mapped opener keys. Existing actions/contexts are never modified.
local M={}
local mode='/Script/EnhancedInput.'
local subsystemClass=mode..'EnhancedInputLocalPlayerSubsystem'
local controllerClass='/Script/Dominion.DominionPlayerController'
local actionClass=mode..'InputAction'
local contextClass=mode..'InputMappingContext'
local priority=100000
local actions={
    '/Game/Gameplay/Inputs/Actions/UI/IA_QuickAccessRadial.IA_QuickAccessRadial',
    '/Game/Gameplay/Inputs/Actions/UI/IA_QuickAccessRadialToggle.IA_QuickAccessRadialToggle',
}
local state={}
local verified=false
local lastError
local function valid(o)return o~=nil and o:IsValid()end
local function find(path)return StaticFindObject(path:gsub('^[^ ]+ ',''))end
local function required(path)
    local o=find(path);assert(valid(o),'Quick wheel API unavailable: '..path);return o
end
local function identity(o,class)
    assert(valid(o)and o:IsA(required(class)),'Unexpected quick wheel object class')
    local address=o:GetAddress()
    assert(type(address)=='number'and address>0 and address%1==0 and address<=9007199254740991,'Invalid quick wheel object address')
    return {name=o:GetFullName(),address=address}
end
local function same(a,b)return a and b and a.name==b.name and a.address==b.address end
local function resolve(item,class)
    if not item then return nil end
    local o=find(item.name)
    if not valid(o)or o:GetFullName()~=item.name or o:GetAddress()~=item.address then return nil end
    assert(o:IsA(required(class)),'Owned quick wheel object class changed');return o
end
local function signature(path,expected)
    local actual={}
    required(path):ForEachProperty(function(p)actual[#actual+1]=p end)
    assert(#actual==#expected,'Changed quick wheel signature: '..path)
    for i,want in ipairs(expected)do
        local p=actual[i]
        assert(p:GetFName():ToString()==want[1]and p:GetClass():GetFName():ToString()==want[2],
            'Changed quick wheel parameter: '..path..':'..want[1])
        if want[3]then assert(p:GetStruct():GetFullName()=='ScriptStruct '..want[3],'Changed quick wheel parameter struct')end
    end
end
local function fields(path,wanted)
    local found={}
    required(path):ForEachProperty(function(p)
        local n=p:GetFName():ToString();local kind=wanted[n]
        if kind then
            assert(p:GetClass():GetFName():ToString()==kind,'Changed quick wheel field: '..path..':'..n)
            found[n]=true
        end
    end)
    for n in pairs(wanted)do assert(found[n],'Missing quick wheel field: '..path..':'..n)end
end
local function verify()
    if verified then return end
    signature('/Script/Engine.SubsystemBlueprintLibrary:GetLocalPlayerSubSystemFromPlayerController',{
        {'PlayerController','ObjectProperty'},{'Class','ClassProperty'},{'ReturnValue','ObjectProperty'}})
    signature(mode..'EnhancedInputSubsystemInterface:QueryKeysMappedToAction',{
        {'Action','ObjectProperty'},{'ReturnValue','ArrayProperty'}})
    signature(mode..'EnhancedInputSubsystemInterface:HasMappingContext',{
        {'MappingContext','ObjectProperty'},{'OutFoundPriority','IntProperty'},{'ReturnValue','BoolProperty'}})
    signature(mode..'EnhancedInputSubsystemInterface:AddMappingContext',{
        {'MappingContext','ObjectProperty'},{'Priority','IntProperty'},{'Options','StructProperty',mode..'ModifyContextOptions'}})
    signature(mode..'EnhancedInputSubsystemInterface:RemoveMappingContext',{
        {'MappingContext','ObjectProperty'},{'Options','StructProperty',mode..'ModifyContextOptions'}})
    signature(contextClass..':MapKey',{
        {'Action','ObjectProperty'},{'ToKey','StructProperty','/Script/InputCore.Key'},
        {'ReturnValue','StructProperty',mode..'EnhancedActionKeyMapping'}})
    fields(mode..'ModifyContextOptions',{bForceImmediately='BoolProperty',bIgnoreAllPressedKeysUntilRelease='BoolProperty',bNotifyUserSettings='BoolProperty'})
    fields(actionClass,{bConsumeInput='BoolProperty',bConsumesActionAndAxisMappings='BoolProperty',
        bReserveAllMappings='BoolProperty',bTriggerWhenPaused='BoolProperty',ValueType='EnumProperty',Triggers='ArrayProperty',Modifiers='ArrayProperty'})
    fields('/Script/InputCore.Key',{KeyName='NameProperty'})
    verified=true
end
local function options()
    -- Set uses the loader's masked FBoolProperty setter. No out-struct bools
    -- are read. Do not flush keys or register private actions in user settings.
    return {bForceImmediately=true,bIgnoreAllPressedKeysUntilRelease=false,bNotifyUserSettings=false}
end
local function has(subsystem,context,checkPriority)
    local out={}
    local present=subsystem:HasMappingContext(context,out)
    assert(type(present)=='boolean','Invalid mapping context readback')
    if present and checkPriority then assert(out.OutFoundPriority==priority,'Owned quick wheel priority changed')end
    return present
end
local function detach()
    if not state.context then return end
    local subsystem=resolve(state.subsystem,subsystemClass)
    local context=resolve(state.context,contextClass)
    if subsystem and context then
        if has(subsystem,context)then subsystem:RemoveMappingContext(context,options())end
        assert(not has(subsystem,context),'Quick wheel context removal did not apply')
    end
    -- Native applied-context/map references kept these objects alive while
    -- attached. Once detached, forget them and allow ordinary Unreal GC.
    state.context=nil;state.key_count=0
end
local function each(values,fn)
    if type(values)=='table'and type(values.ForEach)~='function'then
        for _,v in ipairs(values)do fn(v)end
    elseif values then values:ForEach(function(_,v)fn(v:get())end)end
end
local function keys(subsystem)
    local result,seen={},{}
    for _,path in ipairs(actions)do
        local action=required(path);identity(action,actionClass)
        assert(action.bReserveAllMappings==false,'Quick wheel action reserves its mappings')
        each(subsystem:QueryKeysMappedToAction(action),function(key)
            if not(type(key)=='table'and rawget(key,'KeyName')~=nil)then
                local kind=key:type()
                assert(kind=='LocalUnrealParam'or kind=='RemoteUnrealParam'or kind=='UScriptStruct','Unexpected mapped quick wheel key')
                if kind~='UScriptStruct'then key=key:get()end
            end
            local n=key.KeyName:ToString()
            assert(type(n)=='string','Invalid mapped quick wheel key name')
            if n~=''and n~='None'and not seen[n]then
                assert(#result<32,'Quick wheel key limit exceeded');seen[n]=true;result[#result+1]=n
            end
        end)
    end
    table.sort(result);return result
end
local function attach(subsystem)
    local mapped=keys(subsystem)
    state.key_count=#mapped
    if #mapped==0 then return end -- Both native open actions are unbound.
    local context=StaticConstructObject(required(contextClass),subsystem,FName('None'),0x40)
    local contextID=identity(context,contextClass)
    local action=StaticConstructObject(required(actionClass),context,FName('None'),0x40)
    identity(action,actionClass)
    action.ValueType=0 -- EInputActionValueType::Boolean, verified by readback.
    action.bConsumeInput=true;action.bConsumesActionAndAxisMappings=false
    action.bReserveAllMappings=false;action.bTriggerWhenPaused=false
    assert(action.ValueType==0 and action.bConsumeInput==true and action.bConsumesActionAndAxisMappings==false
        and action.bReserveAllMappings==false and action.bTriggerWhenPaused==false,'Private quick wheel action setup failed')
    assert(#action.Triggers==0 and #action.Modifiers==0,'Private quick wheel action has unexpected qualifiers')
    for _,key in ipairs(mapped)do context:MapKey(action,{KeyName=FName(key)})end
    -- Record before the engine call: AddMappingContext may add and then throw.
    state.context=contextID
    subsystem:AddMappingContext(context,priority,options())
    assert(has(subsystem,context,true),'Quick wheel context was not applied')
end
function M.pending()return state.lease==true or state.context~=nil end
function M.status()
    return {active=state.lease==true,attached=state.context~=nil,key_count=state.key_count or 0,error=lastError}
end
function M.shutdown()
    state.lease=false
    local ok,why=pcall(detach)
    if not ok then lastError=tostring(why);return false,lastError end
    state={};lastError=nil;return true
end
local function run(pc,gameplayAllowed)
    verify()
    local owner=identity(pc,controllerClass)
    if state.controller and not same(owner,state.controller)then
        local done,why=M.shutdown();assert(done,why)
    end
    local subsystem=required('/Script/Engine.Default__SubsystemBlueprintLibrary'):
        GetLocalPlayerSubSystemFromPlayerController(pc,required(subsystemClass))
    local subsystemID=identity(subsystem,subsystemClass)
    if state.subsystem and not same(subsystemID,state.subsystem)then
        local done,why=M.shutdown();assert(done,why)
    end
    state.controller=owner;state.subsystem=subsystemID;state.lease=true
    if not gameplayAllowed then detach();return end
    local context=resolve(state.context,contextClass)
    if context and has(subsystem,context,true)then return end
    -- The engine may rebuild its complete input context set after a menu or
    -- possession change. Query current bindings without our stale context.
    detach();attach(subsystem)
end
function M.ensure(pc,gameplayAllowed)
    assert(type(gameplayAllowed)=='boolean','Quick wheel ensure requires gameplay eligibility')
    local ok,why=pcall(run,pc,gameplayAllowed)
    if ok then lastError=nil;return true end
    local reason=tostring(why)
    local restored,cleanup=M.shutdown()
    if not restored then reason=reason..'; cleanup pending: '..tostring(cleanup)end
    lastError=reason;return false,reason
end
function M.acquire(pc)return M.ensure(pc,true)end
local previous=rawget(_G,'__DragonwildsSkateQuickWheelBlock')
if previous~=nil then
    assert(type(previous)=='table'and type(previous.shutdown)=='function','Previous quick wheel lease is invalid')
    local ok,restored,why=pcall(previous.shutdown)
    assert(ok and restored,'Previous quick wheel cleanup failed: '..tostring(ok and why or restored))
end
_G.__DragonwildsSkateQuickWheelBlock=M
return M
