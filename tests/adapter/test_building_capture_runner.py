"""Read-only runner publication uses actual Lua, a fake capture and temp files."""
from pathlib import Path
import json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).parent/'python-deps'))
from lupa import LuaRuntime
ROOT=Path(__file__).parent
class TestRunner(unittest.TestCase):
 def test_rejected_capture_returns_underlying_guard_for_bridge_log(self):
  with tempfile.TemporaryDirectory(prefix='building-runner-rejected-',dir=ROOT)as folder:
   p=Path(folder);lua=LuaRuntime(unpack_returned_tuples=True)
   lua.execute('package.preload.skate_config=function()return {mailbox='+json.dumps(p.as_posix()+'/')+'}end')
   lua.execute('package.preload.json=function()'+(ROOT/'host-probe/json.lua').read_text()+'\nend')
   lua.execute('''package.preload.building_state=function()return {begin=function()return true end,cancel=function()end,
    advance=function()return {schema='S3BUILDINGSTATE2',complete=false,collision_verified=false,
     errors={'Native building ID inventory changed'},capture_failure_phase='verify',pieces={}}end}end''')
   lua.execute('runner=(function()'+(ROOT/'host-probe/building_capture_runner.lua').read_text()+'\nend)()')
   lua.execute('''assert(runner.begin({},{state2=true,packets=false,unique_report=true}))
    for i=1,100 do local r=runner.tick();if r then result=r;break end end
    assert(not result.complete and not result.collision_verified and result.observed_serial==nil)
    assert(result.error=='Native building ID inventory changed'and result.capture_failure_phase=='verify')''')
   report=json.loads(Path(lua.globals().result.path).read_bytes())
   self.assertEqual(report['errors'],['Native building ID inventory changed'])
 def test_unique_report_larger_row_ceiling_keeps_clock_budget(self):
  with tempfile.TemporaryDirectory(prefix='building-runner-budget-',dir=ROOT)as folder:
   p=Path(folder);lua=LuaRuntime(unpack_returned_tuples=True)
   lua.execute('package.preload.skate_config=function()return {mailbox='+json.dumps(p.as_posix()+'/')+'}end')
   lua.execute('package.preload.json=function()'+(ROOT/'host-probe/json.lua').read_text()+'\nend')
   lua.execute('''ticks=0;step=0.000001
    package.preload.shared_transport=function()return {clock=function()ticks=ticks+step;return ticks end}end
    package.preload.building_state=function()return {begin=function()return true end,cancel=function()end,
     advance=function()local ids={};for i=1,1000 do ids[i]=i end
      return {schema='S3BUILDINGSTATE2',complete=true,collision_verified=true,errors={},pieces={},native_piece_ids=ids}end}end''')
   lua.execute('runner=(function()'+(ROOT/'host-probe/building_capture_runner.lua').read_text()+'\nend)()')
   lua.execute('''assert(runner.begin({},{state2=true,packets=false,unique_report=true}));runner.tick();runner.tick()
    local count=runner.status().rows_written;assert(count>32 and count<=256)
    step=0.0002;runner.tick();local delta=runner.status().rows_written-count;assert(delta>=1 and delta<=5)
    runner.cancel();assert(not runner.busy())''')
   self.assertEqual(list(p.iterdir()),[])
 def test_independent_raw_reports_ordering_and_offthread_cleanup(self):
  with tempfile.TemporaryDirectory(prefix='building-runner-raw-',dir=ROOT)as folder:
   p=Path(folder);lua=LuaRuntime(unpack_returned_tuples=True)
   lua.execute('package.preload.skate_config=function()return {mailbox='+json.dumps(p.as_posix()+'/')+'}end')
   lua.execute('package.preload.json=function()'+(ROOT/'host-probe/json.lua').read_text()+'\nend')
   lua.execute('''package.preload.building_state=function()
    local function create()local options;return {begin=function(_,o)options=o;return true end,cancel=function()end,
      advance=function()options.packet_sink('DWBBDY2'..string.rep('x',24))
       return {schema='S3BUILDINGSTATE2',complete=true,collision_verified=true,errors={},world='World Fixture',world_session='fixture',pieces={{id=1}},inventory={},inventory_piece_ids={}}end}end
    local d=create();d.new=create;return d end''')
   lua.execute('runner=(function()'+(ROOT/'host-probe/building_capture_runner.lua').read_text()+'\nend)()')
   lua.execute('''audit=runner.new();assert(audit.begin({},{state2=true,packets=true,unique_report=true}))
    assert(runner.begin({},{state2=true,packets=true,unique_report=true}));audit.tick();runner.tick()
    for i=1,100 do local r=runner.tick();if r then result=r;break end end
    for i=1,100 do local r=audit.tick();if r then old=r;break end end
    assert(result.observed_serial>old.observed_serial and result.path~=old.path and result.world_session=='fixture')
    assert(runner.discard(result));assert(not runner.discard(result))''')
   result=lua.globals().result;old=lua.globals().old
   data=json.loads(Path(result.path).read_bytes());self.assertEqual(data['pieces'],[])
   packet=Path(result.packet_path).read_bytes();self.assertEqual(packet[:6],b'S3BPK1');self.assertEqual(len(packet),41)
   self.assertEqual(data['piece_packets']['piece_count'],1);self.assertTrue(Path(result.path).exists())
   sys.path.insert(0,str(ROOT));from skate_building_cleanup import cleanup_reports
   self.assertEqual(cleanup_reports(p),1);self.assertFalse(Path(result.path).exists());self.assertFalse(Path(result.packet_path).exists())
   self.assertTrue(Path(old.path).exists());self.assertTrue(Path(old.packet_path).exists())
 def test_state2_selects_verified_capture_and_distinct_report(self):
  with tempfile.TemporaryDirectory(prefix='building-runner2-',dir=ROOT)as folder:
   p=Path(folder);lua=LuaRuntime(unpack_returned_tuples=True)
   lua.execute('package.preload.skate_config=function()return {building_state_probe=true,mailbox='+json.dumps(p.as_posix()+'/')+'}end')
   lua.execute('package.preload.json=function()'+(ROOT/'host-probe/json.lua').read_text()+'\nend')
   lua.execute('''package.preload.building_collision=function()error('STATE1 must not load')end
    package.preload.building_state=function()return {begin=function()return true end,cancel=function()end,
     advance=function()return {schema='S3BUILDINGSTATE2',complete=true,collision_verified=true,errors={},
      pieces={{id=1}},inventory={{id=1,data_index=7},{id=2,data_index=8}},inventory_piece_ids={1,2},native_missing_piece_ids={2}}end}end''')
   lua.execute('runner=(function()'+(ROOT/'host-probe/building_capture_runner.lua').read_text()+'\nend)()')
   lua.execute('assert(runner.begin({}));for i=1,100 do local r=runner.tick();if r then result=r;break end end;assert(result.complete and result.collision_verified and result.native_piece_count==1)')
   data=json.loads((p/'building-state2.json').read_text());self.assertEqual(data['inventory_piece_ids'],[1,2])
   self.assertEqual(data['native_missing_piece_ids'],[2]);self.assertEqual(data['capture_runner']['capture_ticks'],1)
   self.assertFalse((p/'building-native-state.json').exists())
 def test_bounded_atomic_report_and_cancel(self):
  with tempfile.TemporaryDirectory(prefix='building-runner-',dir=ROOT)as folder:
   p=Path(folder);lua=LuaRuntime(unpack_returned_tuples=True)
   lua.execute('package.preload.skate_config=function()return {mailbox='+json.dumps(p.as_posix()+'/')+'}end')
   lua.execute('package.preload.json=function()'+(ROOT/'host-probe/json.lua').read_text()+'\nend')
   lua.execute('''ticks=0;stopped=0;reads=0
    package.preload.shared_transport=function()return {clock=function()ticks=ticks+0.00001;return ticks end}end
    package.preload.building_collision=function()return {
     begin=function(_,o)assert(o.nativeReader);reads=0;return true end,
     cancel=function()stopped=stopped+1 end,
     advance=function(n,b)assert(n==256 and b==0.001);reads=reads+1;if reads==1 then return nil end
      local rows={};for i=1,100 do rows[i]={id=i}end
      return {schema='S3BUILDINGSTATE1',complete=true,collision_verified=false,errors={},native_pieces=rows,pieces={{id=1}},native_capture={complete=true}}end}end''')
   lua.execute('runner=(function()'+(ROOT/'host-probe/building_capture_runner.lua').read_text()+'\nend)()')
   lua.execute('assert(runner.begin({}));assert(runner.tick()==nil);assert(runner.tick()==nil);assert(runner.busy())')
   self.assertFalse((p/'building-native-state.json').exists())
   self.assertTrue((p/'building-native-state.json.tmp').exists())
   lua.execute('assert(runner.tick()==nil);assert(runner.status().rows_written<=32)')
   lua.execute('for i=1,50 do local r=runner.tick();if r then result=r;break end end;assert(result.complete and not result.collision_verified and result.native_piece_count==100)')
   j=json.loads((p/'building-native-state.json').read_text());self.assertEqual(len(j['native_pieces']),100)
   self.assertFalse((p/'building-native-state.json.tmp').exists())
   saved=(p/'building-native-state.json').read_bytes()
   lua.execute('assert(runner.begin({}));runner.tick();runner.tick();runner.cancel();assert(not runner.busy() and stopped==1)')
   self.assertFalse((p/'building-native-state.json.tmp').exists());self.assertEqual(saved,(p/'building-native-state.json').read_bytes())
if __name__=='__main__':unittest.main()
