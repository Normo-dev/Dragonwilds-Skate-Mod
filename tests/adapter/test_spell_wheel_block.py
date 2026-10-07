"""Native-shaped flag lease fixtures; no engine, input, or installed files."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).parent/'python-deps'))
from lupa import LuaRuntime
SOURCE=(Path(__file__).parent/'host-probe/spell_wheel_block.lua').read_text()
MOCK=r'''
registry={};writes={};fault={};local nextAddress=100
local flag='bIsSpellcastingMenuAllowedToOpen'
local function text(v)return {ToString=function()return v end}end
function object(path,class,prefix)
 nextAddress=nextAddress+1
 local o={path=path,class=class,live=true,address=nextAddress,value=true,other=123}
 o.IsValid=function(self)return self.live end
 o.GetFullName=function(self)return (prefix or'Object')..' '..self.path end
 o.GetAddress=function(self)return self.address end
 o.IsA=function(self,wanted)return self.class==wanted end
 setmetatable(o,{
  __index=function(self,k)
   if k==flag then assert(not self.read_error,'mode read failed');return self.value end
  end,
  __newindex=function(self,k,v)
   if k~=flag then rawset(self,k,v);return end
   assert(type(v)=='boolean');writes[#writes+1]={path=self.path,value=v}
   if self.fail_before==v then error('mode write failed before')end
   if self.noop~=v then self.value=v end
   if self.fail_after==v then error('mode write failed after')end
  end})
 registry[path]=o;return o
end
StaticFindObject=function(path)assert(path~=fault.lookup,'lookup failed');return registry[path]end
FindAllOf=function()error('no enumeration allowed')end
RegisterHook=function()error('no hooks allowed')end
controller=object('/Script/Dominion.DominionPlayerController',nil,'Class')
modeClass=object('/Script/Dominion.DominionInputMode',nil,'Class')
local names={'GameplayInputMode','GameplayLockOnTargetingInputMode','MountedInputMode'}
local function property(n,k)
 return {GetFName=function()return text(n)end,GetClass=function()return {GetFName=function()return text(k)end}end}
end
controller.ForEachProperty=function(_,callback)
 for _,n in ipairs(names)do
  local p=property(n,fault.object_kind or'ObjectProperty')
  if not fault.missing_accessor then p.GetPropertyClass=function()
   assert(not fault.accessor_error,'declared class accessor failed');return fault.wrong_declared and controller or modeClass
  end end
  callback(p)
 end
end
modeClass.ForEachProperty=function(_,callback)callback(property(flag,fault.flag_kind or'BoolProperty'))end
pc=object('/World/Controller',controller)
modes={};for i,n in ipairs(names)do modes[i]=object('/Game/'..n,modeClass);pc[n]=modes[i]end
function load_module()lease=assert(load(source))();return lease end
function all(value)for _,o in ipairs(modes)do assert(o.value==value)end end
'''

class SpellWheelLeaseTests(unittest.TestCase):
 def setUp(self):
  self.lua=LuaRuntime(unpack_returned_tuples=True);self.lua.globals().source=SOURCE
  self.lua.execute(MOCK);self.lua.execute('load_module()')
 def test_acquire_idempotent_restore_only_flag(self):
  self.lua.execute('''assert(lease.acquire(pc));all(false);assert(lease.pending()and #writes==3)
   assert(lease.acquire(pc)and #writes==3);assert(lease.status().active)
   pc.live=false;assert(lease.shutdown());all(true);assert(not lease.pending()and #writes==6)
   for _,o in ipairs(modes)do assert(o.other==123)end
   assert(lease.shutdown()and #writes==6)''')
 def test_aliases_deduplicated_and_prior_false_preserved(self):
  self.lua.execute('''pc.MountedInputMode=modes[1];modes[2].value=false
   assert(lease.acquire(pc));assert(#writes==1 and lease.status().owned_count==1)
   assert(lease.shutdown());assert(modes[1].value and not modes[2].value and #writes==2)''')
 def test_shared_metatable_fallback_and_strict_available_metadata(self):
  self.lua.execute('fault.missing_accessor=true;assert(lease.acquire(pc));assert(lease.shutdown())')
  for setup in ("fault.object_kind='SoftObjectProperty'","fault.flag_kind='IntProperty'","fault.wrong_declared=true","fault.accessor_error=true","modes[3].class=controller","modes[3].value=1"):
   with self.subTest(setup=setup):
    self.setUp();self.lua.execute(setup+';load_module();local ok,why=lease.acquire(pc);assert(not ok and why and #writes==0 and not lease.pending())')
 def test_partial_acquisition_rolls_back_prior_writes(self):
  self.lua.execute('''modes[2].fail_before=false;local ok,why=lease.acquire(pc)
   assert(not ok and why);all(true);assert(not lease.pending())
   assert(#writes==3 and writes[1].value==false and writes[2].value==false and writes[3].value==true)''')
 def test_write_then_error_and_failed_cleanup_remain_retryable(self):
  self.lua.execute('''modes[2].fail_after=false;modes[2].fail_before=true
   local ok,why=lease.acquire(pc);assert(not ok and why and lease.pending())
   assert(modes[1].value and not modes[2].value and modes[3].value)
   modes[2].fail_before=nil;modes[2].fail_after=nil
   assert(lease.shutdown());all(true);assert(not lease.pending())''')
 def test_noop_restore_and_postwrite_error_never_consume_another_claim(self):
  self.lua.execute('''assert(lease.acquire(pc));modes[1].noop=true
   assert(not lease.shutdown()and lease.pending());assert(not modes[1].value and modes[2].value and modes[3].value)
   modes[1].noop=nil;modes[1].fail_after=true
   assert(not lease.shutdown()and lease.pending());assert(modes[1].value)
   local n=#writes;modes[1].fail_after=nil;assert(lease.shutdown()and #writes==n)''')
 def test_destroyed_or_replaced_object_never_mutated(self):
  self.lua.execute('''assert(lease.acquire(pc));modes[1].live=false
   local replacement=object(modes[2].path,modeClass);replacement.value=false
   local n=#writes;assert(lease.shutdown());assert(not replacement.value and #writes==n+1 and not lease.pending())''')
 def test_resolution_failure_retains_only_unresolved_cleanup(self):
  self.lua.execute('''assert(lease.acquire(pc));fault.lookup=modes[1].path
   assert(not lease.shutdown()and lease.pending());assert(not modes[1].value and modes[2].value and modes[3].value)
   fault.lookup=nil;assert(lease.shutdown());all(true)''')
 def test_lost_ownership_does_not_reassert_on_repeated_acquire(self):
  self.lua.execute('''assert(lease.acquire(pc));modes[1].value=true;local n=#writes
   assert(not lease.acquire(pc));assert(not lease.acquire(pc));assert(#writes==n and modes[1].value)
   assert(lease.status().ownership_lost);assert(lease.shutdown());all(true)''')
 def test_reload_requires_prior_cleanup_and_retries_same_old_module(self):
  self.lua.execute('''assert(lease.acquire(pc));local old=lease;modes[1].noop=true
   local ok=pcall(load_module);assert(not ok and _G.__DragonwildsSkateSpellWheelBlock==old and old.pending())
   modes[1].noop=nil;assert(pcall(load_module));all(true)
   assert(lease~=old and not old.pending()and _G.__DragonwildsSkateSpellWheelBlock==lease)
   assert(lease.acquire(pc));assert(lease.shutdown())''')
 def test_new_controller_releases_shared_modes_before_resnapshot(self):
  self.lua.execute('''assert(lease.acquire(pc));local other=object('/NewWorld/Controller',controller)
   other.GameplayInputMode=modes[1];other.GameplayLockOnTargetingInputMode=modes[2];other.MountedInputMode=modes[3]
   assert(lease.acquire(other));all(false);assert(#writes==9)
   assert(lease.shutdown());all(true);assert(#writes==12)''')

if __name__=='__main__':unittest.main()
