"""Real relay/cache/Session integration, isolated from game IPC and all input.

The tiny authored synthetic fixture explicitly bypasses the production authored
asset-policy gate, whose independent tests cover that contract. No real input
backend is constructed, no Poll command is sent, and all writes are private.
"""
from pathlib import Path
import ctypes,hashlib,json,math,os,shutil,struct,subprocess,sys,tempfile,time

WORK=Path(__file__).resolve().parent
sys.path.insert(0,str(WORK))
ROOT=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else None
BINARY=WORK/'skate-build/debug/deps/dragonwilds_skate_worker-licensed17-public.exe'
EXPECTED='266d61fb2e46cad32c28ccc1b3410d37d032565e89755b27b995d4a9b02e2f31'
FLOOR='/Game/TestBase.TestBase'
BUILDING='/Game/TestBuilding.TestBuilding'
MESH='/Game/Test/Floor.Floor'
MESH_ID=hashlib.sha256(MESH.encode()).hexdigest().upper()[:24]
IDENTITY={'Translation':{'X':0,'Y':0,'Z':0},'Rotation':{'X':0,'Y':0,'Z':0,'W':1},'Scale3D':{'X':1,'Y':1,'Z':1}}

def ipc(root):
    import skate_ipc
    dll=ctypes.CDLL(str(WORK/'skate-ipc-build-stream/release/skate_ipc.dll'))
    dll.ipc_use_test_namespace.restype=ctypes.c_int
    assert dll.ipc_use_test_namespace()==1
    original=skate_ipc.Transport
    prefix='Local\\DragonwildsSkate.test.v3.'
    skate_ipc.Transport=lambda:original(prefix=prefix)
    return skate_ipc.Transport

def child_relay():
    ipc(ROOT)
    import skate_relay
    from test_input_fixture import NoInput
    class ScriptedInput(NoInput):
        def sample(self,host,active):
            value=super().sample(host,active)
            value['focused']=host.get('test_focus',True)
            value['allowed']=bool(active and value['focused'] and host.get('input_allowed',True))
            value['controls']['buttons']=host.get('test_buttons',0) if value['allowed'] else 0
            return value
    skate_relay.WORK=ROOT;skate_relay.MAILBOX=ROOT/'skate-mailbox'
    skate_relay.create_input=lambda _root:ScriptedInput()
    skate_relay.run()

def child_cache():
    import skate_cache_worker as worker
    import skate_map_cache as cache
    worker.WORK=ROOT;worker.MAILBOX=ROOT/'skate-mailbox';worker.BUILT=ROOT/'map/prepared'
    worker.BUILT.mkdir(parents=True,exist_ok=True)
    cache.ROOT=ROOT/'map'
    original=cache.MapCache
    cache.MapCache=lambda:original(root=ROOT/'map')
    preparer=worker.WholeWorldPreparer
    class SyntheticWorld(preparer):
        def __init__(self,world):super().__init__(world,require_authored=False)
        def prepare(self,request,scene):
            result=super().prepare(request,scene)
            if scene.get('test_native_error'):
                Path(result['scene_overlay']).write_text('{"magic":"S3O1"}')
            return result
    worker.WholeWorldPreparer=SyntheticWorld
    worker.run()

def setup():
    assert hashlib.sha256(BINARY.read_bytes()).hexdigest()==EXPECTED
    root=Path(tempfile.mkdtemp(prefix='wholeworld-relay-check-',dir=WORK))
    mailbox=root/'skate-mailbox';mailbox.mkdir()
    data=root/'map';geometry=data/'static/geometry';geometry.mkdir(parents=True)
    shutil.copy2(WORK/'skate-mailbox/host-rig.json',mailbox/'host-rig.json')
    shutil.copy2(WORK/'skate_host_render.py',root/'skate_host_render.py')
    (root/'runtime.json').write_text(json.dumps({'schema':1,'paths':{'worker':str(BINARY),'map_data':str(data),'assets':str(WORK/'skate3-converted/assets')}}))
    (root/'skate_cache_worker.py').write_text('import runpy,sys\nsys.path.insert(0,'+repr(str(WORK))+')\nsys.argv=['+repr(str(__file__))+',"--cache",'+repr(str(root))+']\nrunpy.run_path('+repr(str(__file__))+',run_name="__main__")\n')
    (geometry/(MESH_ID+'.json')).write_text(json.dumps({'path':MESH,'trace':'CTF_UseDefault','agg':{'BoxElems':[
        {'X':60000,'Y':60000,'Z':100,'Center':{'X':0,'Y':0,'Z':-50},'Rotation':{'Pitch':0,'Yaw':0,'Roll':0}}
    ]}}))
    (data/'static/fixture.instances.jsonl').write_text(json.dumps({'name':'StaticMeshComponent '+FLOOR+':PersistentLevel.Floor.Mesh','id':MESH_ID,'transform':IDENTITY})+'\n')
    return root

def main():
    root=setup();box=root/'skate-mailbox';channel=ipc(root)()
    channel.frame.write(b'');channel.collision.write(b'');channel.status.write(b'{}')
    host={'generation':'worldtest','epoch':1,'active':False,'paused':False,'seq':0,'spawn':[0,.25,0],'heading':0,'test_camera':'high',
          'entry_floor':{'point':[0,0,0],'normal':[0,1,0]}}
    results=[];out=(root/'relay.log').open('w',encoding='utf-8')
    child=subprocess.Popen([sys.executable,str(__file__),'--relay',str(root)],stdout=out,stderr=out,creationflags=subprocess.CREATE_NO_WINDOW)
    def heartbeat():
        host['seq']+=1;channel.host.write(json.dumps(host).encode())
    def status():
        assert child.poll() is None,(root/'relay.log').read_text(encoding='utf-8')[-3000:]
        return json.loads(channel.status.read() or b'{}')
    def frame():
        raw=channel.frame.read()
        if not raw or len(raw)<33:return None
        magic,serial,epoch,tick,ng,ns,nb,nd,connected=struct.unpack_from('<4sQIQHHHHB',raw)
        assert magic==b'S3D2' and not connected
        if raw[33:33+ng]!=host['generation'].encode():return None
        assert nb==84*10 and nd==8*10,('Incomplete host body or board',nb,nd)
        values=struct.unpack_from('<'+'d'*(42+nb+nd),raw,33+ng+ns)
        assert len(raw)==33+ng+ns+8*len(values) and all(map(math.isfinite,values))
        for transforms in [values[32:32+nb],values[32+nb:32+nb+nd]]:
            for offset in range(0,len(transforms),10):
                t=transforms[offset:offset+10]
                assert abs(sum(v*v for v in t[3:7])-1)<.001 and min(t[7:10])>0
        return {'serial':serial,'epoch':epoch,'tick':tick,'state':raw[33+ng:33+ng+ns].decode(),
                'body_transforms':nb//10,'board_transforms':nd//10,'raw':raw,
                'body_pose':values[32:32+nb],'board_pose':values[32+nb:32+nb+nd]}
    def wait(predicate,timeout=60):
        started=time.monotonic()
        while time.monotonic()-started<timeout:
            heartbeat();s=status()
            if predicate(s):return s
            time.sleep(.02)
        raise AssertionError('Timeout '+json.dumps(status())+'\n'+(root/'relay.log').read_text(encoding='utf-8')[-2000:])
    def record(name,**values):results.append({'test':name,**values});print(json.dumps(results[-1]),flush=True)
    def submit(revision,*,bad=False,empty=False):
        name=f'scene-{host["generation"]}-{revision}.json'
        # A second package exercises overlay addition/removal independently of
        # the immutable authored floor and outside any center/radius decisions.
        transform=json.loads(json.dumps(IDENTITY));transform['Translation']['X']=50000
        scene={'schema':1,'world':'World /Game/TestBase.TestBase','generation':host['generation'],'revision':revision,
            'objects':[] if empty else [{'name':'StaticMeshComponent '+BUILDING+':PersistentLevel.Building.Mesh',
                'mesh':'StaticMesh '+MESH,'enabled':3,'response_pawn':2,'owner_collision':True,'transform':transform}],
            'packages':[BUILDING],'errors':[],'test_native_error':bad,'physicsSettings':{'engine_default':1}}
        (box/name).write_text(json.dumps(scene))
        descriptor={'mode':'whole_world','world_session':'synthetic-save','generation':host['generation'],'revision':revision,
            'scene_file':name,'anchor':[0,0,0],'center':[0,0,0],'heading':0}
        channel.collision.write(b'S3C2'+json.dumps(descriptor).encode())
    try:
        wait(lambda s:s.get('status')=='waiting_for_collision')
        submit(1,empty=True)
        s=wait(lambda s:s.get('status')=='ready' and s.get('collision_revision')==1)
        assert s['collision_scope']=='whole_world' and 'coverage_radius_cm' not in s
        record('whole_world_cache_and_session_initialized',triangles=s['triangle_count'])
        host.update(active=True,paused=True,entry_mode='summon',test_buttons=0x8000,test_focus=False)
        wait(lambda s:(s.get('entry') or {}).get('wait_reason')=='game_paused')
        paused_heartbeat=status().get('heartbeat',0)
        wait(lambda s:s.get('heartbeat',0)>=paused_heartbeat+2)
        assert frame() is None
        record('paused_entry_never_initializes_source')
        host.update(paused=False,input_allowed=False)
        wait(lambda s:(s.get('entry') or {}).get('wait_reason')=='host_input_blocked')
        blocked_heartbeat=status().get('heartbeat',0)
        wait(lambda s:s.get('heartbeat',0)>=blocked_heartbeat+2)
        assert frame() is None
        record('host_blocked_entry_never_initializes_source')
        assert 'SOURCE ENTRY FLOOR' not in (root/'relay.log').read_text(encoding='utf-8')
        host.update(input_allowed=True)
        first_seen=time.monotonic()
        wait(lambda s:frame() is not None and frame()['epoch']==1)
        first_frame=frame();first_tick=first_frame['tick']
        wait(lambda s:(s.get('entry') or {}).get('phase')=='pose_ready')
        wait_heartbeat=status().get('heartbeat',0)
        wait(lambda s:s.get('heartbeat',0)>=wait_heartbeat+3)
        assert frame()['tick']==first_tick and frame()['serial']==first_frame['serial']
        assert status()['suspended'] is True
        entry=status()['entry'];assert entry['game_focused'] is False and entry['input_enabled'] is False
        assert entry['floor']['matched'] and entry['floor']['native_revision']==0
        assert entry['native_ms']<1000
        record('unfocused_mount_publishes_neutral_first_pose_and_freezes',tick=first_tick,entry=entry)
        host.update(test_focus=True)
        wait(lambda s:frame()['tick']>first_tick)
        assert frame()['tick']-first_tick<=6
        record('focus_resume_has_no_elapsed_time_catchup',first_resumed_tick=frame()['tick'])

        captured=[];mount_states=set()
        def mounting(s):
            f=frame()
            if f and (not captured or captured[-1]['serial']!=f['serial']):captured.append(f)
            mount=s.get('summon') or {}
            if mount.get('motion_state'):mount_states.add(mount['motion_state'])
            return bool(captured and mount.get('phase')=='riding' and f['tick']>captured[0]['tick']+100)
        wait(mounting)
        assert len(captured)>15
        assert any(f['state']=='BipedGround' for f in captured)
        assert any('Mount.Ground.IntoMount.FromStand' in name for name in mount_states),mount_states
        assert captured[-1]['state']=='PhysicsGround'
        assert all(b['tick']>a['tick'] for a,b in zip(captured,captured[1:]))
        assert any(abs(x-y)>.005 for x,y in zip(captured[0]['body_pose'],captured[-1]['body_pose']))
        assert any(abs(x-y)>.005 for x,y in zip(captured[0]['board_pose'],captured[-1]['board_pose']))
        record('original_summon_streams_retargeted_throwdown_with_held_y',frames=len(captured),
               ticks=[captured[0]['tick'],captured[-1]['tick']],motion_states=sorted(mount_states),
               body_transforms=84,board_transforms=8)
        host['test_buttons']=0;released=frame()['tick']
        wait(lambda s:frame()['tick']>released+4)
        host['test_buttons']=0x8000;pressed=frame()['tick']
        wait(lambda s:frame()['tick']>pressed+4)
        host['test_buttons']=0
        wait(lambda s:frame()['state']=='BipedGround')
        record('physical_release_rearms_original_dismount',state=frame()['state'])
        host.update(epoch=host['epoch']+1,entry_mode='activate')
        wait(lambda s:frame()['epoch']==host['epoch'] and frame()['state']=='PhysicsGround')
        wait(lambda s:(s.get('summon') or {}).get('phase')=='idle')
        first=frame()['tick'];wait(lambda s:frame()['tick']>first+10)
        record('neutral_source_frames_advance',tick=frame()['tick'])
        host['paused']=True
        wait(lambda s:s.get('suspended') is True)
        paused=frame()['tick'];paused_heartbeat=status().get('heartbeat',0)
        wait(lambda s:s.get('suspended') is True and s.get('heartbeat',0)>=paused_heartbeat+2)
        assert frame()['tick']==paused
        host['paused']=False;host['test_camera']='low'
        wait(lambda s:s.get('camera_mode')=='low' and not s.get('suspended') and frame()['tick']>paused)
        record('menu_pause_and_camera_resume',epoch=frame()['epoch'])
        host['test_camera']='dragonwilds'
        wait(lambda s:s.get('camera_mode')=='dragonwilds' and frame()['tick']>paused)
        host['test_camera']='high'
        wait(lambda s:s.get('camera_mode')=='high')
        record('three_camera_modes_keep_native_high_low_protocol')
        original=(box/'host-rig.json').read_bytes();before=frame()['tick']
        topology=(box/'board-topology.json').read_bytes()
        parts=json.loads(topology)['parts'];assert len(parts)==8
        textures={part['texture'] for part in parts};assert len(textures)==3
        assert all(Path(texture).is_file() and Path(texture).read_bytes().startswith(b'\x89PNG\r\n\x1a\n') for texture in textures)
        (box/'host-rig.json').write_text('{')
        wait(lambda s:frame()['tick']>before+15)
        assert (box/'board-topology.json').read_bytes()==topology
        retained=frame()
        (box/'host-rig.json').write_bytes(original)
        wait(lambda s:(root/'relay.log').read_text(encoding='utf-8').count('Host renderer ready:')>=2)
        assert (box/'board-topology.json').read_bytes()==topology
        record('partial_character_reload_keeps_complete_renderer',tick=retained['tick'],
               body_transforms=retained['body_transforms'],board_transforms=retained['board_transforms'],textures=len(textures))
        submit(2)
        added=wait(lambda s:s.get('status')=='ready' and s.get('collision_revision')==2)
        assert added['triangle_count']==24
        before=frame()['tick'];submit(3,bad=True)
        wait(lambda s:s.get('status')=='error' and s.get('failed_revision')==3)
        wait(lambda s:frame()['tick']>before+8)
        record('failed_scene_update_keeps_skating',tick=frame()['tick'])
        submit(4,empty=True)
        s=wait(lambda s:s.get('status')=='ready' and s.get('collision_revision')==4)
        assert s['triangle_count']==12
        record('later_scene_deletion_recovers',triangles=s['triangle_count'])
        host.update(epoch=3,spawn=[500,.25,0],entry_floor={'point':[500,0,0],'normal':[0,1,0]})
        wait(lambda s:(s.get('entry') or {}).get('phase')=='waiting_collision' and
             (s.get('entry') or {}).get('wait_reason')=='entry_floor_mismatch')
        assert frame()['epoch']!=3
        checks=(root/'relay.log').read_text(encoding='utf-8').count('SOURCE ENTRY FLOOR')
        blocked_heartbeat=status().get('heartbeat',0)
        wait(lambda s:s.get('heartbeat',0)>=blocked_heartbeat+3)
        assert (root/'relay.log').read_text(encoding='utf-8').count('SOURCE ENTRY FLOOR')==checks
        assert frame()['epoch']!=3
        record('missing_floor_blocks_first_pose_without_repeated_surface_queries',floor_queries=checks)
        submit(5)
        wait(lambda s:s.get('collision_revision')==5 and (s.get('entry') or {}).get('phase')=='pose_ready' and frame()['epoch']==3)
        assert status()['entry']['floor']['matched'] and status()['entry']['floor']['native_revision']==5
        assert (root/'relay.log').read_text(encoding='utf-8').count('SOURCE ENTRY FLOOR')==checks+1
        record('same_epoch_retries_after_corrected_accepted_scene',entry=status()['entry'])
        prior=frame();host.update(epoch=4,active=True,input_allowed=False)
        wait(lambda s:(s.get('entry') or {}).get('epoch')==4 and (s.get('entry') or {}).get('wait_reason')=='host_input_blocked')
        host.update(active=False,input_allowed=True)
        cancel_heartbeat=status().get('heartbeat',0)
        wait(lambda s:s.get('heartbeat',0)>=cancel_heartbeat+2 and s.get('entry') is None)
        assert frame()['epoch']!=4
        record('canceled_blocked_epoch_never_dispatches_later')
        host.update(active=True,generation='unprepared-generation',epoch=5)
        stale_heartbeat=status().get('heartbeat',0)
        wait(lambda s:s.get('heartbeat',0)>=stale_heartbeat+2)
        assert channel.frame.read()==prior['raw']
        record('unprepared_generation_never_dispatches_source')
        host.update(active=False,generation='worldtest-reload',epoch=6,spawn=[0,.25,0])
        # Old test hosts without the optional proof remain compatible.
        del host['entry_floor']

        submit(1,empty=True)
        s=wait(lambda s:s.get('status')=='ready' and s.get('generation')=='worldtest-reload' and s.get('collision_revision')==1)
        native=(box.parent/'logs/skate-relay-native.log').read_text(encoding='utf-8')
        assert 'WORLD_SCENE_LIP_CACHE required hit' in native
        assert 'WARM WORLD CURRENT SCENE ACCEPTED 1' in (root/'relay.log').read_text(encoding='utf-8')
        host.update(active=True)
        wait(lambda s:frame() is not None and frame()['epoch']==6 and frame()['tick']>10)
        record('f10_generation_reloads_from_verified_cache_then_accepts_current',generation=s['generation'],tick=frame()['tick'])
        (box/'relay-stop.request').write_text('stop isolated verification')
        child.wait(timeout=30);assert child.returncode==0
        native=(box.parent/'logs/skate-relay-native.log').read_text(encoding='utf-8')
        assert native.count('IW4L_SKATE_LOAD begin')==2
        record('one_session_per_generation_and_warm_current_gate',loads=2)
        (root/'results.json').write_text(json.dumps({'binary_sha256':EXPECTED,'results':results},indent=2))
        print('RESULTS '+str(root/'results.json'),flush=True)
    finally:
        if child.poll() is None:
            (box/'relay-stop.request').write_text('stop isolated verification')
            try:child.wait(timeout=30)
            except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=10)
        out.close();channel.close()

if __name__=='__main__':
    if '--relay' in sys.argv:child_relay()
    elif '--cache' in sys.argv:child_cache()
    else:main()
