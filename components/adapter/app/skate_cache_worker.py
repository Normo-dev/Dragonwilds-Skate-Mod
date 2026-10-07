"""Offline collision preparation server. Never opens game objects or input."""
from pathlib import Path
import hashlib,json,re,subprocess,sys,time,traceback
import skate_map_cache as cache
import numpy as np
from skate_world_export import export_world,export_overlay,validate_export,authoritative_inputs,INSTANCE_DTYPE
from skate_observed_world import ObservedWorldState
from skate_overlay_reuse import OverlayReuseStore
from runtime_paths import load_paths,game_paks
from skate_authored_gate import live_included,validate_scene_policy

WORK=Path(__file__).resolve().parent
MAILBOX=load_paths(WORK)['mailbox']
BUILT=load_paths(WORK)['map_data']/'prepared'
BUILT.mkdir(parents=True,exist_ok=True)

def export_command(paths,arguments):
    exporter=Path(paths['map_export'])
    if exporter.suffix.lower()=='.exe':return [str(exporter),*map(str,arguments)]
    if exporter.suffix.lower()=='.dll':return [str(paths['dotnet']),str(exporter),*map(str,arguments)]
    raise ValueError('Map exporter must be a self-contained .exe or a .dll with a configured dotnet runtime')

def resolve_building_state(scene,mailbox):
    name=scene.get('building_state_file')
    if name is None:return scene
    if scene.get('building_state') is not None:raise ValueError('Conflicting embedded and file-backed building snapshots')
    if not isinstance(name,str)or re.fullmatch(r'building-state2(?:-[0-9]+-[0-9]+-[0-9]+)?\.json',name)is None:
        raise ValueError('Building state file must be a generated mailbox basename')
    ident=scene.get('building_capture_id')
    if not isinstance(ident,str)or re.fullmatch('[0-9]+-[0-9]+-[0-9]+',ident)is None:
        raise ValueError('Exact building capture identity is required')
    root=Path(mailbox).resolve();path=root/name
    if path.is_symlink()or path.resolve()!=path or(hasattr(path,'is_junction')and path.is_junction()):
        raise ValueError('Linked building report rejected')
    before=path.stat()
    if not 0<before.st_size<=33554432:raise ValueError('Building report exceeds its size bound')
    capture=json.loads(path.read_bytes());after=path.stat()
    if (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
        raise ValueError('Building report changed while reading')
    if not isinstance(capture,dict)or capture.get('capture_id')!=ident:raise ValueError('Queued building capture was replaced')
    return {**scene,'building_state':capture}

def missing_meshes(scene):
    result=set()
    for item in scene['objects']:
        if not live_included(item):continue
        if item.get('component_collision') is not None:continue
        path=cache.object_path(item['mesh'])
        ident=hashlib.sha256(path.encode()).hexdigest().upper()[:24]
        if not (cache.ROOT/'static/geometry'/f'{ident}.json').exists():result.add(path)
    return sorted(result)

def ensure_meshes(scene):
    missing=missing_meshes(scene)
    if not missing:return
    paths=load_paths(WORK)
    buildings={cache.object_path(row['mesh']) for row in scene['objects']
        if row.get('collision_source')=='verified_lightweight_building_entity'}
    groups=[('normal',[m for m in missing if m not in buildings]),('buildings',[m for m in missing if m in buildings])]
    for label,selected in groups:
        if not selected:continue
        request=BUILT/('missing-'+label+'-meshes.json');request.write_text(json.dumps(selected))
        chosen={**paths}
        if label=='buildings'and paths.get('building_export')is not None:chosen['map_export']=paths['building_export']
        with (BUILT/('missing-'+label+'-mesh-export.log')).open('w')as log:
            result=subprocess.run(export_command(chosen,[game_paks(WORK),cache.ROOT,
                cache.ROOT/'Mappings.usmap',request,'mesh-list']),stdout=log,stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,timeout=120)
        remaining=set(missing_meshes(scene))&set(selected)
        if result.returncode or remaining:raise RuntimeError('Building mesh collision export failed: '+str(sorted(remaining)[:3]))


class WholeWorldPreparer:
    """One immutable whole-world cache plus authoritative live package overlays."""
    def __init__(self,world,*,require_authored=True,building_gate=None):
        self.world=world;self.base=None;self.manifest=None;self.package_triangles=None
        self.require_authored=require_authored
        # Explicit verified supplement injection; release wiring must pin its
        # independent game/catalogue/rule identity before enabling this route.
        self.building_gate=building_gate
        self.observed=ObservedWorldState(BUILT.parent/'observed-worlds')
        self.overlays=OverlayReuseStore(BUILT)

    def load_base(self,scene):
        if self.require_authored and self.world.authored is None:
            raise ValueError('Whole-world collision requires a complete authored-policy.json export')
        default=cache.trace_policy(scene.get('physicsSettings',{}).get('engine_default'))
        inputs=authoritative_inputs(self.world,default)
        pointer=cache.ROOT/'compact-world-base.json'
        saved=json.loads(pointer.read_text()) if pointer.exists() else None
        if saved and saved.get('schema')==1 and saved.get('inputs')==inputs:
            manifest=(cache.ROOT/saved['manifest']).resolve()
            if not manifest.is_relative_to(cache.ROOT.resolve()):raise ValueError('Pinned world manifest escapes map cache')
            base=validate_export(manifest)
            if base['source_fingerprint']!=saved['source_fingerprint']:raise ValueError('Pinned world fingerprint mismatch')
        else:
            identity=hashlib.sha256(json.dumps(inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:20]
            folder=cache.ROOT/'compact-worlds'/identity
            base=export_world(folder,cache=self.world,physics_settings=scene.get('physicsSettings'))
            manifest=folder/'manifest.json'
            temporary=pointer.with_suffix('.json.tmp')
            temporary.write_text(json.dumps({'schema':1,'inputs':inputs,
                'manifest':str(manifest.relative_to(cache.ROOT)),
                'source_fingerprint':base['source_fingerprint']},separators=(',',':')))
            temporary.replace(pointer)
        # Current MapCache mesh IDs may differ after new live meshes were added.
        # Counts must use the pinned base's own geometry and instance table.
        rows=np.memmap(manifest.parent/base['instance_file']['path'],dtype=INSTANCE_DTYPE,mode='r') if base['instance_count'] else np.empty(0,dtype=INSTANCE_DTYPE)
        counts=np.asarray([m['count'] for m in base['geometry']],dtype=np.int64)
        totals=np.zeros(len(base['packages']),dtype=np.int64)
        np.add.at(totals,rows['package'],counts[rows['geometry']])
        self.package_triangles=totals;self.base=base;self.manifest=manifest

    def prepare(self,request,scene):
        started=time.perf_counter()
        scene=resolve_building_state(scene,MAILBOX)
        if self.world.authored:validate_scene_policy(scene,self.world.authored.policy)
        if self.base is None:
            self.load_base(scene)
        paths=load_paths(WORK)
        if self.building_gate is None and paths.get('building_rule') is not None:
            from skate_preflight import check_package
            from skate_building_setup import verify_building_supplement
            from skate_building_scene import BuildingSceneGate
            manifest=check_package(Path(paths['root']).parent)
            pins=verify_building_supplement(paths,manifest,self.base['source_fingerprint'])
            self.building_gate=BuildingSceneGate(**pins,policy=self.world.authored.policy,packet_root=paths['mailbox'])
        if self.building_gate is not None and self.building_gate.packet_root is not None:
            from skate_building_cleanup import cleanup_reports
            cleanup_reports(self.building_gate.packet_root)
        building_record=None;building_report=None
        if self.building_gate is not None:
            scene,building_record=self.building_gate.prepare(request,scene,self.base['source_fingerprint'],observed=self.observed)
        # Promotion must precede geometry discovery and authoritative package
        # merging so newly placed building meshes use the same strict exporter.
        ensure_meshes(scene)
        scene,pending=self.observed.preview(scene,request['world_session'],self.base['source_fingerprint'])
        if self.building_gate is not None:
            pending,building_report=self.building_gate.validate_observed(request,scene,pending,building_record)
        identity=self.overlays.identity(self.base,self.world,scene,request['world_session'])
        reused=self.overlays.select(identity,self.base)
        if reused is not None:
            overlay_path,overlay=reused
        else:
            folder=BUILT/(request['generation']+'-'+str(request['revision'])+'.overlay')
            overlay=export_overlay(folder,base=self.base,cache=self.world,scene=scene)
            overlay_path=folder/'manifest.json'
            try:self.overlays.remember(identity,overlay_path,self.base)
            except (OSError,ValueError,KeyError,TypeError) as error:
                print('OVERLAY REUSE NOT SAVED',str(error),flush=True)
        observed={cache.package_path(p) for p in overlay['packages']}
        package_ids=[i for i,p in enumerate(self.base['packages']) if p in observed]
        removed=int(self.package_triangles[package_ids].sum())
        active=self.base['terrain_triangle_count']+self.base['static_triangle_count']-removed+overlay['triangle_count']
        report={'mode':'whole_world','complete':True,'coverage':'all_cached_world','total':active,
                'base_total':self.base['terrain_triangle_count']+self.base['static_triangle_count'],
                'terrain_triangles':self.base['terrain_triangle_count'],'object_triangles':self.base['static_triangle_count']-removed+overlay['triangle_count'],
                'removed_offline_triangles':removed,'live_triangles':overlay['triangle_count'],'live_instances':overlay['instance_count'],
                'overridden_packages':len(observed),'missing_geometry_count':0,'missing_geometry':[],'unsupported_shapes':0,
                'unresolved_empty_geometry_instances':0,'scene_errors':[],'empty_geometry':[],
                'default_shape_complexity':self.base['default_shape_complexity'],'source_fingerprint':self.base['source_fingerprint'],
                'scene_fingerprint':overlay['source_fingerprint'],'base_binary_bytes':self.base['binary_bytes'],
                'overlay_binary_bytes':overlay['binary_bytes'],'build_seconds':time.perf_counter()-started,
                'reused_scene_overlay':reused is not None,
                'anchor':request['anchor'],'center':request['center']}
        report['world_session']=request['world_session'];report['world']=scene['world']
        report['changed_observed_packages']=scene['changed_observed_packages']
        if building_report is not None:report['building_collision']=building_report
        self.observed.commit(pending)
        if self.building_gate is not None and self.building_gate.packet_root is not None:
            try:self.building_gate.publish_baseline(pending[2],self.building_gate.packet_root)
            except (OSError,ValueError,KeyError,TypeError)as error:
                print('BUILDING BASELINE NOT SAVED',str(error),flush=True)
        return {'world_manifest':str(self.manifest.resolve()),'scene_overlay':str(overlay_path.resolve()),'report':report}

def run():
    # MapCache progress is diagnostic output; stdout is the JSON response stream.
    protocol=sys.stdout
    with __import__('contextlib').redirect_stdout(sys.stderr):
        world=cache.MapCache()
        whole_world=WholeWorldPreparer(world)
    for line in sys.stdin:
        started=time.perf_counter()
        report_path=None;request=None
        try:
            from skate_collision_codec import decode
            request=decode(b'S3C2'+line.encode())
            path=BUILT/(request['generation']+'-'+str(request['revision'])+'.triangles')
            if request['mode']=='whole_world':report_path=path.with_suffix('.report.json')
            scene=json.loads((MAILBOX/request['scene_file']).read_text())
            if scene.get('generation')!=request['generation'] or scene.get('revision')!=request['scene_revision']:
                raise RuntimeError('Live scene generation/revision does not match the request')
            if scene.get('errors'):raise RuntimeError('Live scene inventory contains errors; keeping previous collision')
            prepared={}
            with __import__('contextlib').redirect_stdout(sys.stderr):
                if request['mode']=='whole_world':
                    prepared=whole_world.prepare(request,scene);report=prepared.pop('report')
                else:
                    ensure_meshes(scene)
                    report=world.build(request['anchor'],path,radius=request['radius_cm'],scene=scene,center=request['center'])
            report_path=path.with_suffix('.report.json')
            temporary=report_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps({'descriptor':request,'report':report},indent=2),encoding='utf-8')
            temporary.replace(report_path)
            if not report.get('complete',False):
                details={k:report.get(k) for k in
                    ['missing_geometry_count','unsupported_shapes','unresolved_empty_geometry_instances','scene_errors']}
                details['unresolved_meshes']=[m['mesh'] for m in report.get('empty_geometry',[])
                    if not m.get('ignored_for_simple_collision')][:8]
                raise RuntimeError('Collision coverage is incomplete: '+json.dumps(details))
            result={'ok':True,**prepared,'report':report,'descriptor':request,
                    'report_file':str(report_path),'elapsed_ms':(time.perf_counter()-started)*1000}
            if request['mode']!='whole_world':result['collision_file']=str(path)
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            result={'ok':False,'error':str(error)}
            if request is not None and request.get('mode')=='whole_world' and report_path is not None:
                temporary=report_path.with_suffix('.json.tmp')
                temporary.write_text(json.dumps({'descriptor':request,'report':{'mode':'whole_world','complete':False,'error':str(error)}},indent=2),encoding='utf-8')
                temporary.replace(report_path)
            if report_path is not None and report_path.exists():result['report_file']=str(report_path)
        protocol.write(json.dumps(result,separators=(',',':'))+'\n');protocol.flush()

if __name__=='__main__':run()
