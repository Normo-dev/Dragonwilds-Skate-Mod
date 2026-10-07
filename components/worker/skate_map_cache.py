"""Local collision derived from owned Dragonwilds assets; no game-thread capture."""
from pathlib import Path
import json,struct,math,hashlib,time,os,shutil
from collections import OrderedDict,defaultdict
import numpy as np
from runtime_paths import load_paths
from skate_authored_gate import AuthoredGate,validate_scene_policy,live_included
from skate_spline_collision import state_key as spline_state_key, reflected_state_key as spline_reflected_state_key

ROOT=load_paths()['map_data']
NO_QUERY_SHAPES=(0,2,4,'ECollisionEnabled::NoCollision','ECollisionEnabled::PhysicsOnly','ECollisionEnabled::ProbeOnly')

def xyz(v,default=(0,0,0)):
    return np.array([v.get(k,default[i]) for i,k in enumerate('XYZ')],dtype=np.float64) if v else np.array(default,dtype=np.float64)

def matrix(t):
    q=t.get('Rotation',{});x,y,z,w=[q.get(k,1 if k=='W' else 0) for k in 'XYZW']
    norm=math.sqrt(x*x+y*y+z*z+w*w)
    if not norm: raise ValueError('Zero rotation')
    x,y,z,w=np.array([x,y,z,w])/norm
    r=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
    return r*xyz(t.get('Scale3D'),(1,1,1))[None,:],xyz(t.get('Translation'))

def apply(p,t):
    r,v=matrix(t);return p@r.T+v

def rotation(e):
    p,y,r=[math.radians(e.get(k,0)) for k in ['Pitch','Yaw','Roll']]
    cp,sp,cy,sy,cr,sr=math.cos(p),math.sin(p),math.cos(y),math.sin(y),math.cos(r),math.sin(r)
    return np.array([[cy,-sy,0],[sy,cy,0],[0,0,1]])@np.array([[cp,0,-sp],[0,1,0],[sp,0,cp]])@np.array([[1,0,0],[0,cr,sr],[0,-sr,cr]])

def round_shape(radius,length=0):
    # Triangulation of the host's own sphere/capsule primitive.
    rings=[]
    angles=[(-math.pi/2+math.pi*lat/8,-1 if lat<=4 else 1) for lat in range(9)]
    if length: angles.insert(5,(0,1))
    for a,side in angles:
        z=radius*math.sin(a)+side*length/2
        rings.append([[radius*math.cos(a)*math.cos(2*math.pi*i/12),radius*math.cos(a)*math.sin(2*math.pi*i/12),z] for i in range(12)])
    p=np.array(rings);tris=[]
    for j in range(len(rings)-1):
        for i in range(12):
            n=(i+1)%12;tris.extend([[p[j,i],p[j,n],p[j+1,n]],[p[j,i],p[j+1,n],p[j+1,i]]])
    return np.array(tris)

def geometry(entry):
    if 'UseComplexAsSimple' in str(entry.get('trace')):
        c=entry.get('complex')
        if not c:return np.empty((0,3,3)),1
        points=np.array([xyz(p) for p in c['vertices']])
        return points[np.array(c['indices'],dtype=np.int64).reshape(-1,3)],0
    a=entry.get('agg') or {};parts=[];omitted=sum(entry.get('unhandled_geometry',{}).values())
    cooked=entry.get('cooked') or {}
    fallbacks=cooked.get('fallbacks',{}) if cooked.get('success') else {}
    absent=set(cooked.get('absent_convex_indices',[])) if cooked.get('success') else set()
    for shape_index,c in enumerate(a.get('ConvexElems',[])):
        if c.get('CollisionEnabled') in NO_QUERY_SHAPES:continue
        points=np.array([xyz(p) for p in c['VertexData']]);indices=c['IndexData']
        if not indices or len(indices)%3:
            fallback=fallbacks.get(str(shape_index),fallbacks.get(shape_index))
            if fallback:
                p=np.array([xyz(v) for v in fallback['vertices']])[np.array(fallback['indices'],dtype=np.int64).reshape(-1,3)]
                parts.append(p if fallback['space']=='body' else apply(p,c['Transform']))
            elif shape_index not in absent:omitted+=1
            continue
        parts.append(apply(points[np.array(indices).reshape(-1,3)],c['Transform']))
    for c in a.get('BoxElems',[]):
        if c.get('CollisionEnabled') in NO_QUERY_SHAPES:continue
        p=np.array([[x,y,z] for z in [-.5,.5] for y in [-.5,.5] for x in [-.5,.5]])*np.array([c['X'],c['Y'],c['Z']])
        indices=np.array([[0,2,3],[0,3,1],[4,5,7],[4,7,6],[0,1,5],[0,5,4],[2,6,7],[2,7,3],[0,4,6],[0,6,2],[1,3,7],[1,7,5]])
        parts.append(p[indices]@rotation(c['Rotation']).T+xyz(c['Center']))
    for key in ['SphereElems','SphylElems']:
        for c in a.get(key,[]):
            if c.get('CollisionEnabled') in NO_QUERY_SHAPES:continue
            p=round_shape(c['Radius'],c.get('Length',0))
            parts.append(p@rotation(c.get('Rotation',{})).T+xyz(c['Center']))
    omitted+=sum(c.get('CollisionEnabled') not in NO_QUERY_SHAPES for c in a.get('TaperedCapsuleElems',[]))
    return (np.concatenate(parts) if parts else np.empty((0,3,3))),omitted

def clip(t,center,radius):
    return t[(t[:,:,:2].max(axis=1)>=center[:2]-radius).all(axis=1)&(t[:,:,:2].min(axis=1)<=center[:2]+radius).all(axis=1)]

def object_path(name):
    if "'" in name:return name.split("'",1)[1].rstrip("'")
    return name.split(' ',1)[-1]

def package_path(name):
    return object_path(name).split(':',1)[0]


def mesh_id(name):
    return hashlib.sha256(object_path(name).encode()).hexdigest().upper()[:24]


def trace_policy(value):
    if value is None:return 0
    if isinstance(value,(int,float)) and value in (0,1,2,3):return int(value)
    text=str(value)
    for name,number in [('UseComplexAsSimple',3),('UseSimpleAsComplex',2),('UseSimpleAndComplex',1),('UseDefault',0)]:
        if name.lower() in text.lower():return number
    return None


def empty_collision_evidence(entry):
    cooked=entry.get('cooked') or {}
    if not entry.get('raw_aggregate_checked') or entry.get('unhandled_geometry'):return None
    shapes=[shape for name in ('SphereElems','BoxElems','SphylElems','ConvexElems','TaperedCapsuleElems')
            for shape in (entry.get('agg') or {}).get(name,[])]
    if shapes and all(shape.get('CollisionEnabled') in NO_QUERY_SHAPES for shape in shapes):
        # The shapes may still have cooked bytes, but Unreal explicitly disables
        # them for simple queries. Complex-as-simple remains a separate policy.
        return {'reason':'all_simple_shapes_disabled','trace':trace_policy(entry.get('trace'))}
    if not cooked.get('success') or cooked.get('shared') or cooked.get('convex_count')!=0:return None
    if cooked.get('triangle_mesh_count')==0:
        return {'reason':'no_cooked_physics_shapes','trace':trace_policy(entry.get('trace'))}
    return {'reason':'complex_only','trace':trace_policy(entry.get('trace'))}


class _ArrayCache:
    """A bounded cache: loading a new area cannot retain whole-world triangles."""
    def __init__(self,budget):
        self.budget=budget;self.bytes=0;self.values=OrderedDict()

    def get(self,key,create):
        if key in self.values:
            self.values.move_to_end(key);return self.values[key]
        value=create()
        if value.nbytes<=self.budget:
            while self.values and self.bytes+value.nbytes>self.budget:
                _,old=self.values.popitem(last=False);self.bytes-=old.nbytes
            self.values[key]=value;self.bytes+=value.nbytes
        return value


class MapCache:
    """Persistent owned-asset geometry and a reusable spatial instance index.

    The anchor is the fixed Unreal origin used by the running Skate session.
    Center only chooses collision coverage; moving it never moves the session.
    Scene snapshots replace explicitly observed packages, including empty ones.
    Build on one background worker: an instance is intentionally single-threaded.
    """
    SCHEMA=7
    CELL=10000.0
    MAX_CELL_REFERENCES=64
    MAX_TRIANGLES=16_000_000

    def __init__(self,root=ROOT,cache_dir=None,triangle_cache_mb=128):
        started=time.perf_counter()
        self.root=Path(root);cache_base=Path(cache_dir or self.root/'collision-index-v3');self.cache_base=cache_base
        self.authored=AuthoredGate(self.root) if (self.root/'authored-policy.json').exists() else None
        self._terrain_cache=_ArrayCache(triangle_cache_mb*1024*1024//2)
        self._world_cache=_ArrayCache(triangle_cache_mb*1024*1024//2)
        self._extra_geometry={};self._scene_digest=None;self._scene_records=[];self._scene_packages=set()
        self._scene_missing=[];self._scene_errors=[];self._last_payload=None
        sources=sorted(list((self.root/'static').glob('*.instances.jsonl'))+
                       list((self.root/'static/geometry').glob('*.json'))+
                       list(self.root.glob('*-terrain/terrain.jsonl'))+
                       list(self.root.glob('*-terrain/*.points'))+list(self.root.glob('*-terrain/*.visibility'))+
                       (list((self.root/'static').glob('*.policy.json'))+[self.root/'authored-policy.json',self.root/'authored-packages.json'] if self.authored else []))
        signature=[]
        for p in sources:
            status=p.stat();signature.append([str(p.relative_to(self.root)),status.st_size,status.st_mtime_ns])
        fingerprint=hashlib.sha256(json.dumps([self.SCHEMA,signature],separators=(',',':')).encode()).hexdigest()[:20]
        # A changed source set gets a new generation. Existing worker memory maps
        # remain valid, including on Windows where mapped files cannot be replaced.
        self.cache_dir=cache_base/fingerprint
        metadata=None
        for directory in (self.cache_dir,cache_base):
            try:
                candidate=json.loads((directory/'metadata.json').read_text())
                if candidate.get('schema')==self.SCHEMA and candidate.get('sources')==signature and candidate.get('authored_policy')==(self.authored.summary if self.authored else None):
                    metadata=candidate;self.cache_dir=directory;break
            except (FileNotFoundError,ValueError):pass
        self.index_rebuilt=metadata is None
        if metadata is None:metadata=self._create_index(sources,signature)
        self.metadata=metadata
        self.geom_meta=metadata['geometry'];self.geometry_ids={g['id']:i for i,g in enumerate(self.geom_meta)}
        self.spline_geometry={}
        self.spline_reflected_geometry={}
        for meta in self.geom_meta:
            if meta.get('component_collision'):
                self.spline_geometry.setdefault(spline_state_key(meta['component_collision']),[]).append(meta['id'])
                identity=(meta['path'],spline_reflected_state_key(meta['component_collision']))
                self.spline_reflected_geometry.setdefault(identity,[]).append(meta['id'])
        self.packages=metadata['packages'];self.package_ids={p:i for i,p in enumerate(self.packages)}
        self.terrain_meta=metadata['terrain']
        self.instances=np.load(self.cache_dir/'instances.npy',mmap_mode='r',allow_pickle=False)
        self.local_triangles=np.load(self.cache_dir/'geometry.npy',mmap_mode='r',allow_pickle=False)
        self.cell_keys=np.load(self.cache_dir/'cell-keys.npy',allow_pickle=False)
        self.cell_offsets=np.load(self.cache_dir/'cell-offsets.npy',allow_pickle=False)
        self.cell_instances=np.load(self.cache_dir/'cell-instances.npy',mmap_mode='r',allow_pickle=False)
        self.large_instances=np.load(self.cache_dir/'large-instances.npy',allow_pickle=False)
        self.cells={tuple(k):i for i,k in enumerate(self.cell_keys)}
        self.load_seconds=time.perf_counter()-started

    def _create_index(self,sources,signature):
        self.cache_dir.mkdir(parents=True,exist_ok=True)
        geometry_meta=[];geometry_parts=[];geometry_ids={};offset=0
        for path in sources:
            if path.parent.name!='geometry':continue
            data=json.loads(path.read_text());local,unsupported=geometry(data)
            local=np.asarray(local,dtype='<f8')
            if not np.isfinite(local).all():raise ValueError(f'Non-finite collision mesh {path.name}')
            bounds=[local.min(axis=(0,1)).tolist(),local.max(axis=(0,1)).tolist()] if len(local) else None
            entry={'id':path.stem,'path':data.get('path',path.stem),'offset':offset,'count':len(local),
                   'unsupported':unsupported,'bounds':bounds,'empty_evidence':empty_collision_evidence(data) if not len(local) else None}
            if data.get('component_collision'):entry['component_collision']=data['component_collision']
            geometry_ids[path.stem]=len(geometry_meta);geometry_meta.append(entry);geometry_parts.append(local);offset+=len(local)
        all_geometry=np.concatenate(geometry_parts) if geometry_parts else np.empty((0,3,3))
        np.save(self.cache_dir/'geometry.npy',all_geometry,allow_pickle=False)
        del all_geometry,geometry_parts
        dtype=np.dtype([('geometry','<u4'),('package','<u4'),('r','<f8',(3,3)),('v','<f8',(3,)),('lo','<f8',(3,)),('hi','<f8',(3,))])
        chunks=[];packages=[];package_ids={};missing=[];instances=None;reuse_cells=None
        manifest_signature=[s for s in signature if s[0].endswith('.instances.jsonl')]
        # Most later asset exports add a new building mesh without changing the
        # 331k map placements. Remap its geometry table, retaining the saved
        # matrices, bounds and spatial cells instead of parsing every placement.
        for previous_file in [self.cache_base/'metadata.json',*self.cache_base.glob('*/metadata.json')]:
            try:previous=json.loads(previous_file.read_text())
            except (FileNotFoundError,ValueError):continue
            if previous.get('schema') not in (3,4,self.SCHEMA) or previous.get('missing_geometry'):continue
            if previous.get('authored_policy')!=(self.authored.summary if self.authored else None):continue
            if [s for s in previous['sources'] if s[0].endswith('.instances.jsonl')]!=manifest_signature:continue
            if any(g['id'] not in geometry_ids for g in previous['geometry']):continue
            instances=np.load(previous_file.parent/'instances.npy',allow_pickle=False)
            packages=previous['packages'];remap=np.asarray([geometry_ids[g['id']] for g in previous['geometry']],dtype='<u4')
            previous_geometry=instances['geometry'].copy();instances['geometry']=remap[previous_geometry]
            changed=False
            for old_index,old in enumerate(previous['geometry']):
                new=geometry_meta[remap[old_index]]
                if old['bounds']==new['bounds']:continue
                rows=np.flatnonzero(previous_geometry==old_index)
                if not len(rows):continue
                changed=True;m=instances[rows];v=m['v']
                if new['bounds']:
                    lo,hi=np.asarray(new['bounds']);middle=np.einsum('nij,j->ni',m['r'],(lo+hi)*.5)+v
                    half=np.einsum('nij,j->ni',np.abs(m['r']),(hi-lo)*.5);lo,hi=middle-half,middle+half
                else:lo=hi=v
                instances['lo'][rows]=lo;instances['hi'][rows]=hi
            if not changed:reuse_cells=previous_file.parent
            break
        if instances is None:
            for path in sources:
                if not path.name.endswith('.instances.jsonl'):continue
                records=[];row_count=0
                with path.open() as f:
                    for line_number,line in enumerate(f,1):
                        m=json.loads(line);row_count+=1
                        if self.authored:self.authored.row(path,m)
                        p=package_path(m['name'])
                        if p not in package_ids:package_ids[p]=len(packages);packages.append(p)
                        g=geometry_ids.get(m['id'])
                        if g is None:
                            missing.append({'id':m['id'],'name':m['name'],'manifest':path.name,'line':line_number});continue
                        entry=geometry_meta[g];r,v=matrix(m['transform'])
                        if not np.isfinite(r).all() or not np.isfinite(v).all():raise ValueError(f'Non-finite transform {m["name"]}')
                        bounds=entry['bounds']
                        if bounds:
                            lo,hi=np.asarray(bounds);middle=((lo+hi)*.5)@r.T+v;half=np.abs(r)@((hi-lo)*.5);lo,hi=middle-half,middle+half
                        else:lo=hi=v
                        records.append((g,package_ids[p],r,v,lo,hi))
                if self.authored:self.authored.count(path,row_count)
                if records:chunks.append(np.array(records,dtype=dtype))
            instances=np.concatenate(chunks) if chunks else np.empty(0,dtype=dtype)
        np.save(self.cache_dir/'instances.npy',instances,allow_pickle=False)
        if reuse_cells:
            for name in ['cell-keys.npy','cell-offsets.npy','cell-instances.npy','large-instances.npy']:
                shutil.copyfile(reuse_cells/name,self.cache_dir/name)
        else:
            cells=defaultdict(list);large=[]
            for i,m in enumerate(instances):
                lo=np.floor(m['lo'][:2]/self.CELL).astype(np.int64);hi=np.floor(m['hi'][:2]/self.CELL).astype(np.int64)
                if np.prod(hi-lo+1)>self.MAX_CELL_REFERENCES:large.append(i);continue
                for x in range(lo[0],hi[0]+1):
                    for y in range(lo[1],hi[1]+1):cells[(x,y)].append(i)
            keys=sorted(cells);offsets=[0];indices=[]
            for k in keys:indices.extend(cells[k]);offsets.append(len(indices))
            np.save(self.cache_dir/'cell-keys.npy',np.asarray(keys,dtype='<i8').reshape(-1,2),allow_pickle=False)
            np.save(self.cache_dir/'cell-offsets.npy',np.asarray(offsets,dtype='<i8'),allow_pickle=False)
            np.save(self.cache_dir/'cell-instances.npy',np.asarray(indices,dtype='<u4'),allow_pickle=False)
            np.save(self.cache_dir/'large-instances.npy',np.asarray(large,dtype='<u4'),allow_pickle=False)
        terrain_meta=[]
        for path in sources:
            if path.name!='terrain.jsonl':continue
            for line in path.read_text().splitlines():
                m=json.loads(line);m['folder']=str(path.parent.relative_to(self.root));terrain_meta.append(m)
        metadata={'schema':self.SCHEMA,'sources':signature,'geometry':geometry_meta,'packages':packages,
                  'terrain':terrain_meta,'missing_geometry':missing,'instances':len(instances),
                  'authored_policy':self.authored.summary if self.authored else None}
        temp=self.cache_dir/'metadata.json.tmp';temp.write_text(json.dumps(metadata));os.replace(temp,self.cache_dir/'metadata.json')
        return metadata

    def _geometry(self,key):
        index=self.geometry_ids.get(key)
        if index is not None:
            m=self.geom_meta[index];return self.local_triangles[m['offset']:m['offset']+m['count']],m
        if key not in self._extra_geometry:
            path=self.root/'static/geometry'/(key+'.json')
            if not path.exists():return None,None
            entry=json.loads(path.read_text());local,unsupported=geometry(entry)
            if not np.isfinite(local).all():raise ValueError(f'Non-finite collision mesh {key}')
            bounds=[local.min(axis=(0,1)).tolist(),local.max(axis=(0,1)).tolist()] if len(local) else None
            self._extra_geometry[key]=(local,{'id':key,'path':entry.get('path',key),'count':len(local),'unsupported':unsupported,'bounds':bounds,
                                               'empty_evidence':empty_collision_evidence(entry) if not len(local) else None})
        return self._extra_geometry[key]

    def live_geometry_id(self,item):
        state=item.get('component_collision')
        if state is None:
            if str(item.get('name','')).startswith('SplineMeshComponent '):
                raise ValueError('Spline component has no exact deformed collision state')
            return item.get('id') or mesh_id(item['mesh'])
        if state.get('mesh')!=object_path(item['mesh']):raise ValueError('Spline collision mesh differs from component mesh')
        if state.get('kind')=='spline_body_setup_v2':
            component=state.get('component_path');body=state.get('body_path')
            if component!=object_path(item.get('name','')):raise ValueError('Spline body owner differs from component')
            if not isinstance(body,str) or not body.startswith(component+'.') or state.get('mesh_dirty') is not False:
                raise ValueError('Spline body identity is missing or deformed mesh is dirty')
            ids=self.spline_reflected_geometry.get((body,spline_reflected_state_key(state)),[])
        else:
            ids=self.spline_geometry.get(spline_state_key(state),[])
        if not ids:raise ValueError('Spline collision state changed or has no serialized deformed BodySetup: '+item['name'])
        if len(ids)>1:
            first,_=self._geometry(ids[0])
            if any(not np.array_equal(first,self._geometry(key)[0]) for key in ids[1:]):
                raise ValueError('Ambiguous serialized spline collision state')
        return ids[0]

    def _set_scene(self,scene):
        # Hashing also notices an in-place edit by a caller; object identity does not.
        digest=hashlib.sha256(json.dumps(scene,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if digest==self._scene_digest:
            # A mesh exporter may have just supplied geometry absent on the prior
            # build. Absence is never cached permanently.
            newly_available=any((self.root/'static/geometry'/(m['id']+'.json')).exists() for m in self._scene_missing)
            if not newly_available:return
            self._last_payload=None
        self._scene_digest=digest;self._scene_records=[];self._scene_missing=[];self._scene_errors=[]
        self._scene_packages=set()
        if scene is None:return
        if self.authored:validate_scene_policy(scene,self.authored.policy)
        objects=scene.get('objects',[])
        if 'packages' in scene:
            self._scene_packages={package_path(p) for p in scene['packages']}
        else:
            self._scene_packages={package_path(o['name']) for o in objects}
        self._scene_errors=list(scene.get('errors',[]))
        for o in objects:
            if not live_included(o,self.authored.policy if self.authored else None):continue
            key=self.live_geometry_id(o);local,meta=self._geometry(key)
            if meta is None:
                self._scene_missing.append({'id':key,'name':o['name'],'mesh':o.get('mesh')});continue
            for instance_index,t in enumerate(o.get('instances',[o.get('transform')])):
                if t is None:self._scene_errors.append(f'Missing transform: {o["name"]}');continue
                r,v=matrix(t)
                if not np.isfinite(r).all() or not np.isfinite(v).all():raise ValueError(f'Non-finite live transform {o["name"]}')
                if meta['bounds']:
                    lo,hi=np.asarray(meta['bounds']);middle=((lo+hi)*.5)@r.T+v;half=np.abs(r)@((hi-lo)*.5);lo,hi=middle-half,middle+half
                else:lo=hi=v
                identity=('live',o['name'],instance_index,key,r.tobytes(),v.tobytes())
                self._scene_records.append((key,r,v,lo,hi,identity))

    def _candidates(self,center,radius):
        lo=np.floor((center[:2]-radius)/self.CELL).astype(np.int64);hi=np.floor((center[:2]+radius)/self.CELL).astype(np.int64)
        parts=[self.large_instances]
        for x in range(lo[0],hi[0]+1):
            for y in range(lo[1],hi[1]+1):
                index=self.cells.get((x,y))
                if index is not None:parts.append(self.cell_instances[self.cell_offsets[index]:self.cell_offsets[index+1]])
        ids=np.unique(np.concatenate(parts))
        m=self.instances[ids]
        keep=(m['hi'][:,:2]>=center[:2]-radius).all(axis=1)&(m['lo'][:,:2]<=center[:2]+radius).all(axis=1)
        if self._scene_packages:
            replaced=[self.package_ids[p] for p in self._scene_packages if p in self.package_ids]
            keep&=~np.isin(m['package'],replaced)
        return ids[keep]

    def terrain(self,center,radius):
        center=np.asarray(center,dtype=np.float64);parts=[];count=0
        for i,m in enumerate(self.terrain_meta):
            if np.any(np.asarray(m['max'])[:2]<center[:2]-radius) or np.any(np.asarray(m['min'])[:2]>center[:2]+radius):continue
            def create():
                n=m['n'];folder=self.root/m['folder']
                p=np.memmap(folder/(m['file']+'.points'),dtype='<f4',mode='r',shape=(n,n,3))
                vis=np.memmap(folder/(m['file']+'.visibility'),dtype='u1',mode='r',shape=(n,n))
                keep=np.maximum.reduce([vis[:-1,:-1],vis[:-1,1:],vis[1:,1:],vis[1:,:-1]])<170
                a,b,c,d=p[:-1,:-1][keep],p[:-1,1:][keep],p[1:,1:][keep],p[1:,:-1][keep]
                t=np.concatenate([np.stack([a,b,c],axis=1),np.stack([a,c,d],axis=1)])
                return t[:,[0,2,1],:] if m['mirrored'] else t
            t=self._terrain_cache.get(i,create)
            contained=all(m['min'][j]>=center[j]-radius and m['max'][j]<=center[j]+radius for j in (0,1))
            selected=t if contained else clip(t,center,radius)
            if len(selected):parts.append(selected);count+=1
        return (np.concatenate(parts) if parts else np.empty((0,3,3))),count

    def static(self,center,radius,scene=None,_prepared=False):
        center=np.asarray(center,dtype=np.float64)
        if not _prepared:self._set_scene(scene)
        parts=[];unsupported=0;empty=0;ignored_empty=0;selected_instances=0;unsupported_meshes={};empty_meshes={}
        settings=(scene or {}).get('physicsSettings',{})
        default=trace_policy((scene or {}).get('default_shape_complexity',settings.get('engine_default')))
        lower=center[:2]-radius;upper=center[:2]+radius
        def append(local,meta,r,v,lo,hi,identity):
            nonlocal unsupported,empty,ignored_empty,selected_instances
            selected_instances+=1;unsupported+=meta['unsupported']
            if meta['unsupported']:
                entry=unsupported_meshes.setdefault(meta['id'],{'id':meta['id'],'mesh':meta['path'],'instances':0,'shapes_per_instance':meta['unsupported']})
                entry['instances']+=1
            if not len(local):
                empty+=1;evidence=meta.get('empty_evidence');ignored=False
                if evidence:
                    effective=default if evidence['trace']==0 else evidence['trace']
                    ignored=evidence['reason']=='no_cooked_physics_shapes' or effective in (1,2)
                if ignored:ignored_empty+=1
                entry=empty_meshes.setdefault(meta['id'],{'id':meta['id'],'mesh':meta['path'],'instances':0,
                                                         'evidence':evidence,'ignored_for_simple_collision':ignored})
                entry['instances']+=1;return
            def create():
                world=local@r.T+v
                return world[:,[0,2,1],:] if np.linalg.det(r)<0 else world
            world=self._world_cache.get(identity,create)
            # Most meshes are fully inside the requested square. Their exact
            # cached instance bounds let us skip per-triangle clipping entirely.
            contained=lo[0]>=lower[0] and lo[1]>=lower[1] and hi[0]<=upper[0] and hi[1]<=upper[1]
            t=world if contained else clip(world,center,radius)
            if len(t):parts.append(t)
        candidates=self._candidates(center,radius)
        for i in candidates:
            m=self.instances[i];meta=self.geom_meta[m['geometry']]
            local=self.local_triangles[meta['offset']:meta['offset']+meta['count']]
            append(local,meta,m['r'],m['v'],m['lo'],m['hi'],('offline',int(i)))
        live_count=0
        for key,r,v,lo,hi,identity in self._scene_records:
            if np.any(hi[:2]<center[:2]-radius) or np.any(lo[:2]>center[:2]+radius):continue
            local,meta=self._geometry(key);append(local,meta,r,v,lo,hi,identity);live_count+=1
        info={'omitted_shapes':unsupported,'unsupported_shapes':unsupported,'empty_geometry_instances':empty,
              'ignored_empty_geometry_instances':ignored_empty,'unresolved_empty_geometry_instances':empty-ignored_empty,
              'default_shape_complexity':default,
              'object_instances':selected_instances,'live_instances':live_count,'offline_candidates':len(candidates),
              'overridden_packages':len(self._scene_packages),'missing_geometry':self.metadata['missing_geometry']+self._scene_missing,
              'scene_errors':self._scene_errors,'unsupported_meshes':list(unsupported_meshes.values()),'empty_geometry':list(empty_meshes.values())}
        return (np.concatenate(parts) if parts else np.empty((0,3,3))),info

    def build(self,anchor,path,radius=25000,scene=None,center=None):
        started=time.perf_counter();anchor=np.asarray(anchor,dtype=np.float64)
        center=anchor.copy() if center is None else np.asarray(center,dtype=np.float64)
        if anchor.shape!=(3,) or center.shape!=(3,) or not np.isfinite(anchor).all() or not np.isfinite(center).all() or not math.isfinite(radius) or radius<=0:
            raise ValueError('Anchor/center must be finite XYZ coordinates and radius must be positive Unreal centimetres')
        self._set_scene(scene)
        key=(tuple(anchor),tuple(center),radius,self._scene_digest)
        reused=self._last_payload is not None and self._last_payload[0]==key
        if reused:
            _,native,report=self._last_payload;report=dict(report)
        else:
            ground,terrain_components=self.terrain(center,radius);objects,info=self.static(center,radius,scene,_prepared=True)
            triangles=np.concatenate([ground,objects]);native=((triangles-anchor)[:,:,[0,2,1]]*.01).astype('<f4');native[:,:,2]*=-1
            cross=np.cross(native[:,1]-native[:,0],native[:,2]-native[:,0]);keep=(cross*cross).sum(axis=1)>1e-16
            degenerate=int((~keep).sum());native=np.ascontiguousarray(native[keep])
            if not 0<len(native)<=self.MAX_TRIANGLES:
                raise ValueError(f'Requested {radius*.01:g} m collision radius contains {len(native):,} triangles; supported count is 1..{self.MAX_TRIANGLES:,}. Radius was not reduced.')
            if not np.isfinite(native).all():raise ValueError('Collision contains non-finite coordinates')
            report={'terrain_triangles':len(ground),'terrain_components':terrain_components,'object_triangles':len(objects),
                    **info,'degenerate_removed':degenerate,'total':len(native),'radius_m':radius*.01,
                    'anchor':anchor.tolist(),'center':center.tolist(),'coverage_min_xy':(center[:2]-radius).tolist(),
                    'coverage_max_xy':(center[:2]+radius).tolist(),'missing_geometry_count':len(info['missing_geometry'])}
            report['complete']=not (info['missing_geometry'] or info['scene_errors'] or info['unsupported_shapes'] or info['unresolved_empty_geometry_instances'])
            self._last_payload=(key,native,dict(report))
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.tmp')
        with temp.open('wb') as f:f.write(struct.pack('<4sI',b'S3T1',len(native)));f.write(native.tobytes())
        os.replace(temp,path)
        report['build_seconds']=time.perf_counter()-started;report['reused_area']=reused
        report['index_load_seconds']=self.load_seconds;report['index_rebuilt']=self.index_rebuilt
        return report


_default_cache=None


def _cache():
    global _default_cache
    if _default_cache is None or _default_cache.root!=Path(ROOT):_default_cache=MapCache(ROOT)
    return _default_cache


def terrain(center,radius):
    return _cache().terrain(center,radius)[0]


def static(center,radius,scene=None):
    triangles,info=_cache().static(center,radius,scene)
    return triangles,info['omitted_shapes']


def build(anchor,path,radius=25000,scene=None,center=None):
    return _cache().build(anchor,path,radius,scene,center)

if __name__=='__main__':
    meta=json.loads((ROOT.parent/'skate-mailbox/map-import-metadata.json').read_text())
    scene_path=ROOT.parent/'skate-mailbox/scene-collision.json'
    scene=json.loads(scene_path.read_text()) if scene_path.exists() else None
    report=build(meta['position'],ROOT/'test-area.triangles',scene=scene)
    print(json.dumps(report,indent=2))
