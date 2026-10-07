"""Private stdio test of the imported mount graph. Never reads live input/IPC."""
from pathlib import Path
import argparse,hashlib,json,math,queue,subprocess,threading,time
W=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('--worker',type=Path,required=True);p.add_argument('--sha256',required=True)
p.add_argument('--assets',type=Path,required=True);p.add_argument('--manifest',type=Path)
p.add_argument('--world-pad',type=float,nargs=3,help='Verified clear floor point in source metres; required with --manifest')
p.add_argument('--scene',type=Path);p.add_argument('--heading',type=float,default=0)
p.add_argument('--init-timeout',type=float,default=900)
p.add_argument('--output',type=Path,required=True);args=p.parse_args()
EXE=args.worker.resolve();SHA=args.sha256.lower();OUT=args.output.resolve();OUT.mkdir(exist_ok=True)
assert hashlib.sha256(EXE.read_bytes()).hexdigest()==SHA
q=queue.Queue();log=(OUT/'native.log').open('w')
p=subprocess.Popen([str(EXE)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,bufsize=1,
    creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
print(json.dumps({'private_native_pid':p.pid,'worker':str(EXE),'started':time.time()}),flush=True)
def read():
    for line in p.stdout:q.put(line)
    q.put(None)
threading.Thread(target=read,daemon=True).start()
def req(op,**kw):
    assert op!='poll'
    p.stdin.write(json.dumps(dict(op=op,**kw),allow_nan=False)+'\n');p.stdin.flush()
    v=q.get(timeout=args.init_timeout if op=='init_world' else 180);assert v is not None,'worker stopped';v=json.loads(v)
    assert v.get('ok'),v
    if 'root' in v:
        assert all(math.isfinite(x) for x in v['root'])
        assert all(math.isfinite(x) for b in v['bones'] for x in b)
    return v
frames=[];changes=[];runs=[]
def memory():
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in
            ['peak_working_set','working_set','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile']]
    c=Counters();c.cb=ctypes.sizeof(c)
    fn=ctypes.WinDLL('psapi').GetProcessMemoryInfo;fn.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD];fn.restype=wintypes.BOOL
    if not fn(wintypes.HANDLE(int(p._handle)),ctypes.byref(c),c.cb):raise ctypes.WinError()
    return {name:getattr(c,name) for name in ['peak_working_set','working_set','pagefile','peak_pagefile']}
def enter(spawn,heading,held_y=False):
    frames=[];changes=[];poses=[]
    value=req('summon',spawn=spawn,heading=heading)
    assert value['summon']['phase']=='waiting_ground',value
    initial_tick=value['tick']
    for i in range(1250):
        previous=value['tick'];value=req('tick',controls={'buttons':0x8000} if held_y else {},steps=1)
        assert value['tick']==previous+1
        state={k:value.get(k) for k in ['tick','state','summon']};state['position']=value['root'][12:15]
        frames.append(state)
        identity=(value.get('state'),value['summon']['phase'],value['summon']['motion_state'],value['summon']['holding_board'],value['summon']['supported'],value['summon']['locomotion'])
        if not changes or identity!=changes[-1]['identity']:
            changes.append(dict(frame=i,identity=identity,position=state['position']));print(changes[-1],flush=True)
        if 'Mount.Ground.IntoMount.FromStand' in value['summon']['motion_state']:poses.append(value['bones'])
        if value['summon']['phase'] in ['riding','failed']:break
    run={'initial_tick':initial_tick,'held_y':held_y,'frames':frames,'changes':changes,'last_state':value['state']}
    runs.append(run);(OUT/'results.json').write_text(json.dumps({'sha256':SHA,'runs':runs},indent=2))
    assert value['summon']['phase']=='riding',value['summon']
    assert len(poses)>10,'Original mount must expose successive animation frames'
    assert value['state']=='PhysicsGround',value['state']
    assert any(abs(x-y)>.05 for bone_a,bone_b in zip(poses[0],poses[-1]) for x,y in zip(bone_a,bone_b))
    assert all(math.isfinite(x) for x in value['camera']['position'])
    assert math.dist(value['camera']['position'],value['root'][12:15])<100
    for _ in range(60):
        value=req('tick',controls={'buttons':0x8000} if held_y else {},steps=1)
        assert value['summon']['phase']=='riding'
        assert value['state']!='BipedGround','Held summon input caused another toggle'
    if held_y:
        # A release must re-arm the original control, not disable it forever.
        value=req('tick',controls={},steps=1)
        value=req('tick',controls={'buttons':0x8000},steps=1)
        for _ in range(90):value=req('tick',controls={},steps=1)
        assert value['state']=='BipedGround','Released Y no longer reaches the source dismount'
        run['release_then_press_dismount_state']=value['state']
    return value
try:
    init_started=time.perf_counter()
    a=[-100,0,-100];b=[-100,0,100];c=[100,0,100];d=[100,0,-100]
    if args.manifest:
        # The caller supplies a known broad, hole-free pad. Its floor height is
        # confirmed by the source query rather than assumed from a visual mesh.
        assert args.world_pad,'--manifest requires --world-pad X Y Z'
        spawn=args.world_pad[:]
        world_args={'scene_file':str(args.scene.resolve())} if args.scene else {}
        init=req('init_world',assets=str(args.assets.resolve()),manifest=str(args.manifest.resolve()),
            rail_file=str(args.manifest.resolve().parent/'world.lips'),anchor=[0,0,0],spawn=spawn,heading=args.heading,**world_args)
        surface=req('surface',point=spawn,above=.1,below=.5)['surface'];assert surface,'No source floor close to supplied feet position'
        assert abs(surface['position'][1]-spawn[1])<=.5,'Source floor differs from captured feet'
        spawn[1]=surface['position'][1]+.25
        second=spawn[:];recovery=spawn[:]
    else:
        spawn=[0,.25,0];second=[10,.25,10];recovery=[-10,.25,0]
        init=req('init',assets=str(args.assets.resolve()),triangles=[[a,b,c],[a,c,d]],rails=[],spawn=spawn,heading=0)
    init_seconds=time.perf_counter()-init_started;init_memory=memory()
    value=enter(spawn,args.heading)
    value=enter(second,args.heading+math.pi,held_y=True)
    value=req('summon',spawn=spawn,heading=0)
    value=req('activate',spawn=recovery,heading=0)
    assert value['summon']['phase']=='idle'
    for _ in range(60):value=req('tick',controls={},steps=1)
    assert value['state']=='PhysicsGround' and value['summon']['phase']=='idle'
    (OUT/'results.json').write_text(json.dumps({'sha256':SHA,'passed':True,'world':init.get('world'),'scene':str(args.scene) if args.scene else None,
        'world_pad':args.world_pad,'heading':args.heading,'source_surface':surface if args.manifest else None,
        'init_seconds':init_seconds,'init_memory_bytes':init_memory,'final_memory_bytes':memory(),
        'runs':runs,'recovery':{'state':value['state'],'phase':value['summon']['phase'],'tick':value['tick']}},indent=2))
    print('PASS two original throwdowns, finite animated poses, continuous ticks, and recovery cancellation',flush=True)
finally:
    try:req('shutdown')
    except Exception:pass
    p.stdin.close()
    try:p.wait(timeout=10)
    except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=10)
    log.close()
