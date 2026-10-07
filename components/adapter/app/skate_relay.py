"""Local transport and CPU skinning for the existing Skate 3 simulation."""
from pathlib import Path
import io, json, math, os, struct, subprocess, time, sys
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image
from runtime_paths import load_paths

WORK = Path(__file__).resolve().parent
MAILBOX = load_paths(WORK)['mailbox']
ASSETS = load_paths(WORK)['assets']
IPC=None

def create_input(root):
    from skate_input import InputRouter
    return InputRouter(root)

def check_entry_floor(value, request):
    """Check host support against the accepted source scene without ticking it.

    Returned native revision is deliberately not the host descriptor revision:
    a newly initialized native world is revision zero even if the host's first
    accepted descriptor is revision one. The native token detects a scene swap
    between this query and the subsequent neutral activation request.
    """
    def vector(v):
        if not isinstance(v,(list,tuple)) or len(v)!=3:return None
        if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in v):return None
        return [float(x) for x in v]
    def normal(v):
        v=vector(v)
        if v is None or v[1]<=0:return None
        length=math.hypot(*v)
        if not math.isfinite(length) or length<1e-8:return None
        return [x/length for x in v]
    detail={'matched':False}
    point=vector(value.get('point')) if isinstance(value,dict) else None
    expected=normal(value.get('normal')) if isinstance(value,dict) else None
    if point is None or max(map(abs,point))>=1e7 or expected is None:
        detail['reason']='invalid_host_floor'
        return False,detail,None
    started=time.perf_counter()
    try:result=request({'op':'surface','point':point,'above':.1,'below':.1})
    except Exception as error:
        detail.update(reason='surface_query_failed',error=str(error),query_ms=round((time.perf_counter()-started)*1000,3))
        return False,detail,None
    detail['query_ms']=round((time.perf_counter()-started)*1000,3)
    if not isinstance(result,dict):
        detail['reason']='invalid_surface_response'
        return False,detail,None
    revision=result.get('collision_revision')
    if type(revision) is not int or revision<0:
        detail['reason']='invalid_surface_revision'
        return False,detail,result
    detail['native_revision']=revision
    surface=result.get('surface')
    position=vector(surface.get('position')) if isinstance(surface,dict) else None
    actual=normal(surface.get('normal')) if isinstance(surface,dict) else None
    if position is None or actual is None:
        detail['reason']='missing_surface'
        return False,detail,result
    distance=math.dist(point,position)
    dot=sum(a*b for a,b in zip(expected,actual))
    matches=math.isfinite(distance) and distance<=.05 and dot>=.94
    detail.update(matched=matches,distance_m=round(distance,7),normal_dot=round(dot,7),
                  reason=None if matches else 'surface_mismatch')
    return matches,detail,result

def floor_proof_covers_pose(detail, response):
    """A proof authorizes only a pose from the exact same native scene."""
    return bool(detail and detail.get('matched') is True and
                type(response.get('collision_revision')) is int and
                detail.get('native_revision')==response['collision_revision'])

def publish(name, data):
    if name=='status.json' and IPC is not None:
        IPC.status.write(json.dumps(data,separators=(',',':'),allow_nan=False).encode())
        return
    target = MAILBOX / name
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    # Lua's Windows CRT reader may briefly deny delete access to the old file.
    # Keep the completed temporary file and retry; a single overlap must not
    # terminate the simulation and leave the host waiting indefinitely.
    deadline = time.monotonic() + 2
    while True:
        try:
            os.replace(temp, target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Cannot publish {name}: reader held the file for two seconds")
            time.sleep(0.005)

def read(name):
    try:
        return json.loads((MAILBOX / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

def read_host():
    if IPC is not None:
        blob=IPC.host.read()
        if blob:
            try: return json.loads(blob)
            except ValueError: pass
        return None
    candidates = []
    for slot in range(4):
        path = MAILBOX / f"host{slot}.json"
        try:
            stamp = path.stat().st_mtime_ns
            value = json.loads(path.read_text(encoding="utf-8"))
            candidates.append((stamp, value))
        except (OSError, ValueError):
            pass
    return max(candidates, key=lambda pair: pair[0])[1] if candidates else read("host.json")

def publish_frame(packet, serial):
    slot = serial % 4
    packet["serial"] = serial
    if IPC is not None:
        IPC.frame.write(binary_frame(packet))
        return
    # Fixed numeric layout: Lua's native binary unpacker avoids a JSON decoder
    # and compiling a new Lua program on the game thread for each frame.
    target=MAILBOX/f"frame{slot}.bin"
    temp=target.with_suffix('.bin.tmp')
    temp.write_bytes(binary_frame(packet))
    deadline=time.monotonic()+2
    while True:
        try: os.replace(temp,target);break
        except PermissionError:
            if time.monotonic()>=deadline: raise RuntimeError('Frame reader held a slot for two seconds')
            time.sleep(.005)
    # Small pointer may be briefly incomplete; Lua keeps its previous pose.
    (MAILBOX / "frame-index.json").write_text(json.dumps({"serial":serial,"slot":slot}), encoding="utf-8")

def binary_frame(packet):
    generation=packet['generation'].encode('utf-8');state=packet['state'].encode('utf-8')
    body=packet['body'];board=packet['board'];camera=packet['camera']
    values=packet['root']+packet['velocity']+camera['position']+camera['basis']+[camera['fov']]+body+board+packet['rt']
    if not all(math.isfinite(v) for v in values): raise ValueError('Nonfinite frame value')
    header=struct.pack('<4sQIQHHHHB',b'S3D2',packet['serial'],packet['epoch'],packet['tick'],
                       len(generation),len(state),len(body),len(board),bool(packet['controller_connected']))
    return header+generation+state+struct.pack('<'+'d'*len(values),*values)

def lua_literal(value):
    if value is None: return 'nil'
    if value is True: return 'true'
    if value is False: return 'false'
    if isinstance(value,str): return json.dumps(value,ensure_ascii=False)
    if isinstance(value,(int,float)):
        if not math.isfinite(value): raise ValueError('Nonfinite frame value')
        return repr(value)
    if isinstance(value,list): return '{'+','.join(map(lua_literal,value))+'}'
    if isinstance(value,dict):
        return '{'+','.join('['+lua_literal(k)+']='+lua_literal(v) for k,v in value.items())+'}'
    raise TypeError(type(value).__name__)

def flat_transform(t):
    return [t['Translation'][k] for k in 'XYZ']+[t['Rotation'][k] for k in 'XYZW']+[t['Scale3D'][k] for k in 'XYZ']

class SkaterMesh:
    def __init__(self):
        data = (ASSETS / "private/skater.glb").read_bytes()
        magic, version, size = struct.unpack_from("<III", data)
        if magic != 0x46546C67 or version != 2 or size != len(data):
            raise ValueError("Invalid converted GLB")
        offset = 12
        while offset < len(data):
            length, kind = struct.unpack_from("<I4s", data, offset)
            payload = data[offset + 8:offset + 8 + length]
            if kind == b"JSON":
                doc = json.loads(payload)
            elif kind == b"BIN\0":
                buffer = payload
            offset += 8 + length
        self.doc = doc
        def accessor(index):
            a = doc["accessors"][index]
            view = doc["bufferViews"][a["bufferView"]]
            dtype = {5126: "<f4", 5123: "<u2", 5125: "<u4", 5121: "u1"}[a["componentType"]]
            count = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[a["type"]]
            if "byteStride" in view or "sparse" in a:
                raise ValueError("Unexpected converted GLB accessor layout")
            return np.frombuffer(buffer, dtype=dtype, count=a["count"] * count,
                                 offset=view.get("byteOffset", 0) + a.get("byteOffset", 0)).reshape(a["count"], count)
        skin = doc["skins"][0]
        self.names = [doc["nodes"][node]["name"] for node in skin["joints"]]
        self.inverse = accessor(skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
        # Same GLB-to-native bone basis as the example's board renderer.
        self.rb = np.array([[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]], dtype=np.float32)
        self.surfaces = []
        self.texture_paths=[]
        topology = []
        for primitive in doc["meshes"][0]["primitives"]:
            attrs = primitive["attributes"]
            positions = accessor(attrs["POSITION"])
            normals = accessor(attrs["NORMAL"])
            uvs = accessor(attrs["TEXCOORD_0"])
            joints = accessor(attrs["JOINTS_0"])
            weights = accessor(attrs["WEIGHTS_0"])
            indices = accessor(primitive["indices"]).reshape(-1, 3)[:, [0, 2, 1]].reshape(-1)
            material = doc["materials"][primitive["material"]]
            pbr = material["pbrMetallicRoughness"]
            tint = np.array(pbr.get("baseColorFactor", [1,1,1,1]))
            image_index = doc["textures"][pbr["baseColorTexture"]["index"]]["source"]
            image_view = doc["bufferViews"][doc["images"][image_index]["bufferView"]]
            start = image_view.get("byteOffset", 0)
            pixels=Image.open(io.BytesIO(buffer[start:start + image_view["byteLength"]])).convert("RGBA")
            image=np.array(pixels)
            texture_path=MAILBOX/f"retail-{len(topology)}.png"
            if len(topology)>=7: pixels.save(texture_path)
            self.texture_paths.append(str(texture_path).replace('\\','/'))
            x = (uvs[:, 0] % 1 * (image.shape[1] - 1)).astype(int)
            y = (uvs[:, 1] % 1 * (image.shape[0] - 1)).astype(int)
            colors = image[y, x] / 255 * tint
            self.surfaces.append((np.column_stack((positions, np.ones(len(positions),dtype=np.float32))),
                                  normals, joints, weights))
            topology.append({"triangles": indices.tolist(), "uv": uvs.tolist(),
                             "colors": colors.tolist(), "material": material["name"]})
        publish("topology.json", {"sections": topology})
        self.topology=topology
        print("Skater geometry loaded:", len(topology), "sections,",
              sum(len(s[0]) for s in self.surfaces), "vertices", flush=True)

    def pose(self, response):
        by_name = {name: i for i, name in enumerate(response["names"])}
        native = np.asarray(response["bones"], dtype=np.float32).reshape(-1,4,4).transpose(0,2,1)
        root = np.asarray(response["root"], dtype=np.float32).reshape(4,4).T
        matrices = root @ native[[by_name[n] for n in self.names]] @ self.rb @ self.inverse
        sections = []
        for positions, normals, joints, weights in self.surfaces:
            selected = matrices[joints]
            p = (np.einsum("nkij,nj->nki", selected, positions) * weights[:,:,None]).sum(axis=1)[:,:3]
            n = (np.einsum("nkij,nj->nki", selected[:,:,:3,:3], normals) * weights[:,:,None]).sum(axis=1)
            n /= np.maximum(np.linalg.norm(n,axis=1,keepdims=True),1e-20)
            p = p[:,[0,2,1]] * [100,-100,100]
            n = n[:,[0,2,1]] * [1,-1,1]
            if not np.isfinite(p).all() or np.max(np.abs(p)) > 1e6:
                raise ValueError("Invalid skinned geometry")
            sections.append({"vertices": np.round(p,3).tolist(), "normals": np.round(n,5).tolist()})
        return sections

def run():
    global IPC
    from skate_ipc import Transport
    IPC=Transport()
    MAILBOX.mkdir(parents=True,exist_ok=True)
    logs=MAILBOX.parent/'logs'
    logs.mkdir(parents=True,exist_ok=True)
    inputs=create_input(WORK)
    mesh = SkaterMesh()
    from skate_host_render import HostRender
    renderer=None;renderer_stamp=None;renderer_retry=0.;renderer_error=None
    stop_file=MAILBOX/'relay-stop.request'
    if os.environ.get('S3_MANAGED_RELAY')!='1':stop_file.unlink(missing_ok=True)
    log = (logs / "skate-relay-native.log").open("w", encoding="utf-8")
    process = subprocess.Popen([str(load_paths(WORK)['worker'])],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                               text=True, bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW)
    from skate_loading_progress import LoadingProgress,NativeReplyReader
    loading=LoadingProgress(logs/'skate-relay-native.log')
    replies=NativeReplyReader()
    def request(data):
        if replies.pending is not None:
            raise RuntimeError('Cannot send a native command while its loading reply is pending')
        loading.native(data.get('op'))
        process.stdin.write(json.dumps(data, separators=(",", ":")) + "\n")
        process.stdin.flush()
        result = json.loads(replies.read(process.stdout,data.get('op'),loading_progress))
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "Simulation failed"))
        return result
    cache_log=(logs/'skate-cache-worker.log').open('w',encoding='utf-8')
    cache_process=subprocess.Popen([sys.executable,str(WORK/'skate_cache_worker.py')],stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=cache_log,text=True,bufsize=1,creationflags=subprocess.CREATE_NO_WINDOW)
    executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='cached-map-request')
    from skate_warm_world import WarmWorldStore,initialize_world,prewarm_world
    warm_world=WarmWorldStore(load_paths(WORK)['map_data'],load_paths(WORK)['worker'])
    from skate_cache_background import BackgroundCache
    background_log=(logs/'skate-cache-background.log').open('ab',buffering=0)
    background=BackgroundCache(load_paths(WORK),request,background_log)
    preloaded=None;prewarm_attempted=None;legacy_checkpoint_future=None
    def prepare_cached(world):
        cache_process.stdin.write(json.dumps(world,separators=(',',':'))+'\n');cache_process.stdin.flush()
        line=cache_process.stdout.readline()
        if not line:raise RuntimeError('Collision cache worker ended unexpectedly')
        return json.loads(line)
    cache_future=None;cache_descriptor=None;queued_descriptor=None;desired=None
    installed_descriptor=None;pending_descriptor=None;pending_report=None;pending_cached=None
    generation = None
    active_epoch = None
    ticks = 0
    last_step = time.monotonic()
    host_stamp = None
    last_host_update = 0
    world_stamp = None
    world_revision=0
    installed_revision=0
    pending_revision=None
    source_period=0.016666600480675697
    serial = int(time.time()*1000)  # Must remain exactly representable in Lua's double.
    cached_host = None
    metrics={k:0. for k in ['host_ms','native_ms','retarget_ms','publish_ms','steps']}
    metric_count=0
    input_state={};status_value={};last_status_heartbeat=0.;status_serial=0;selected_camera=None;summon_state=None
    entry_marker=None;entry_started=0.;entry_status=None;entry_wait_logged=None
    entry_floor_revision=None;entry_floor_matches=False;entry_floor_detail=None
    last_loading_heartbeat=0.
    def publish_status(value):
        nonlocal status_value
        if value.get('status') in ('ready','error'):loading.finish()
        status_value=value
        publish('status.json',{**value,'progress':loading.snapshot()})
    def loading_progress():
        nonlocal last_loading_heartbeat
        if stop_file.exists():
            loading.finish()
            raise RuntimeError('Relay stopped during cache preparation')
        now=time.monotonic()
        if now-last_loading_heartbeat>=.5:
            last_loading_heartbeat=now
            progress=loading.snapshot()
            if progress is not None:
                publish_status({'status':'loading','generation':progress['generation'],
                    'heartbeat':int(now*1000)})
    def coverage_status(descriptor,report):
        if descriptor['mode']=='whole_world':
            return {'collision_scope':'whole_world','triangle_count':report['total'],
                    'world_fingerprint':report['source_fingerprint'],'scene_fingerprint':report['scene_fingerprint']}
        return {'collision_scope':'local','coverage_center':descriptor['center'],
                'coverage_radius_cm':report['radius_m']*100}
    def grind_status(result):
        world=result.get('world')
        expanded=world.get('exposed_edges') if isinstance(world,dict) else None
        ready=isinstance(expanded,dict) and expanded.get('ready') is True
        # Machine-readable readiness only. The loading-only HUD does not show
        # a persistent diagnostics banner while either provider is playable.
        return {'expanded_grinding_ready':ready,'grind_coverage':'expanded' if ready else 'legacy'}
    def collision_status(result):
        nonlocal pending_revision,pending_descriptor,pending_report,pending_cached,installed_revision,installed_descriptor
        nonlocal legacy_checkpoint_future
        if pending_revision is None:return
        if result.get('collision_error_revision')==pending_revision:
            publish_status({'status':'error','generation':generation,'failed_revision':pending_revision,
                            'error':result.get('collision_error','Collision update failed')})
            print('NATIVE COLLISION ERROR',pending_revision,result.get('collision_error'),flush=True)
            pending_revision=None;pending_descriptor=None;pending_report=None;pending_cached=None
        elif result.get('collision_revision')==pending_revision:
            installed_revision=pending_revision;installed_descriptor=pending_descriptor
            publish_status({'status':'ready','generation':generation,'period':source_period,
                'collision_revision':installed_revision,**coverage_status(pending_descriptor,pending_report),**grind_status(result)})
            print('CACHED COLLISION INSTALLED',installed_revision,flush=True)
            if pending_descriptor['mode']=='whole_world':
                # A separate interpreter avoids holding the frame loop's GIL
                # while validating a full-world manifest and saving its roots.
                background.accepted(pending_descriptor,pending_cached,result)
                checkpoint=result.get('world',{}).get('scene_checkpoint',{})
                if not background.managed or checkpoint.get('format')!='exposed_edges_v1':
                    legacy_checkpoint_future=executor.submit(save_legacy_accepted,pending_descriptor,pending_cached,result)
            pending_revision=None;pending_descriptor=None;pending_report=None;pending_cached=None
    def save_legacy_accepted(descriptor,cached,result):
        try:return warm_world.promote(descriptor,cached,result)
        except (OSError,ValueError,KeyError,TypeError) as error:
            print('WARM WORLD CHECKPOINT NOT SAVED',str(error),flush=True)
            return False
    def status_heartbeat(host_active,enabled):
        nonlocal status_serial,last_status_heartbeat
        status_serial+=1;last_status_heartbeat=time.monotonic()
        publish('status.json',{**status_value,'heartbeat':status_serial,'suspended':host_active and not enabled,
            'controller_name':input_state['controller_name'],'camera_mode':selected_camera,
            'input_backend':'SDL3 + keyboard','summon':summon_state,'entry':entry_status,
            'progress':loading.snapshot()})
    def query_entry_floor(host):
        nonlocal entry_floor_matches,entry_floor_detail,entry_floor_revision
        entry_floor_matches,entry_floor_detail,response=check_entry_floor(host['entry_floor'],request)
        if response is not None:collision_status(response)
        # Retry when the accepted host descriptor changes, not on native zero
        # versus host one immediately after startup. Failed proofs never spin.
        entry_floor_revision=installed_revision
        print('SOURCE ENTRY FLOOR',json.dumps({'generation':generation,'epoch':host['epoch'],
            'accepted_revision':entry_floor_revision,**entry_floor_detail}),flush=True)
    def prewarm_intent(host):
        warmup=host.get('world_warmup') if host else None
        return bool(isinstance(warmup,dict) and not host.get('active') and
            warmup.get('generation')==host.get('generation') and
            warmup.get('generation')!=generation and warmup.get('generation')!=prewarm_attempted)
    try:
        publish_status({"status":"waiting_for_collision"})
        while True:
            if stop_file.exists():
                print('Relay stop requested',flush=True)
                break
            # A queued host descriptor may wait while retirement runs. The
            # accepted native world remains resident and tick/pose keep working.
            collision_sequence=IPC.collision.sequence.value
            background.pump(can_collect=cache_future is None and pending_revision is None
                and queued_descriptor is None and collision_sequence in (0,world_stamp)
                and not prewarm_intent(cached_host)
                and (legacy_checkpoint_future is None or legacy_checkpoint_future.done()))
            if time.monotonic()>=renderer_retry:
                try:
                    new_renderer_stamp=((WORK/'skate_host_render.py').stat().st_mtime_ns,(MAILBOX/'host-rig.json').stat().st_mtime_ns)
                    if new_renderer_stamp!=renderer_stamp:
                        import importlib,skate_host_render
                        importlib.reload(skate_host_render)
                        candidate=skate_host_render.HostRender(mesh,MAILBOX/'host-rig.json',publish)
                        renderer=candidate;renderer_stamp=new_renderer_stamp;renderer_error=None
                except (OSError,ValueError,KeyError) as error:
                    # F10 can replace appearance data while this process reads
                    # it. Keep the complete prior renderer and retry the new
                    # file; a first install waits for its initial character rig.
                    renderer_retry=time.monotonic()+.25
                    if str(error)!=renderer_error:
                        renderer_error=str(error);print('WAITING FOR COMPLETE CHARACTER',renderer_error,flush=True)
                    if renderer is None:publish_status({'status':'waiting_for_character'})
            if renderer is None:
                time.sleep(.03);continue
            new_stamp=IPC.collision.sequence.value
            if new_stamp and new_stamp%2==0 and new_stamp!=world_stamp:
                blob=IPC.collision.read()
                if blob:
                    from skate_collision_codec import decode
                    world=decode(blob)
                    world_stamp=new_stamp
                    # A stale rolling packet from an earlier relay session is ignored.
                    if world.get('mode') in ('persistent','whole_world'):
                        queued_descriptor=world;desired=(world['generation'],world['revision'])
                        background.cancel_retirement()
            if (not background.blocked and cache_future is None and queued_descriptor is not None
                    and pending_revision is None):
                cache_descriptor=queued_descriptor;queued_descriptor=None
                loading.begin(cache_descriptor['generation'],cache_descriptor['revision'])
                cache_future=executor.submit(prepare_cached,cache_descriptor)
                # Retrying the current generation must replace an old error;
                # otherwise its heartbeat would hide the new revision's progress.
                publish_status({'status':'loading','generation':cache_descriptor['generation']})
            if not background.blocked and cache_future is not None and cache_future.done():
                descriptor=cache_descriptor
                try: cached=cache_future.result()
                except Exception as error: cached={'ok':False,'error':str(error)}
                cache_future=None;cache_descriptor=None
                if (descriptor['generation'],descriptor['revision'])==desired:
                    if not cached.get('ok'):
                        publish_status({'status':'error','generation':descriptor['generation'],'failed_revision':descriptor['revision'],
                                        'error':cached.get('error','Cache failed')})
                        print('CACHE ERROR',cached,flush=True)
                    else:
                        try:
                            background.invalidate()
                            whole=descriptor['mode']=='whole_world'
                            payload=({'scene_file':cached['scene_overlay']} if whole else
                                     {'collision_file':cached['collision_file'],'rails':[],'derive_rails':True})
                            if generation!=descriptor['generation']:
                                if whole:
                                    center=descriptor['center'];anchor=descriptor['anchor']
                                    spawn=[(center[0]-anchor[0])*.01,(center[2]-anchor[2])*.01+.25,-(center[1]-anchor[1])*.01]
                                    command={'op':'init_world',**payload,'assets':str(ASSETS),
                                             'manifest':cached['world_manifest'],'anchor':anchor,
                                             'spawn':spawn,'heading':descriptor['heading']}
                                    if os.environ.get('S3_MANAGED_RELAY') == '1':
                                        prepared_manifest = os.environ.get('S3_PREPARED_WORLD_MANIFEST')
                                        prepared_rails = os.environ.get('S3_PREPARED_RAIL_FILE')
                                        if (not prepared_manifest or not prepared_rails or
                                                Path(prepared_manifest).resolve() != Path(cached['world_manifest']).resolve()):
                                            raise RuntimeError('Whole-world preparation changed; run offline setup before skating')
                                        # An explicit rail file makes the native worker reject a
                                        # missing/corrupt cache instead of baking during gameplay.
                                        command['rail_file'] = prepared_rails
                                else:
                                    command={'op':'init' if generation is None else 'collision',**payload}
                                    if generation is None:command.update(assets=str(ASSETS),spawn=[0,.25,0],heading=descriptor['heading'])
                                if whole:
                                    # Never publish ready or poll gameplay input against the
                                    # previous snapshot. The fresh observed scene must finish
                                    # its native collision swap before this helper returns.
                                    result=initialize_world(request,command,descriptor,cached,warm_world,
                                        stopped=stop_file.exists,progress=loading_progress,preloaded=preloaded,
                                        persist=not background.managed)
                                    preloaded=None
                                else:result=request(command)
                                source_period=result.get('period',source_period)
                                generation=descriptor['generation'];active_epoch=None;selected_camera=None
                                installed_revision=descriptor['revision'];world_revision=installed_revision
                                installed_descriptor=descriptor
                                if whole:background.accepted(descriptor,cached,result)
                                publish_status({'status':'ready','generation':generation,'period':source_period,
                                    'collision_revision':installed_revision,**coverage_status(descriptor,cached['report']),**grind_status(result)})
                                print('CACHED WORLD READY',json.dumps(cached['report']),flush=True)
                            else:
                                request({'op':'world_scene' if whole else 'queue_collision','revision':descriptor['revision'],**payload})
                                pending_revision=descriptor['revision'];pending_descriptor=descriptor;pending_report=cached['report'];pending_cached=cached
                                world_revision=pending_revision
                                print('CACHED COLLISION QUEUED',pending_revision,'cache_ms',round(cached['elapsed_ms'],2),flush=True)
                        except Exception as error:
                            publish_status({'status':'error','generation':descriptor['generation'],'failed_revision':descriptor['revision'],
                                            'error':str(error)})
                            print('CACHED WORLD INSTALL ERROR',str(error),flush=True)
                else:
                    loading.finish()  # Superseded preparation cannot leak an old count.
            host_started=time.perf_counter()
            new_host = read_host()
            host_ms=(time.perf_counter()-host_started)*1000
            if new_host is not None: cached_host = new_host
            host = cached_host
            now = time.monotonic()
            if host and host.get("seq") != host_stamp:
                host_stamp = host["seq"]
                last_host_update = now
                warmup=host.get('world_warmup')
                if prewarm_intent(host):background.cancel_retirement()
                # init_world replaces the native session, including its pending
                # build handle. Let an older generation finish and persist its
                # accepted checkpoint before prewarming a reloaded adapter.
                # Do not consume prewarm_attempted while either job is pending.
                if (pending_revision is None and
                        not background.blocked and cache_future is None and
                        (legacy_checkpoint_future is None or legacy_checkpoint_future.done()) and
                        isinstance(warmup,dict) and not host.get('active') and
                        warmup.get('generation')==host.get('generation') and
                        warmup.get('generation')!=generation and warmup.get('generation')!=prewarm_attempted):
                    prewarm_attempted=warmup.get('generation');preloaded=None
                    manifest=os.environ.get('S3_PREPARED_WORLD_MANIFEST');rails=os.environ.get('S3_PREPARED_RAIL_FILE')
                    if manifest and rails:
                        try:
                            background.invalidate()
                            loading.begin(warmup['generation'],0,prewarm=True)
                            loading_progress()
                            preloaded=prewarm_world(request,warmup,warm_world,manifest,rails,ASSETS)
                        except (OSError,ValueError,KeyError,TypeError,RuntimeError) as error:
                            print('WARM WORLD PRELOAD SKIPPED',str(error),flush=True)
                        finally:
                            loading.finish()
                            publish_status({'status':'waiting_for_collision','generation':warmup['generation']})
                    now=time.monotonic()
            host_active = bool(host and generation is not None and host.get("generation") == generation
                               and host.get("active") and now-last_host_update < 1)
            input_state=inputs.sample(host or {},host_active and not host.get('paused'))
            enabled = host_active and not host.get('paused') and input_state['allowed']
            marker=(generation,host['epoch']) if host_active else None
            if marker!=entry_marker:
                entry_marker=marker;entry_started=time.perf_counter();entry_wait_logged=None
                entry_status=({'generation':generation,'epoch':host['epoch'],'phase':'waiting'} if marker else None)
                entry_floor_revision=None;entry_floor_matches=False;entry_floor_detail=None
            needs_entry=bool(host_active and active_epoch!=host['epoch'])
            # A mount request already came from the game. Establish its initial
            # neutral pose even if focus moved to the desktop while collision
            # finished. This request has no controls; subsequent timed physics
            # and all keyboard/controller input remain strictly focus-gated.
            # In-game menus/death/pause still block this initial setup request.
            start_entry=bool(needs_entry and not host.get('paused') and host.get('input_allowed',True))
            urgent_status=False
            if needs_entry:
                previous_entry=(entry_status.get('phase'),entry_status.get('wait_reason'))
                reason=('game_paused' if host.get('paused') else
                        'host_input_blocked' if not host.get('input_allowed',True) else None)
                if start_entry and 'entry_floor' in host:
                    if entry_floor_revision!=installed_revision:query_entry_floor(host)
                    if not entry_floor_matches:
                        start_entry=False;reason='entry_floor_mismatch'
                entry_status.update(phase='starting' if start_entry else 'waiting',wait_reason=reason)
                if reason=='entry_floor_mismatch':entry_status['phase']='waiting_collision'
                if entry_floor_detail is not None:entry_status['floor']=entry_floor_detail
                urgent_status=previous_entry!=(entry_status['phase'],entry_status['wait_reason'])
                if reason and reason!=entry_wait_logged:
                    entry_wait_logged=reason
                    print('SOURCE ENTRY WAIT',json.dumps({'generation':generation,'epoch':host['epoch'],'reason':reason}),flush=True)
            if generation is not None and selected_camera!=input_state['camera_mode']:
                from skate_input import source_camera_mode
                request({'op':'camera','mode':source_camera_mode(input_state['camera_mode'])})
                selected_camera=input_state['camera_mode']
                urgent_status=True
            if urgent_status or now-last_status_heartbeat>.5:status_heartbeat(host_active,enabled)
            if start_entry or (enabled and not needs_entry):
                native_started=time.perf_counter()
                steps=0
                first_frame=active_epoch!=host['epoch']
                if first_frame:
                    entry_mode=host.get('entry_mode','activate')
                    if entry_mode not in ('activate','summon'):raise ValueError('Unknown Skate activation mode')
                    result = request({"op":entry_mode, "spawn":host["spawn"], "heading":host["heading"]})
                    last_step = time.monotonic()
                    ticks = result["tick"]
                    collision_status(result)
                    if 'entry_floor' in host and not floor_proof_covers_pose(entry_floor_detail,result):
                        # A finished background scene can install at any native
                        # request boundary, including between surface/summon.
                        # Never publish a pose supported only by an older scene.
                        query_entry_floor(host)
                        if not floor_proof_covers_pose(entry_floor_detail,result):
                            entry_floor_matches=False
                            if entry_floor_detail.get('matched'):
                                entry_floor_detail={**entry_floor_detail,'matched':False,'reason':'scene_changed_during_activation'}
                            entry_status.update(phase='waiting_collision',wait_reason='entry_floor_mismatch',floor=entry_floor_detail)
                            status_heartbeat(host_active,enabled)
                            time.sleep(.03)
                            continue
                    active_epoch = host["epoch"]
                else:
                    period = 0.016666600480675697
                    steps = min(6,int((now-last_step)/period))
                    if steps == 0:
                        time.sleep(0.002)
                        continue
                    result = request({"op":"tick", "steps":steps, "controls":input_state['controls']})
                    last_step += steps*period
                    if now-last_step > 0.15: last_step=now
                    ticks = result["tick"]
                summon_state=result.get('summon')
                native_ms=(time.perf_counter()-native_started)*1000
                collision_status(result)
                render_started=time.perf_counter()
                bones,board,root_transform=renderer.pose_flat(result)
                render_ms=(time.perf_counter()-render_started)*1000
                packet = {"generation":generation, "epoch":active_epoch, "tick":ticks,
                          "root":result["root"], "state":result["state"], "velocity":result["velocity"],
                          "camera":result["camera"], "controller_connected":input_state['controller_connected'],
                          "body":bones,"board":board,"rt":root_transform}
                serial += 1
                publish_started=time.perf_counter()
                publish_frame(packet, serial)
                publish_ms=(time.perf_counter()-publish_started)*1000
                if first_frame:
                    entry_status={'generation':generation,'epoch':active_epoch,'phase':'pose_ready',
                        'entry_mode':entry_mode,'observed_to_first_frame_ms':round((time.perf_counter()-entry_started)*1000,3),
                        'wait_before_native_ms':round((native_started-entry_started)*1000,3),
                        'native_ms':round(native_ms,3),'retarget_ms':round(render_ms,3),'publish_ms':round(publish_ms,3),
                        'game_focused':input_state.get('focused'),'input_enabled':bool(enabled)}
                    if entry_floor_detail is not None:entry_status['floor']=entry_floor_detail
                    print('SOURCE ENTRY FIRST FRAME',json.dumps(entry_status),flush=True)
                    status_heartbeat(host_active,enabled)
                for k,v in [('host_ms',host_ms),('native_ms',native_ms),('retarget_ms',render_ms),
                            ('publish_ms',publish_ms),('steps',steps)]: metrics[k]+=v
                metric_count+=1
                if metric_count%120==0:
                    print('RELAY TIMING',json.dumps({k:round(v/metric_count,3) for k,v in metrics.items()}),flush=True)
                    if os.environ.get('S3_POSE_DIAGNOSTICS')=='1':
                        publish('native-pose-diagnostic.json',result)
            else:
                # Finish a queued collision swap even if the player exited while
                # it was building. Pose checks do not advance source physics.
                if pending_revision is not None:
                    collision_status(request({'op':'pose'}))
                last_step = now
                # Menu/focus pauses preserve the current source pose and input
                # history; only a real mode exit/new host invalidates activation.
                if not host_active:active_epoch = None
                time.sleep(0.03)
    except Exception as error:
        publish_status({"status":"error", "error":str(error),"generation":generation})
        raise
    finally:
        loading.finish()
        try:background.close()
        except Exception as error:print('CACHE BACKGROUND STOP ERROR',str(error),flush=True)
        background_log.close()
        inputs.close()
        if process.poll() is None:
            if replies.pending is not None:
                # A cancelled blocking load still owns stdout. Terminate it;
                # never write shutdown/another command over that pending reply.
                process.terminate();process.wait(timeout=10)
            else:
                process.stdin.write('{"op":"shutdown"}\n')
                process.stdin.flush()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.terminate();process.wait(timeout=10)
        replies.close()
        if cache_process.poll() is None:
            cache_process.stdin.close()
            try:cache_process.wait(timeout=10)
            except subprocess.TimeoutExpired:cache_process.terminate();cache_process.wait(timeout=5)
        executor.shutdown(wait=True,cancel_futures=True)
        cache_log.close()
        log.close()
        stop_file.unlink(missing_ok=True)
        IPC.close()

if __name__ == "__main__":
    run()
