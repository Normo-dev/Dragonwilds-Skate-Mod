"""Real relay + native Session with deterministic collision-boundary injection.

Only collision response tokens are injected, allowing request-boundary races to
be reproduced without relying on a background thread winning by chance. Native
surface queries/activation still run; no real game IPC/input is accessed.
"""
from pathlib import Path
import json,struct,subprocess,sys,time
import test_relay_entry_focus as fixture


def child_relay():
    import skate_relay
    original=skate_relay.subprocess.Popen
    root=fixture.ROOT
    class NativeBoundary:
        def __init__(self,process):
            self.process=process;self.stdin=self.Input(self);self.stdout=self.Output(self)
            self.command=None;self.synthetic=None;self.case=None;self.raced=False;self.pending=None;self.revision=0
        def __getattr__(self,name):return getattr(self.process,name)
        def settings(self):
            try:return json.loads((root/'protocol.json').read_text())
            except (OSError,ValueError):return {}
        class Input:
            def __init__(self,owner):self.owner=owner
            def __getattr__(self,name):return getattr(self.owner.process.stdin,name)
            def write(self,line):
                owner=self.owner;owner.command=json.loads(line);settings=owner.settings()
                if settings.get('case')!=owner.case:
                    owner.case=settings.get('case');owner.raced=False
                if settings.get('hold_scene') and owner.command['op']=='world_scene':
                    owner.pending=owner.command['revision']
                    owner.synthetic={'ok':True,'collision_revision':owner.revision,'collision_pending_revision':owner.pending}
                    return len(line)
                return owner.process.stdin.write(line)
        class Output:
            def __init__(self,owner):self.owner=owner
            def __getattr__(self,name):return getattr(self.owner.process.stdout,name)
            def readline(self):
                owner=self.owner;settings=owner.settings();op=owner.command['op']
                if owner.synthetic is not None:
                    response=owner.synthetic;owner.synthetic=None
                else:
                    response=json.loads(owner.process.stdout.readline())
                    owner.revision=response.get('collision_revision',owner.revision)
                if settings.get('race') and op in ('summon','activate'):owner.raced=True
                if settings.get('race') and owner.raced:
                    response['collision_revision']=owner.revision+1000
                    if settings['race']=='missing' and op=='surface':response['surface']=None
                if owner.pending is not None:response['collision_pending_revision']=owner.pending
                with (root/'protocol-requests.jsonl').open('a') as log:
                    log.write(json.dumps({'case':settings.get('case'),'op':op,'tick':response.get('tick'),
                        'revision':response.get('collision_revision'),'pending':response.get('collision_pending_revision')})+'\n')
                return json.dumps(response)+'\n'
    def popen(command,*args,**kwargs):
        process=original(command,*args,**kwargs)
        return NativeBoundary(process) if Path(command[0]).resolve()==fixture.BINARY.resolve() else process
    skate_relay.subprocess.Popen=popen
    fixture.child_relay()


def main():
    root=fixture.setup();box=root/'skate-mailbox';channel=fixture.ipc(root)()
    channel.frame.write(b'');channel.collision.write(b'');channel.status.write(b'{}')
    host={'generation':'floor-protocol','epoch':1,'active':False,'paused':False,'seq':0,
          'spawn':[0,.25,0],'heading':0,'entry_mode':'summon','test_focus':False,
          'entry_floor':{'point':[0,0,0],'normal':[0,1,0]}}
    out=(root/'relay.log').open('w');results=[]
    child=subprocess.Popen([sys.executable,str(__file__),'--relay',str(root)],stdout=out,stderr=out,creationflags=subprocess.CREATE_NO_WINDOW)
    def frame():
        raw=channel.frame.read()
        if not raw:return None
        magic,serial,epoch,tick,*_=struct.unpack_from('<4sQIQHHHHB',raw)
        assert magic==b'S3D2'
        return {'epoch':epoch,'serial':serial,'tick':tick}
    def status():
        assert child.poll() is None,(root/'relay.log').read_text()[-3000:]
        return json.loads(channel.status.read() or b'{}')
    def wait(predicate,timeout=30):
        start=time.monotonic()
        while time.monotonic()-start<timeout:
            host['seq']+=1;channel.host.write(json.dumps(host).encode())
            state=status()
            if predicate(state):return state
            time.sleep(.02)
        raise AssertionError(json.dumps(status())+'\n'+(root/'relay.log').read_text()[-3000:])
    def control(**values):(root/'protocol.json').write_text(json.dumps(values))
    def requests(case):
        return [row for line in (root/'protocol-requests.jsonl').read_text().splitlines()
                if (row:=json.loads(line)).get('case')==case]
    def record(name,**values):
        results.append({'test':name,**values});print(json.dumps(results[-1]),flush=True)
    def submit(revision):
        name=f'scene-{revision}.json'
        (box/name).write_text(json.dumps({'schema':1,'world':'World /Game/TestBase.TestBase',
            'generation':host['generation'],'revision':revision,'objects':[],'packages':[],
            'errors':[],'physicsSettings':{'engine_default':1}}))
        channel.collision.write(b'S3C2'+json.dumps({'mode':'whole_world','world_session':'synthetic-save',
            'generation':host['generation'],'revision':revision,'scene_file':name,
            'anchor':[0,0,0],'center':[0,0,0],'heading':0}).encode())
    try:
        wait(lambda s:s.get('status')=='waiting_for_collision');submit(1)
        wait(lambda s:s.get('status')=='ready')
        control(case='race-missing',race='missing');host['active']=True
        wait(lambda s:(s.get('entry') or {}).get('phase')=='waiting_collision')
        assert frame() is None
        heartbeat=status().get('heartbeat',0)
        wait(lambda s:s.get('heartbeat',0)>=heartbeat+3)
        rows=requests('race-missing')
        assert sum(r['op']=='summon' for r in rows)==1
        assert sum(r['op']=='surface' for r in rows)==2
        assert all(r['op']!='tick' for r in rows)
        record('scene_swap_removing_support_discards_first_pose_without_repeat_summon',requests=rows)
        control(case='race-matching',race='matching');host['epoch']=2
        state=wait(lambda s:(s.get('entry') or {}).get('phase')=='pose_ready' and frame()['epoch']==2)
        rows=requests('race-matching')
        assert sum(r['op']=='summon' for r in rows)==1 and sum(r['op']=='surface' for r in rows)==2
        assert state['entry']['floor']['native_revision']==1000
        record('scene_swap_preserving_support_rechecks_before_first_pose',requests=rows)
        host['active']=False;wait(lambda s:s.get('entry') is None)
        control(case='pending-current',hold_scene=True);submit(2)
        wait(lambda s:'CACHED COLLISION QUEUED 2' in (root/'relay.log').read_text())
        host.update(active=True,epoch=3)
        state=wait(lambda s:(s.get('entry') or {}).get('phase')=='pose_ready' and frame()['epoch']==3)
        rows=requests('pending-current')
        assert state['collision_revision']==1
        assert all(r['pending']==2 for r in rows if r['op'] in ('surface','summon'))
        assert sum(r['op']=='summon' for r in rows)==1 and sum(r['op']=='surface' for r in rows)==1
        assert all(r['op']!='tick' for r in rows)
        record('accepted_support_mounts_while_new_scene_remains_pending',requests=rows)
        (box/'relay-stop.request').write_text('stop isolated protocol check')
        child.wait(timeout=30);assert child.returncode==0
        (root/'protocol-results.json').write_text(json.dumps(results,indent=2))
        print('RESULTS '+str(root/'protocol-results.json'),flush=True)
    finally:
        if child.poll() is None:
            (box/'relay-stop.request').write_text('stop isolated protocol check')
            try:child.wait(timeout=30)
            except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=10)
        out.close();channel.close()


if __name__=='__main__':
    if '--relay' in sys.argv:child_relay()
    else:main()
