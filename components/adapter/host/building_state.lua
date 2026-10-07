-- Actual lightweight collision snapshots. Reads only: no native calls other
-- than the pinned RPM helper and verified reflected getters.
local function create()
local M={}
local pending
local function valid(o)return o and o:IsValid()end
local function text(v)return type(v)=='string'and v or v:ToString()end
local function name(o)assert(valid(o),'Building object unavailable');return o:GetFullName()end
local function find(n)local o=StaticFindObject(n:gsub('^[^ ]+ ',''));assert(valid(o),'Building object disappeared: '..n);return o end
local function integer(v,limit)assert(type(v)=='number'and v%1==0 and v>=0 and v<=limit,'Invalid building integer');return v end
local function addr(o)return integer(o:GetAddress(),9007199254740991)end
local function finite(v)assert(type(v)=='number'and v==v and math.abs(v)<math.huge,'Invalid building coordinate');return v end
local function boolean(v)assert(type(v)=='boolean','Invalid building boolean');return v end
local function fields(class,wanted)
 local found={};find(class):ForEachProperty(function(p)
  local n=text(p:GetFName());local s=wanted[n];if not s then return end
  assert(text(p:GetClass():GetFName())==s[1],'Changed building field type: '..n)
  if s[2]then assert(name(p:GetStruct())=='ScriptStruct '..s[2],'Changed building struct: '..n)end
  if s.object and p.GetPropertyClass~=nil then assert(type(p.GetPropertyClass)=='function'and name(p:GetPropertyClass())=='Class '..s.object,'Changed building object property: '..n)end
  found[n]=true
 end)
 for n in pairs(wanted)do assert(found[n],'Missing building field: '..n)end
end
local function verify()
 fields('/Script/Dominion.GlobalBuildingManager',{BuildingPieces={'MapProperty'},LightweightBuildingPieceManager={'ObjectProperty',object='/Script/Dominion.LightweightBuildingPieceManager'}})
 fields('/Script/Dominion.BuildingPieceState',{ClientVisible={'StructProperty','/Script/Dominion.ClientVisibleBuildingPieceState'}})
 fields('/Script/Dominion.ClientVisibleBuildingPieceState',{BuildingPieceDataIndex={'IntProperty'},Location={'StructProperty','/Script/CoreUObject.Vector'},Yaw={'FloatProperty'},bGhosted={'BoolProperty'}})
 fields('/Script/Dominion.BuildingPieceData',{BuildingPieceDataIndex={'IntProperty'}})
 fields('/Script/Engine.StaticMesh',{BodySetup={'ObjectProperty',object='/Script/Engine.BodySetup'}})
 fields('/Script/Dominion.BuildingSettings',{ActiveCollisionProfileName={'StructProperty','/Script/Engine.CollisionProfileName'},InactiveCollisionProfileName={'StructProperty','/Script/Engine.CollisionProfileName'}})
 fields('/Script/Engine.CollisionProfileName',{Name={'NameProperty'}})
 local enum='ByteProperty|EnumProperty'
 for fn,expected in pairs({['Actor:GetActorEnableCollision']={'BoolProperty'},
  ['PrimitiveComponent:GetCollisionEnabled']={enum},['PrimitiveComponent:GetCollisionObjectType']={enum},
  ['PrimitiveComponent:GetCollisionProfileName']={'NameProperty'},['PrimitiveComponent:GetCollisionResponseToChannel']={enum,enum}})do
  local actual={};find('/Script/Engine.'..fn):ForEachProperty(function(p)actual[#actual+1]=text(p:GetClass():GetFName())end)
  assert(#actual==#expected,'Changed building getter signature: '..fn)
  for i,k in ipairs(expected)do assert(('|'..k..'|'):find('|'..actual[i]..'|',1,true),'Changed building getter parameter: '..fn)end
 end
end
local function same_world(o,p)return valid(o)and valid(o:GetWorld())and name(o:GetWorld())==p.world and addr(o:GetWorld())==p.world_address end
local function identity(p)
 local pawn=find(p.pawn);assert(addr(pawn)==p.pawn_address and same_world(pawn,p),'Building player/world identity changed')
 local owner=find(p.owner);assert(addr(owner)==p.owner_address and owner:IsA(p.classes.owner)and same_world(owner,p),'Building owner identity changed')
 local c=owner.LightweightBuildingPieceManager
 assert(valid(c)and c:IsA(p.classes.component)and addr(c)==p.manager_address and same_world(c,p),'Building manager identity changed')
 local mass=find(p.mass);assert(addr(mass)==p.mass_address and mass:IsA(p.classes.mass)and same_world(mass,p),'Building Mass identity changed')
 return pawn,owner,c,mass
end
local function primitive(c)
 local r={name=name(c),enabled=integer(c:GetCollisionEnabled(),5),object_type=integer(c:GetCollisionObjectType(),31),profile=text(c:GetCollisionProfileName()),responses={}}
 for i=0,31 do r.responses[i+1]=integer(c:GetCollisionResponseToChannel(i),2)end
 return r
end
local function serial(v)
 -- Deterministic structural comparison, including every native policy byte.
 if type(v)~='table'then return type(v)..':'..tostring(v)end
 local keys={};for k in pairs(v)do keys[#keys+1]=k end;table.sort(keys,function(a,b)return tostring(a)<tostring(b)end)
 local out={};for _,k in ipairs(keys)do out[#out+1]=serial(k)..'='..serial(v[k])end;return '{'..table.concat(out,';')..'}'
end
local function equal(a,b)
 if type(a)~=type(b)then return false end
 if type(a)~='table'then return a==b end
 for k,v in pairs(a)do if not equal(v,b[k])then return false end end
 for k in pairs(b)do if a[k]==nil then return false end end;return true
end
local function policy_values(manager)
 local c=manager.component
 return {{owner_collision=manager.owner_collision,component={enabled=c.enabled,object_type=c.object_type,profile=c.profile,responses=c.responses}}}
end
local function configure_baseline(p,baseline)
 if p.incremental and type(baseline)=='table'and baseline.schema=='S3BUILDINGBASELINE1'
  and baseline.world==p.world and baseline.world_session==p.result.world_session and baseline.game_exe_sha256==p.result.game_exe_sha256
  and type(baseline.descriptors)=='table'and type(baseline.native_piece_ids)=='table'
  and equal(baseline.manager_policy,policy_values(p.result.managers[1]))then
  p.baseline=baseline;p.previously_native={}
  for _,id in ipairs(baseline.native_piece_ids)do integer(id,4294967295);assert(not p.previously_native[id],'Duplicate baseline native ID');p.previously_native[id]=true end
 end
 p.result.capture_mode=p.baseline and 'delta'or'full'
end
local function inventory_ids(owner)
 local ids,seen={},{};local expected=integer(#owner.BuildingPieces,50000)
 owner.BuildingPieces:ForEach(function(k)
  local id=integer(k:get(),4294967295);assert(not seen[id],'Duplicate global building ID');seen[id]=true
  ids[#ids+1]=id;assert(#ids<=50000,'Building inventory exceeds limit')
 end)
 assert(#ids==expected,'Global building inventory changed during read')
 table.sort(ids);return ids
end
local function descriptor(owner,id)
 local cv=owner.BuildingPieces:Find(id):get().ClientVisible;local location=cv.Location
 return {id=id,data_index=integer(cv.BuildingPieceDataIndex,2147483647),
  location={finite(location.X),finite(location.Y),finite(location.Z)},yaw=finite(cv.Yaw),ghosted=boolean(cv.bGhosted)}
end
local function fail(p,why)
 p.result.error=tostring(why);p.result.capture_failure_phase=p.phase
 p.result.errors[#p.result.errors+1]=p.result.error;p.result.complete=false;p.result.collision_verified=false;pending=nil;return p.result
end
local function check_asset(p,address)
 local data=p.assets[address];local a=find(data.name)
 assert(a:IsA(p.classes.asset)and addr(a)==address and a.BuildingPieceDataIndex==data.index,'Building asset identity changed')
end
local function check_derived(p,address)
 local d=find(p.derived[address]);assert(d:IsA(p.classes.derived)and addr(d)==address,'Derived building identity changed')
end
local function check_body(p,address)
 local known=p.bodies[address];local mesh,body=find(known.mesh),find(known.body)
 assert(mesh:IsA(p.classes.mesh)and body:IsA(p.classes.body)and addr(body)==address
  and valid(mesh.BodySetup)and addr(mesh.BodySetup)==address,'Actual mesh/BodySetup identity changed')
end
local function check_end(p,pawn,owner,c)
 assert(equal(p.reader.ids(c),p.all_ids),'Native building ID inventory changed')
 assert(equal(inventory_ids(owner),p.result.inventory_piece_ids),'Global building ID inventory changed')
 local policy={name=p.owner,owner_collision=boolean(owner:GetActorEnableCollision()),component=primitive(c)}
 assert(serial(policy)==p.policy_key,'Building manager collision policy changed')
 assert(require('skate_world').read(pawn).session==p.result.world_session,'Building save identity changed')
end
function M.begin(pawn,options)
 assert(not pending,'Building collision capture already running');options=options or{}
 verify();local world=pawn:GetWorld();local save=require('skate_world').read(pawn)
 local p={clock=options.clock or os.clock,pawn=name(pawn),pawn_address=addr(pawn),world=name(world),world_address=addr(world),
  classes={owner=find('/Script/Dominion.GlobalBuildingManager'),component=find('/Script/Dominion.LightweightBuildingPieceManager'),
   mass=find('/Script/JagexMassEntity.JgxMassSubsystem'),asset=find('/Script/Dominion.BuildingPieceData'),derived=find('/Script/Dominion.BuildingPieceDerivedData'),
   mesh=find('/Script/Engine.StaticMesh'),body=find('/Script/Engine.BodySetup')},phase='inventory',index=1,
  assets={},asset_indices={},derived={},bodies={},client={},packet_keys={},reader=require('building_native'),packet_sink=options.packet_sink,
  used_assets={},used_derived={},used_bodies={},incremental=options.incremental==true,baseline_loader=options.baseline_loader}
 p.started=p.clock();p.result={schema='S3BUILDINGSTATE2',scope='lightweight_pieces',complete=false,collision_verified=false,
  world=p.world,world_session=save.session,errors={},managers={},data_assets={},derived_assets={},body_bindings={},inventory={},inventory_piece_ids={},native_missing_piece_ids={},pieces={}}
 local config=require('skate_config');assert(type(config.game_exe_sha256)=='string'and #config.game_exe_sha256==64 and config.game_exe_sha256:match('^[a-fA-F0-9]+$'),'Pinned game identity is unavailable')
 p.result.game_exe_sha256=config.game_exe_sha256:lower()
 local settings=find('/Script/Dominion.Default__BuildingSettings')
 p.result.settings={active_profile=text(settings.ActiveCollisionProfileName.Name),inactive_profile=text(settings.InactiveCollisionProfileName.Name)}
 for _,o in ipairs(FindAllOf('GlobalBuildingManager')or{})do
  if valid(o)and o:IsA(p.classes.owner)and same_world(o,p)and not name(o):find('Default__',1,true)then
   assert(not p.owner,'Multiple same-world global building managers');p.owner=name(o);p.owner_address=addr(o)
  end
 end
 assert(p.owner,'Global building manager unavailable');local owner=find(p.owner);local c=owner.LightweightBuildingPieceManager
 assert(valid(c)and c:IsA(p.classes.component)and same_world(c,p),'Native building manager unavailable');p.manager_address=addr(c)
 for _,o in ipairs(FindAllOf('JgxMassSubsystem')or{})do
  if valid(o)and o:IsA(p.classes.mass)and same_world(o,p)and not name(o):find('Default__',1,true)then
   assert(not p.mass,'Multiple same-world Mass subsystems');p.mass=name(o);p.mass_address=addr(o)
  end
 end
 assert(p.mass,'Same-world Mass subsystem unavailable')
 p.result.managers[1]={name=p.owner,owner_collision=boolean(owner:GetActorEnableCollision()),component=primitive(c)}
 p.policy_key=serial(p.result.managers[1]);p.result.inventory_piece_ids=inventory_ids(owner)
 configure_baseline(p,options.baseline);p.result.retained_loaded_piece_ids={}
 if p.baseline_loader then assert(type(p.baseline_loader.advance)=='function','Invalid baseline decoder');p.phase='baseline'end
 -- FindAllOf does one native discovery per class. Validation is bounded across
 -- subsequent ticks; no reflected function/type inventories or HISM scans.
 p.asset_objects=FindAllOf('BuildingPieceData')or{}
 pending=p;return true
end
function M.cancel()pending=nil end
function M.busy()return pending~=nil end
function M.advance(maxItems,budgetSeconds)
 local p=pending;if not p then return end
 local started=p.clock();local n=0
 local ok,why=pcall(function()
  local pawn,owner,c,mass=identity(p)
  while n<(maxItems or 64)do
   if n>0 and budgetSeconds and p.clock()-started>=budgetSeconds then break end;n=n+1
   if p.phase=='baseline'then
    local value=p.baseline_loader.advance()
    if value then configure_baseline(p,value);p.baseline_loader=nil;p.phase='inventory';p.index=1 end
   elseif p.phase=='inventory'or p.phase=='verify_global'then
    local id=p.result.inventory_piece_ids[p.index]
    if id then
     local row=descriptor(owner,id)
     if p.phase=='inventory'then p.client[id]=row;p.result.inventory[#p.result.inventory+1]=row
     else assert(equal(row,p.client[id]),'Global building descriptor changed during capture')end
     p.index=p.index+1
    elseif p.phase=='inventory'then
     assert(equal(inventory_ids(owner),p.result.inventory_piece_ids),'Global building ID inventory changed');p.phase='assets';p.index=1
    else check_end(p,pawn,owner,c);p.phase='done';break end
   elseif p.phase=='assets'or p.phase=='derived'or p.phase=='meshes'then
    if p.phase=='derived'and not p.derived_objects then p.derived_objects=FindAllOf('BuildingPieceDerivedData')or{}end
    if p.phase=='meshes'and not p.mesh_objects then p.mesh_objects=FindAllOf('StaticMesh')or{}end
    local list=p.phase=='assets'and p.asset_objects or p.phase=='derived'and p.derived_objects or p.mesh_objects
    local o=list[p.index]
    if not o then
     if p.phase=='assets'then p.asset_objects=nil;p.phase='ids'
     elseif p.phase=='derived'then p.derived_objects=nil;p.phase='meshes'
     else p.mesh_objects=nil;p.phase='pieces'end;p.index=1
    else
     p.index=p.index+1
     if valid(o)and not name(o):find('Default__',1,true)and not name(o):find(' /Temp/',1,true)then
      if p.phase=='assets'then
       assert(o:IsA(p.classes.asset),'Changed building data class');local index=o.BuildingPieceDataIndex
       if index>=0 then
        integer(index,2147483647);assert(not p.assets[addr(o)],'Duplicate building data pointer')
        p.assets[addr(o)]={name=name(o),index=index,address=addr(o)};p.result.data_assets[#p.result.data_assets+1]={name=name(o),index=index,address=addr(o)}
        assert(not p.asset_indices[index]or p.asset_indices[index]==name(o),'Duplicate runtime building asset index');p.asset_indices[index]=name(o)
       end
      elseif p.phase=='derived'then
       assert(o:IsA(p.classes.derived),'Changed derived data class');p.derived[addr(o)]=name(o)
      else
       assert(o:IsA(p.classes.mesh),'Changed static mesh class');local body=o.BodySetup
       if valid(body)then
        assert(body:IsA(p.classes.body),'Changed mesh BodySetup class')
        local address=addr(body);local prior=p.bodies[address]
        assert(not prior or prior.mesh==name(o),'Ambiguous shared mesh BodySetup')
        p.bodies[address]={mesh=name(o),body=name(body)}
       end
      end
     end
    end
   elseif p.phase=='ids'then
    p.all_ids=p.reader.ids(c);p.result.native_piece_ids=p.all_ids;p.ids={};local seen={}
    for _,id in ipairs(p.all_ids)do
     local current=p.client[id];assert(current,'Native building absent from global inventory');seen[id]=true
     local asset=p.asset_indices[current.data_index];assert(asset,'Native Global asset index has no typed binding')
     local descriptor={asset=asset:gsub('^[^ ]+ ',''),location=current.location,yaw=current.yaw,ghosted=current.ghosted}
     if p.baseline and p.previously_native[id]and equal(p.baseline.descriptors[tostring(id)],descriptor)then
      p.result.retained_loaded_piece_ids[#p.result.retained_loaded_piece_ids+1]=id
     else p.ids[#p.ids+1]=id end
    end
    for _,id in ipairs(p.result.inventory_piece_ids)do if not seen[id]then p.result.native_missing_piece_ids[#p.result.native_missing_piece_ids+1]=id end end
    p.phase=#p.ids>0 and'derived'or'pieces';p.index=1
   elseif p.phase=='pieces'or p.phase=='verify'then
    local ids={};for i=p.index,math.min(p.index+3,#p.ids)do ids[#ids+1]=p.ids[i]end
    if #ids==0 then
     if #p.ids==0 then p.phase='verify_global';p.index=1
     elseif p.phase=='pieces'then check_end(p,pawn,owner,c);p.phase='verify';p.index=1
     else
      p.binding_checks={}
      for address in pairs(p.used_assets)do p.binding_checks[#p.binding_checks+1]={check_asset,address}end
      for address in pairs(p.used_derived)do p.binding_checks[#p.binding_checks+1]={check_derived,address}end
      for address in pairs(p.used_bodies)do p.binding_checks[#p.binding_checks+1]={check_body,address}end
      p.phase='bindings';p.index=1
     end
    else
     if p.phase=='verify'then
      local packet=p.reader.body_packet(c,mass,ids)
      assert(type(packet)=='string'and packet==p.packet_keys[p.index],'Native building packet changed during capture')
     else
     local rows,packet=p.reader.bodies(c,mass,ids)
     assert(type(packet)=='string','Native building packet stream unavailable');p.packet_keys[p.index]=packet
     for _,r in ipairs(rows)do
      local data=p.assets[r.data_asset_address];local derived=p.derived[r.derived_asset_address]
      assert(data and derived,'Native building assets are not in typed loaded inventory')
      if not p.used_assets[r.data_asset_address]then check_asset(p,r.data_asset_address);p.used_assets[r.data_asset_address]=true end
      assert(p.client[r.id].data_index==data.index and p.client[r.id].ghosted==r.ghosted,'Native/global building descriptor mismatch')
      r.data_asset=data.name;r.data_index=data.index;r.derived_asset=derived
      if not p.used_derived[r.derived_asset_address]then
       check_derived(p,r.derived_asset_address)
       p.used_derived[r.derived_asset_address]=true;p.result.derived_assets[#p.result.derived_assets+1]={address=r.derived_asset_address,name=derived}
      end
      for _,e in ipairs(r.entities)do
       if e.physics_present and(e.collision.enabled==1 or e.collision.enabled==3 or e.collision.enabled==5)then
        local known=p.bodies[e.body_setup_address];assert(known,'Actual native BodySetup is not a typed loaded StaticMesh body')
        e.body_mesh=known.mesh;e.body_setup_asset=known.body;e.geometry_verified=true
        if not p.used_bodies[e.body_setup_address]then
         check_body(p,e.body_setup_address)
         p.used_bodies[e.body_setup_address]=true;p.result.body_bindings[#p.result.body_bindings+1]={address=e.body_setup_address,mesh=known.mesh,body=known.body}
        end
       end
      end
      p.result.pieces[#p.result.pieces+1]=r
     end
     if p.phase=='pieces'and p.packet_sink then
      p.packet_sink(packet)
     end
     end
     p.index=p.index+#ids
    end
   elseif p.phase=='bindings'then
    local value=p.binding_checks[p.index]
    if value then value[1](p,value[2]);p.index=p.index+1
    else p.phase='verify_global';p.index=1 end
   else break end
  end
 end)
 if not ok then return fail(p,why)end
 if p.phase~='done'then return nil end
 table.sort(p.result.data_assets,function(a,b)return a.index<b.index end)
 p.result.complete=true;p.result.collision_verified=true;p.result.elapsed_seconds=p.clock()-p.started
 p.result.native_reader={schema=2,profile='pinned_pe_code_vtable',manager_address=p.manager_address,mass_address=p.mass_address,
  verified_passes=2,loaded_piece_count=#p.ids,native_piece_count=#p.all_ids,global_piece_count=#p.result.inventory_piece_ids}
 pending=nil;return p.result
end
return M
end
local default=create();default.new=create;return default
