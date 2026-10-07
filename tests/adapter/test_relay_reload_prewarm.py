"""Run the real relay loop with isolated IPC/processes; never access the game."""
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from contextlib import ExitStack, redirect_stdout
import io, json, sys, tempfile, unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skate_relay
import skate_warm_world
import skate_cache_background


class ReloadPrewarmTests(unittest.TestCase):
    def scenario(self, fail_old=False, collision_timing=None):
        events=[]; statuses=[]; loop=[0]; time_now=[0.]; accepted_pointer=[None]
        old_queued=[False]; reload_seen=[False]; fresh_submitted=[False]; done=[False]
        children=[]; collect_frames=[0]; building_submitted=[False]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, ExitStack() as stack:
            root=Path(tmp); box=root/'mailbox';box.mkdir()
            (root/'skate_host_render.py').write_text('# mock')
            (box/'host-rig.json').write_text('{}')
            manifest=root/'world.json';manifest.write_text(json.dumps({'source_fingerprint':'base'}))
            rails=root/'world.lips';rails.write_bytes(b'fixture')
            def descriptor(generation, revision):
                return dict(mode='whole_world',generation=generation,revision=revision,
                    scene_file=f'scene-{generation}-{revision}.json',world_session='same-save',
                    center=[0,0,0],anchor=[0,0,0],heading=0.)
            def cached(value):
                return dict(ok=True,world_manifest=str(manifest),elapsed_ms=1,
                    scene_overlay=str(root/f"{value['generation']}-{value['revision']}.json"),
                    report=dict(total=10,source_fingerprint='base',scene_fingerprint=str(value['revision']),
                        world_session='same-save',world='World /Game/World'))
            class Channel:
                sequence=SimpleNamespace(value=2)
                payload=b'S3C2'+json.dumps(descriptor('old',3)).encode()
                def read(self):return self.payload
                def submit(self,value):
                    self.sequence.value+=2;self.payload=b'S3C2'+json.dumps(value).encode()
            channel=Channel()
            transport=SimpleNamespace(collision=channel,close=lambda:None)
            class Future:
                def __init__(self,fn,args,ready):self.fn=fn;self.args=args;self.ready=ready;self.complete=False
                def done(self):return self.complete
                def result(self):
                    if not self.complete:raise AssertionError('Future used before completion')
                    return self.value
            class Executor:
                def __init__(self,*args,**kwargs):self.queue=[]
                def submit(self,fn,*args):
                    # Delay checkpoint persistence to cover the gap between
                    # native acceptance and the durable warm pointer update.
                    future=Future(fn,args,loop[0]+3 if fn.__name__=='save_legacy_accepted' else loop[0])
                    self.queue.append(future);self.pump();return future
                def pump(self):
                    while self.queue and self.queue[0].ready<=loop[0]:
                        future=self.queue.pop(0);future.value=future.fn(*future.args);future.complete=True
                def shutdown(self,**kwargs):self.pump()
            executor=Executor()
            class Store:
                def __init__(self,*args):pass
                def _identity(self,*args):return {'save':'same-save'}
                def select(self,*args):return accepted_pointer[0]
                def remember(self,desc,data,response=None):
                    accepted_pointer[0]=Path(data['scene_overlay']);events.append(('remember',desc['revision']))
                    return True
                def promote(self,desc,data,response):
                    assert response['collision_revision']==desc['revision']
                    accepted_pointer[0]=Path(data['scene_overlay']);events.append(('promote',desc['generation'],desc['revision']))
                    return True
            class Process:
                def __init__(self,is_native):
                    self.native=is_native;self.stdin=self;self.stdout=self
                    self.command=None;self.closed=False;self.revision=0;self.pending=None;self.polls=0;self.error=None
                    self.scene='3';self.pending_scene=None;self.leased=False;self.ticks=0
                def write(self,line):self.command=json.loads(line);return len(line)
                def flush(self):pass
                def readline(self):
                    command=self.command
                    if not self.native:return json.dumps(cached(command))+'\n'
                    op=command['op'];events.append((op,self.pending,command.get('scene_file')))
                    if self.leased and op in ('init_world','world_scene'):
                        raise AssertionError('Collision cache write dispatched during retirement lease')
                    if op=='init_world':
                        # Match native behavior: reset invalidates the handle,
                        # even though a detached builder may still save files.
                        self.pending=None;self.revision=0;self.error=None
                        self.scene=Path(command['scene_file']).stem.rsplit('-',1)[-1]
                    elif op=='world_scene':
                        self.pending=command['revision'];self.polls=0;self.error=None
                        self.pending_scene=Path(command['scene_file']).stem.rsplit('-',1)[-1]
                        if self.pending==4:old_queued[0]=True
                    elif op in ('pose','tick') and self.pending is not None:
                        self.polls+=1
                        if self.polls>=3:
                            if fail_old and self.pending==4:self.error=4
                            else:self.revision=self.pending;self.scene=self.pending_scene
                            events.append(('accepted' if self.error is None else 'rejected',self.pending))
                            self.pending=None
                    elif op=='shutdown':self.closed=True
                    elif op=='maintenance_begin':self.leased=True
                    elif op=='maintenance_end':
                        if not children[-1].closed:raise AssertionError('Lease released before collector stopped')
                        self.leased=False
                    if op=='tick':self.ticks+=command['steps']
                    result=dict(ok=True,period=1/60,collision_revision=self.revision,
                                collision_pending_revision=self.pending,tick=self.ticks,root=[0,0,0],
                                state='Skate',velocity=[0,0,0],camera={},world={
                                    'scene_fingerprint':self.scene,
                                    'scene_checkpoint':{'format':'exposed_edges_v1'},
                                    'exposed_edges':{'ready':True}})
                    if op.startswith('maintenance_'):
                        result.update(status={'maintenance_status':'maintenance_status',
                            'maintenance_begin':'maintenance_acquired','maintenance_end':'maintenance_released'}[op],
                            maintenance=dict(available=True,worker_id='worker',quiescent=self.pending is None,
                                lease_active=self.leased,retirement_allowed=self.leased),lease_token='token')
                    if self.error is not None:result.update(collision_error_revision=self.error,collision_error='fixture failure')
                    return json.dumps(result)+'\n'
                def poll(self):return 0 if self.closed else None
                def wait(self,**kwargs):self.closed=True;return 0
                def close(self):self.closed=True
            native=Process(True);cache_process=Process(False)
            class BackgroundChild:
                def __init__(self,script,payload,log):
                    self.payload=payload;self.closed=False;self.started=loop[0]
                    self.collect=payload['op']=='collect';children.append(self)
                    events.append(('background_start','collect' if self.collect else 'checkpoint',loop[0]))
                def poll(self):
                    return None if self.collect or loop[0]<self.started+3 else 0
                def result(self):
                    if self.collect:raise AssertionError('Collector should be cancelled, not finish')
                    desc=self.payload['descriptor'];data=self.payload['cached']
                    accepted_pointer[0]=Path(data['scene_overlay'])
                    events.append(('remember',desc['revision']) if self.payload['op']=='remember' else
                        ('promote','old' if Path(data['scene_overlay']).stem.startswith('old-') else 'new',desc['revision']))
                    return {'ok':True,'retained':True}
                def close(self):
                    self.closed=True;events.append(('background_close','collect' if self.collect else 'checkpoint',loop[0]))
            real_background=skate_cache_background.BackgroundCache
            def background(paths,request,log):
                return real_background(paths,request,log,spawn=BackgroundChild,clock=lambda:time_now[0],managed=True)
            def popen(args,**kwargs):return cache_process if len(args)>1 else native
            def publish(name,value):
                assert name=='status.json';statuses.append(dict(value))
                if value.get('status')=='ready' and (value.get('generation')=='new' or
                        (collision_timing and value.get('collision_revision')==4)):
                    done[0]=True;(box/'relay-stop.request').write_text('fixture complete')
            def host():
                loop[0]+=1
                if loop[0]>50:raise AssertionError('New generation never became ready')
                if collision_timing:time_now[0]+=.1
                executor.pump()
                if collision_timing:
                    # Exercise an update queued during checkpoint validation,
                    # or a fresh update after live cleanup has already started.
                    if native.leased:collect_frames[0]+=1
                    checkpoint=next((c for c in children if not c.collect),None)
                    trigger=(collision_timing=='queued' and checkpoint is not None and loop[0]>=checkpoint.started+1 or
                        collision_timing=='collecting' and collect_frames[0]>=2)
                    if trigger and not building_submitted[0]:
                        channel.submit(descriptor('old',4));building_submitted[0]=True
                        events.append(('building_observed',loop[0]))
                elif not old_queued[0]:channel.submit(descriptor('old',4))
                else:reload_seen[0]=True
                generation='new' if reload_seen[0] else 'old'
                # The next host scan arrives only after the prewarm attempt.
                if reload_seen[0] and any(e[0]=='init_world' and e[2] for e in events[events.index(('remember',3))+1:]):
                    if not fresh_submitted[0]:channel.submit(descriptor('new',1));fresh_submitted[0]=True
                return dict(seq=loop[0],generation=generation,epoch=1,active=bool(collision_timing),paused=False,
                    spawn=[0,0,0],heading=0.,
                    world_warmup={**descriptor(generation,1),'world':'World /Game/World'})
            def sleep(_):time_now[0]+=.1
            fake_ipc=ModuleType('skate_ipc');fake_ipc.Transport=lambda:transport
            fake_render=ModuleType('skate_host_render');fake_render.HostRender=lambda *args:SimpleNamespace(pose_flat=lambda result:([],[],[]))
            inputs=SimpleNamespace(sample=lambda host,enabled:dict(controller_name='none',camera_mode='high',
                allowed=bool(collision_timing),controller_connected=False,controls={}),close=lambda:None)
            replacements=dict(WORK=root,MAILBOX=box,ASSETS=root/'assets',create_input=lambda _:inputs,
                SkaterMesh=lambda:None,load_paths=lambda *args:dict(worker=root/'worker.exe',map_data=root),
                read_host=host,publish=publish,publish_frame=lambda packet,serial:events.append(('frame',packet['tick'])),
                ThreadPoolExecutor=lambda **kwargs:executor)
            for name,value in replacements.items():stack.enter_context(patch.object(skate_relay,name,value))
            stack.enter_context(patch.dict(sys.modules,{'skate_ipc':fake_ipc,'skate_host_render':fake_render}))
            stack.enter_context(patch('importlib.reload',lambda module:module))
            stack.enter_context(patch.object(skate_relay.subprocess,'Popen',popen))
            stack.enter_context(patch.object(skate_warm_world,'WarmWorldStore',Store))
            stack.enter_context(patch.object(skate_cache_background,'BackgroundCache',background))
            stack.enter_context(patch.object(skate_relay.time,'sleep',sleep))
            stack.enter_context(patch.object(skate_relay.time,'monotonic',lambda:time_now[0]))
            stack.enter_context(patch.dict(skate_relay.os.environ,{'S3_PREPARED_WORLD_MANIFEST':str(manifest),
                'S3_PREPARED_RAIL_FILE':str(rails),'S3_MANAGED_RELAY':'1'}))
            with redirect_stdout(io.StringIO()):skate_relay.run()
        if collision_timing:
            self.assertTrue(done[0]);self.assertTrue(building_submitted[0])
            update=next(i for i,e in enumerate(events) if e[0]=='world_scene')
            self.assertTrue(any(e[0]=='tick' for e in events[:update]),events)
            self.assertTrue(any(e[0]=='frame' for e in events[update:]),events)
            self.assertIn(('accepted',4),events)
            if collision_timing=='queued':
                self.assertFalse(any(e[0]=='maintenance_begin' for e in events[:update]),events)
                checkpoint_start=next(i for i,e in enumerate(events) if e[:2]==('background_start','checkpoint'))
                self.assertTrue(any(e[0]=='tick' for e in events[checkpoint_start:events.index(('remember',3))]),events)
            else:
                begin=next(i for i,e in enumerate(events) if e[0]=='maintenance_begin')
                close=next(i for i,e in enumerate(events) if e[:2]==('background_close','collect'))
                end=next(i for i,e in enumerate(events) if e[0]=='maintenance_end')
                self.assertTrue(any(e[0]=='tick' for e in events[begin:close]),events)
                self.assertLess(begin,close);self.assertLess(close,end);self.assertLess(end,update)
            return events
        self.assertTrue(done[0]);self.assertTrue(reload_seen[0])
        self.assertTrue(all(e[1] is None for e in events if e[0]=='init_world'),events)
        self.assertFalse(any(e[0] in ('tick','activate','summon','surface') for e in events))
        # Late old-generation status keeps its generation, so the new adapter
        # cannot confuse completion of the old world with fresh readiness.
        fresh_ready=[s for s in statuses if s.get('status')=='ready' and s.get('generation')=='new']
        self.assertTrue(fresh_ready);self.assertEqual(fresh_ready[0]['collision_revision'],1)
        self.assertFalse(any(s.get('generation')=='new' and s.get('collision_revision')==4 for s in statuses))
        new_init_index=[i for i,e in enumerate(events) if e[0]=='init_world'][1]
        new_init=events[new_init_index]
        if fail_old:
            self.assertIn(('rejected',4),events)
            self.assertFalse(any(e[:3]==('promote','old',4) for e in events))
            self.assertLess(events.index(('rejected',4)),new_init_index)
        else:
            promotion=('promote','old',4)
            self.assertIn(promotion,events)
            self.assertLess(events.index(('accepted',4)),events.index(promotion))
            self.assertLess(events.index(promotion),new_init_index)
            self.assertTrue(new_init[2].endswith('old-4.json'))
        return events

    def test_reload_defers_prewarm_until_pending_scene_and_checkpoint_finish(self):self.scenario()

    def test_rejected_old_scene_releases_deferred_prewarm_without_promotion(self):self.scenario(fail_old=True)

    def test_queued_building_update_precedes_collection_while_ticks_continue(self):self.scenario(collision_timing='queued')

    def test_new_building_update_stops_collector_before_native_write_while_ticks_continue(self):self.scenario(collision_timing='collecting')


if __name__=='__main__':unittest.main()
