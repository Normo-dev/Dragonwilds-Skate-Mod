"""Reuse a verified previous startup snapshot, then require the fresh scene.

Pristine starts and native-verified accepted incremental histories are separate
cache formats. The native worker remains authoritative for geometry and rails.
"""
from pathlib import Path
import hashlib,json,math,os,time


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


class WarmWorldStore:
    def __init__(self,map_root,worker):
        self.root=Path(map_root).resolve();self.worker_sha256=digest(worker)
        self.folder=self.root/'warm-start-v1'

    def _identity(self,descriptor,cached):
        report=cached['report']
        if descriptor.get('mode')!='whole_world' or not descriptor.get('world_session'):
            raise ValueError('Warm startup requires a whole-world save identity')
        if report.get('world_session')!=descriptor['world_session'] or not report.get('world'):
            raise ValueError('Warm startup requires the verified world asset and save')
        base=Path(cached['world_manifest']).resolve()
        if not base.is_relative_to(self.root):raise ValueError('Warm base escapes the map cache')
        return {'world_session':descriptor['world_session'],'world':report['world'],
                'base_fingerprint':report['source_fingerprint'],'base_manifest':base.relative_to(self.root).as_posix(),
                'base_manifest_sha256':digest(base),'worker_sha256':self.worker_sha256}

    def _path(self,identity):
        key=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return self.folder/(key+'.json')

    def _overlay(self,path,identity):
        path=Path(path).resolve()
        if not path.is_relative_to((self.root/'prepared').resolve()):raise ValueError('Warm overlay escapes prepared data')
        value=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value,dict):raise ValueError('Warm overlay is not an object')
        completeness=value.get('completeness')
        if (value.get('magic')!='S3O1' or value.get('schema')!=1 or
                not isinstance(completeness,dict) or completeness.get('complete') is not True):
            raise ValueError('Warm overlay is not a complete S3O1 snapshot')
        if value.get('base_fingerprint')!=identity['base_fingerprint']:raise ValueError('Warm overlay has a different base')
        records=value['files']
        if len(records)!=2 or len({r['path'] for r in records})!=2:
            raise ValueError('Warm overlay must declare both exact payloads')
        if value['geometry_file'] not in records or value['instance_file'] not in records or value['geometry_file']==value['instance_file']:
            raise ValueError('Warm overlay payload references disagree')
        for record in records:
            payload=(path.parent/record['path']).resolve()
            if not payload.is_relative_to(path.parent):raise ValueError('Warm payload escapes overlay directory')
            if payload.stat().st_size!=record['bytes'] or digest(payload)!=record['sha256']:
                raise ValueError('Warm overlay payload checksum/length mismatch')
        inputs={'base':value['base_fingerprint'],'packages':value['packages'],'instance_packages':value['instance_packages'],
                'geometry':value['geometry'],'files':records,'default_shape_complexity':value['default_shape_complexity']}
        fingerprint=hashlib.sha256(json.dumps(inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if value['source_fingerprint']!=fingerprint:raise ValueError('Warm overlay content identity mismatch')
        return value

    def remember(self,descriptor,cached,response=None):
        """Call only after successful native init_world of this exact overlay."""
        checkpoint=None
        if isinstance(response,dict) and response.get('ok') is True:
            world=response.get('world')
            if isinstance(world,dict) and world.get('scene_fingerprint')==cached['report']['scene_fingerprint']:
                checkpoint=world.get('scene_checkpoint')
        return self._save(descriptor,cached,checkpoint)

    def _checkpoint(self,declaration,identity,scene):
        if not isinstance(declaration,dict):raise ValueError('Accepted checkpoint declaration is not an object')
        if (declaration.get('schema')!=1 or declaration.get('durable') is not True or
                declaration.get('format') not in ('accepted_incremental_v1','exposed_edges_v1') or
                declaration.get('scene_fingerprint')!=scene):
            raise ValueError('Accepted checkpoint declaration does not match the scene')
        if declaration['format']=='exposed_edges_v1':
            from skate_exposed_cache import manifest,hash_value,rail_cache_record,declared_grind_cache
            if ('graph_cache' in declaration and 'rail_cache' in declaration and
                    declaration['graph_cache']!=declaration['rail_cache']):
                raise ValueError('Accepted native grind aliases disagree')
            if (not isinstance(declaration.get('manifest_file'),str) or
                    not hash_value(declaration.get('manifest_sha256')) or
                    not hash_value(declaration.get('algorithm_sha256'))):
                raise ValueError('Exposed-edge checkpoint lacks exact cache identity')
            value=manifest(self.root,Path(declaration['manifest_file']),base=identity['base_fingerprint'],scene=scene,
                algorithm=declaration['algorithm_sha256'],expected_sha256=declaration['manifest_sha256'])
            rails=rail_cache_record(self.root,declared_grind_cache(declaration),value,native_path=True)
            return {'format':'exposed_edges_v1','path':value['path'],'sha256':value['sha256'],
                    'algorithm_sha256':value['algorithm_sha256'],'rail_cache':rails}
        path=Path(declaration['checkpoint_file']).resolve()
        if not path.is_relative_to((self.root/'prepared'/'accepted-scene-cache').resolve()):
            raise ValueError('Accepted checkpoint escapes its cache')
        value=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value,dict):raise ValueError('Accepted checkpoint is not an object')
        if (value.get('schema')!=1 or value.get('base_fingerprint')!=identity['base_fingerprint'] or
                value.get('scene_fingerprint')!=scene):
            raise ValueError('Accepted checkpoint identities disagree')
        return {'path':path.relative_to(self.root).as_posix(),'sha256':digest(path)}

    def promote(self,descriptor,cached,response):
        """Save only the exact installed revision with a durable native history."""
        if response.get('collision_revision')!=descriptor['revision']:return False
        world=response.get('world')
        checkpoint=world.get('scene_checkpoint') if isinstance(world,dict) else None
        if not checkpoint:return False
        return self._save(descriptor,cached,checkpoint)

    def _save(self,descriptor,cached,checkpoint=None):
        identity=self._identity(descriptor,cached);path=Path(cached['scene_overlay']).resolve()
        overlay=self._overlay(path,identity)
        if overlay['source_fingerprint']!=cached['report']['scene_fingerprint']:
            raise ValueError('Accepted startup report does not match its overlay')
        record={'schema':1,'identity':identity,'scene_overlay':path.relative_to(self.root).as_posix(),
                'scene_manifest_sha256':digest(path),'scene_fingerprint':overlay['source_fingerprint']}
        if checkpoint is not None:
            record['accepted_checkpoint']=self._checkpoint(checkpoint,identity,overlay['source_fingerprint'])
        self.folder.mkdir(parents=True,exist_ok=True);target=self._path(identity)
        temporary=target.with_suffix('.json.tmp');temporary.write_text(json.dumps(record,separators=(',',':')),encoding='utf-8')
        os.replace(temporary,target)
        # This is an optional ownership journal, never a gameplay readiness gate.
        # The checkpoint was validated above; a crash before catalog publication
        # leaves an unknown pointer/cache which conservative collection protects.
        try:
            from skate_cache_retention import record_accepted
            recorded=record_accepted(self.root,target,record)
            return recorded if record.get('accepted_checkpoint',{}).get('format')=='exposed_edges_v1' else True
        except Exception as error:
            print('GENERATED CACHE RECORD SKIPPED',str(error),flush=True)
            return record.get('accepted_checkpoint',{}).get('format')!='exposed_edges_v1'

    def select(self,descriptor,cached):
        """An invalid/missing optimization leaves the normal fresh path available."""
        try:
            identity=self._identity(descriptor,cached);pointer=self._path(identity)
            if not pointer.exists():return None
            saved=json.loads(pointer.read_text(encoding='utf-8'))
            if not isinstance(saved,dict):raise ValueError('Warm snapshot pointer is not an object')
            if saved.get('schema')!=1 or saved.get('identity')!=identity:raise ValueError('Warm snapshot identity mismatch')
            path=(self.root/saved['scene_overlay']).resolve()
            if not path.is_relative_to((self.root/'prepared').resolve()):raise ValueError('Warm overlay escapes prepared data')
            if digest(path)!=saved['scene_manifest_sha256']:raise ValueError('Warm snapshot manifest changed')
            overlay=self._overlay(path,identity)
            if overlay['source_fingerprint']!=saved['scene_fingerprint']:raise ValueError('Warm snapshot scene identity mismatch')
            checkpoint=saved.get('accepted_checkpoint')
            if checkpoint:
                if not isinstance(checkpoint,dict):raise ValueError('Warm snapshot checkpoint is not an object')
                marker=(self.root/checkpoint['path']).resolve()
                exposed=checkpoint.get('format')=='exposed_edges_v1'
                parent=self.root/'exposed-edges-v1' if exposed else self.root/'prepared'/'accepted-scene-cache'
                if not marker.is_relative_to(parent.resolve()):raise ValueError('Warm checkpoint escapes its cache')
                if digest(marker)!=checkpoint['sha256']:raise ValueError('Warm checkpoint history changed')
                declaration={'schema':1,'durable':True,'scene_fingerprint':saved['scene_fingerprint']}
                if exposed:
                    from skate_exposed_cache import contained,declared_grind_cache
                    rails=dict(declared_grind_cache(checkpoint))
                    rails['path']=str(contained(self.root,rails.get('path')))
                    declaration.update(format='exposed_edges_v1',manifest_file=str(marker),
                        manifest_sha256=checkpoint['sha256'],algorithm_sha256=checkpoint['algorithm_sha256'],
                        rail_cache=rails)
                else:
                    declaration.update(format='accepted_incremental_v1',checkpoint_file=str(marker))
                self._checkpoint(declaration,identity,saved['scene_fingerprint'])
            return path
        except (OSError,ValueError,KeyError,TypeError) as error:
            print('WARM WORLD CACHE IGNORED',str(error),flush=True);return None


def prewarm_world(request,warmup,store,manifest,rail_file,assets):
    """Load only a verified saved session while the host reads current objects.

    This never samples input, advances physics or declares gameplay ready. An
    exact current descriptor must still be accepted by initialize_world.
    """
    def vector(value):
        return (isinstance(value,list) and len(value)==3 and all(
            type(v) in (int,float) and math.isfinite(v) and abs(v)<1e9 for v in value))
    if not isinstance(warmup,dict) or warmup.get('mode')!='whole_world':return None
    if not all(isinstance(warmup.get(k),str) and warmup[k] for k in ('generation','world_session','world')):return None
    if not vector(warmup.get('center')) or not vector(warmup.get('anchor')):return None
    heading=warmup.get('heading')
    if type(heading) not in (int,float) or not math.isfinite(heading):return None
    manifest=Path(manifest).resolve()
    base=json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(base,dict):raise ValueError('Warm base manifest is not an object')
    cached={'world_manifest':str(manifest),'report':{'world_session':warmup['world_session'],
        'world':warmup['world'],'source_fingerprint':base['source_fingerprint']}}
    previous=store.select(warmup,cached)
    if previous is None:return None
    center=warmup['center'];anchor=warmup['anchor']
    command={'op':'init_world','manifest':str(manifest),'rail_file':str(Path(rail_file).resolve()),
        'assets':str(assets),'scene_file':str(previous),'scene_cache_required':True,'anchor':anchor,
        'spawn':[(center[0]-anchor[0])*.01,(center[2]-anchor[2])*.01+.25,-(center[1]-anchor[1])*.01],
        'heading':heading}
    result=request(command)
    print('WARM WORLD PRELOADED DURING OBJECT SCAN',warmup['generation'],flush=True)
    return {'generation':warmup['generation'],'identity':store._identity(warmup,cached),
        'scene_file':str(previous),'anchor':anchor,'assets':str(assets),'result':result}


def initialize_world(request,command,descriptor,cached,store,*,stopped=lambda:False,progress=lambda:None,preloaded=None,persist=True):
    """Return ready only after current collision; never step an old snapshot.

    Pose polling installs a queued collision without advancing source physics.
    Missing native caches fall back to initializing the current snapshot, so a
    stale optimization cannot trigger an extra bake of a superseded snapshot.
    """
    previous=store.select(descriptor,cached)
    if previous is not None:
        try:
            reusable=bool(preloaded and preloaded.get('generation')==descriptor['generation'] and
                preloaded.get('identity')==store._identity(descriptor,cached) and
                preloaded.get('scene_file')==str(previous) and preloaded.get('anchor')==command['anchor'] and
                preloaded.get('assets')==command['assets'])
            result=preloaded['result'] if reusable else request({**command,'scene_file':str(previous),'scene_cache_required':True})
        except RuntimeError as error:
            print('WARM WORLD NATIVE CACHE REJECTED',str(error),flush=True)
        else:
            print('WARM WORLD SNAPSHOT LOADED',str(previous),flush=True)
            request({'op':'world_scene','revision':descriptor['revision'],'scene_file':cached['scene_overlay']})
            while True:
                if stopped():raise RuntimeError('Stopped during current collision preparation')
                current=request({'op':'pose'})
                if current.get('collision_error_revision')==descriptor['revision']:
                    raise RuntimeError(current.get('collision_error','Current collision preparation failed'))
                if current.get('collision_revision')==descriptor['revision']:
                    print('WARM WORLD CURRENT SCENE ACCEPTED',descriptor['revision'],flush=True)
                    if persist:
                        try:store.promote(descriptor,cached,current)
                        except (OSError,ValueError,KeyError,TypeError) as error:
                            print('WARM WORLD CHECKPOINT NOT SAVED',str(error),flush=True)
                    # Readiness belongs to the newly accepted scene, not the
                    # snapshot that was preloaded while the host was scanning.
                    result={**result,**{key:current[key] for key in ('world','collision_scope','collision_revision',
                        'collision_pending_revision','camera_mode','summon') if key in current}}
                    result.pop('collision_error',None);result.pop('collision_error_revision',None)
                    return result
                progress();time.sleep(.03)
    result=request(command)
    if persist:
        try:store.remember(descriptor,cached,result)
        except (OSError,ValueError,KeyError,TypeError) as error:
            print('WARM WORLD CACHE NOT SAVED',str(error),flush=True)
    return result
