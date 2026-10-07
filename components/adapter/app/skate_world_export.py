"""Persistent owned world index for the native collision provider.

S3W1 keeps local mesh triangles, instance transforms and original terrain grids.
No world-sized triangle expansion and no session/anchor-dependent output.
"""
from pathlib import Path
import argparse, hashlib, json, math, os, shutil, time
import numpy as np
from skate_map_cache import MapCache, ROOT, trace_policy, mesh_id, package_path, matrix
from skate_authored_gate import validate_scene_policy,live_included

INSTANCE_DTYPE=np.dtype([('geometry','<u4'),('package','<u4'),('r','<f8',(3,3)),
                        ('v','<f8',(3,)),('lo','<f8',(3,)),('hi','<f8',(3,))])
EXPORT_REVISION=1


def authoritative_inputs(cache,default):
    """Ignore extra live meshes when deciding whether the baked world changed.

    Existing S3W1 files still identify and checksum their complete binary data.
    This separate input identity permits reusing that immutable generation when
    a later MapCache index contains additional, unrelated live mesh entries.
    """
    referenced={cache.geom_meta[int(i)]['id'] for i in np.unique(cache.instances['geometry'])}
    sources=[]
    for row in cache.metadata['sources']:
        path=Path(row[0])
        if path.parent.name=='geometry' and path.stem not in referenced:continue
        sources.append(row)
    mapping=cache.root/'Mappings.usmap'
    return {'schema':cache.SCHEMA,'export_revision':EXPORT_REVISION,'default_shape_complexity':default,
            'sources':sources,'mapping_sha256':file_hash(mapping) if mapping.exists() else None,
            'authored_policy':cache.metadata.get('authored_policy')}


def file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def _relative(root, path):
    return Path(path).relative_to(root).as_posix()


def _complete(cache, default):
    failures=[];empty_reasons={}
    if cache.metadata.get('missing_geometry'):failures.append({'missing_geometry':cache.metadata['missing_geometry']})
    for meta in cache.geom_meta:
        if meta['unsupported']:failures.append({'id':meta['id'],'path':meta['path'],'unsupported':meta['unsupported']})
        if meta['count']:continue
        evidence=meta.get('empty_evidence')
        effective=default if evidence and evidence['trace']==0 else evidence['trace'] if evidence else None
        ignored=evidence and (evidence['reason']=='no_cooked_physics_shapes' or effective in (1,2))
        if not ignored:failures.append({'id':meta['id'],'path':meta['path'],'unresolved_empty':evidence})
        else:empty_reasons[evidence['reason']]=empty_reasons.get(evidence['reason'],0)+1
    if failures:raise ValueError('Whole-world source completeness failed: '+json.dumps(failures))
    return {'complete':True,'missing_geometry_count':0,'unsupported_meshes':0,'unresolved_empty_meshes':0,
            'verified_default_shape_complexity':default,'ignored_empty_meshes':sum(empty_reasons.values()),
            'empty_reasons':empty_reasons,'authored_policy':cache.metadata.get('authored_policy')}


def _validate(cache):
    if cache.instances.dtype!=INSTANCE_DTYPE or INSTANCE_DTYPE.itemsize!=152:
        raise ValueError('Instance table does not match the S3W1 packed152-byte layout')
    if cache.local_triangles.ndim!=3 or cache.local_triangles.shape[1:]!=(3,3):
        raise ValueError('Geometry table does not contain Nx3x3 triangles')
    if not np.isfinite(cache.local_triangles).all():raise ValueError('Non-finite local geometry')
    offset=0
    for meta in cache.geom_meta:
        if meta['offset']!=offset or not isinstance(meta['count'],int) or meta['count']<0:
            raise ValueError('Non-contiguous geometry offsets or invalid triangle count')
        offset+=meta['count']
    if offset!=len(cache.local_triangles):raise ValueError('Geometry metadata/payload count mismatch')
    instances=cache.instances
    if len(instances) and (instances['geometry'].max()>=len(cache.geom_meta) or instances['package'].max()>=len(cache.packages)):
        raise ValueError('Out-of-range geometry/package reference')
    for name in ('r','v','lo','hi'):
        if not np.isfinite(instances[name]).all():raise ValueError('Non-finite instance '+name)
    if np.any(instances['lo']>instances['hi']):raise ValueError('Inverted instance bounds')


def export_world(output, *, cache=None, physics_settings=None):
    """Export one self-contained immutable world generation; output is a folder.

    physics_settings must come from the live read-only engine CDO probe. There
    is no assumed project default. Session anchor/live replacements are separate.
    """
    started=time.perf_counter();cache=cache or MapCache()
    settings=physics_settings or {};default=trace_policy(settings.get('engine_default'))
    if default not in (1,2,3):raise ValueError('Verified engine default collision policy is required')
    completeness=_complete(cache,default);_validate(cache)
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    terrain_sources=[];total_terrain_bytes=0
    for meta in cache.terrain_meta:
        n=meta['n'];folder=cache.root/meta['folder']
        if not isinstance(n,int) or n<2:raise ValueError('Invalid terrain grid size')
        paths=[folder/(meta['file']+'.points'),folder/(meta['file']+'.visibility')]
        expected=[n*n*12,n*n]
        for path,size in zip(paths,expected):
            if path.stat().st_size!=size:raise ValueError('Terrain source size mismatch: '+str(path))
        total_terrain_bytes+=sum(expected);terrain_sources.append(paths)
    expected_bytes=cache.local_triangles.size*8+len(cache.instances)*152+total_terrain_bytes
    # This identity tracks the complete immutable source generation and policy;
    # each exported binary additionally has an exact-byte SHA256.
    source_fingerprint=hashlib.sha256(json.dumps({'schema':cache.SCHEMA,'sources':cache.metadata['sources'],
        'default_shape_complexity':default,'authored_policy':cache.metadata.get('authored_policy')},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    target=output/'manifest.json'
    if target.exists():
        previous=json.loads(target.read_text())
        if previous.get('source_fingerprint')!=source_fingerprint:raise ValueError('Output already contains a different world generation')
        validate_export(target)
        return previous
    if shutil.disk_usage(output).free<expected_bytes+256*1024*1024:
        raise ValueError(f'Not enough free space for {expected_bytes:,} bytes plus256MiB reserve')
    files=[]
    def finish(path,expected):
        if path.stat().st_size!=expected:raise ValueError('Export byte count mismatch: '+str(path))
        record={'path':_relative(output,path),'bytes':expected,'sha256':file_hash(path)};files.append(record);return record
    def write_array(name,source,dtype):
        path=output/name;temp=path.with_suffix(path.suffix+'.tmp')
        np.asarray(source,dtype=dtype).tofile(temp);os.replace(temp,path)
        return finish(path,source.size*np.dtype(dtype).itemsize)
    geometry_file=write_array('geometry.f64',cache.local_triangles,'<f8')
    instance_file=write_array('instances.bin',cache.instances,INSTANCE_DTYPE)
    terrain=[];terrain_triangles=0;terrain_quad_start=0
    terrain_dir=output/'terrain';terrain_dir.mkdir(exist_ok=True)
    for index,(meta,paths) in enumerate(zip(cache.terrain_meta,terrain_sources)):
        n=meta['n'];points=np.memmap(paths[0],dtype='<f4',mode='r',shape=(n,n,3))
        if not np.isfinite(points).all():raise ValueError('Non-finite terrain points: '+str(paths[0]))
        vis=np.memmap(paths[1],dtype='u1',mode='r',shape=(n,n))
        keep=np.maximum.reduce([vis[:-1,:-1],vis[:-1,1:],vis[1:,1:],vis[1:,:-1]])<170
        count=int(np.count_nonzero(keep))*2
        entry=dict(meta)
        for key,source,suffix in zip(('points','visibility'),paths,('points','visibility')):
            path=terrain_dir/f'{index:04d}.{suffix}';temp=path.with_suffix(path.suffix+'.tmp')
            shutil.copyfile(source,temp);os.replace(temp,path);entry[key]=finish(path,source.stat().st_size)
        entry['triangle_start']=terrain_triangles;entry['triangle_count']=count
        entry['quad_start']=terrain_quad_start
        terrain_triangles+=count;terrain_quad_start+=(n-1)*(n-1);terrain.append(entry)
    static_triangles=sum(int(meta['count'])*int(count) for meta,count in zip(cache.geom_meta,
        np.bincount(cache.instances['geometry'],minlength=len(cache.geom_meta))))
    manifest={'magic':'S3W1','schema':1,'coordinate_system':'Unreal centimetres XYZ','byte_order':'little',
              'source_fingerprint':source_fingerprint,'source_cache_schema':cache.SCHEMA,
              'physicsSettings':settings,'default_shape_complexity':default,'completeness':completeness,
              'geometry':cache.geom_meta,'geometry_file':geometry_file,'geometry_triangle_count':len(cache.local_triangles),
              'packages':cache.packages,'instance_file':instance_file,'instance_count':len(cache.instances),'instance_stride':152,
              'instance_layout':'u32 geometry,u32 package,9f64 row-major r,3f64 v,3f64 lo,3f64 hi',
              'terrain':terrain,'terrain_triangle_count':terrain_triangles,'static_triangle_count':static_triangles,
              'triangle_order':'terrain component order: all visible abc then all visible acd; static instance row order then local triangle order',
              'visibility_hole_threshold':170,'mirrored_winding':'swap triangle vertices1and2 for mirrored terrain or negative instance determinant',
              'files':files,'binary_bytes':sum(f['bytes'] for f in files),'export_seconds':time.perf_counter()-started}
    temp=target.with_suffix('.json.tmp');temp.write_text(json.dumps(manifest,separators=(',',':')));os.replace(temp,target)
    validate_export(target)
    return manifest


def validate_export(manifest_path):
    """Check the exact self-contained files before making a generation usable."""
    path=Path(manifest_path).resolve();root=path.parent;manifest=json.loads(path.read_text())
    if manifest.get('magic')!='S3W1' or manifest.get('schema')!=1 or not manifest.get('completeness',{}).get('complete'):
        raise ValueError('Invalid/incomplete S3W1 manifest')
    for record in manifest['files']:
        source=(root/record['path']).resolve()
        if not source.is_relative_to(root):raise ValueError('S3W1 file escapes cache directory')
        if source.stat().st_size!=record['bytes'] or file_hash(source)!=record['sha256']:
            raise ValueError('S3W1 checksum/length mismatch: '+record['path'])
    if manifest['geometry_file']['bytes']!=manifest['geometry_triangle_count']*72:
        raise ValueError('S3W1 geometry count mismatch')
    if manifest['instance_stride']!=152 or manifest['instance_file']['bytes']!=manifest['instance_count']*152:
        raise ValueError('S3W1 instance count mismatch')
    for terrain in manifest['terrain']:
        if terrain['points']['bytes']!=terrain['n']**2*12 or terrain['visibility']['bytes']!=terrain['n']**2:
            raise ValueError('S3W1 terrain count mismatch')
    return manifest


def export_overlay(output, *, base, cache, scene):
    """An authoritative observed-package snapshot; never changes the base cache."""
    started=time.perf_counter();output=Path(output).resolve()
    if scene.get('errors') or scene.get('complete') is False:raise ValueError('Live scene inventory is incomplete')
    if not isinstance(scene.get('packages'),list):raise ValueError('Whole-world overlays require explicit observed packages')
    if any(not isinstance(p,str) or not p for p in scene['packages']):raise ValueError('Invalid observed package')
    observed=sorted(set(package_path(p) for p in scene['packages']))
    default=trace_policy(scene.get('physicsSettings',{}).get('engine_default'))
    if default!=base['default_shape_complexity']:raise ValueError('Live project collision policy differs from base world')
    policy=cache.authored.policy if cache.authored else None
    if policy is not None:validate_scene_policy(scene,policy)
    if base.get('completeness',{}).get('authored_policy')!=cache.metadata.get('authored_policy'):
        raise ValueError('Live and base authored collision policies differ')
    base_ids={meta['id']:i for i,meta in enumerate(base['geometry'])}
    used={};items=[];skipped=0;unresolved=[]
    for item in scene.get('objects',[]):
        package=package_path(item['name'])
        if package not in observed:raise ValueError('Live object package was not declared observed: '+package)
        if not live_included(item,policy):
            skipped+=1;continue
        key=cache.live_geometry_id(item);local,meta=cache._geometry(key)
        if meta is None:raise ValueError('Live collision mesh is missing: '+item.get('mesh',key))
        evidence=meta.get('empty_evidence');effective=default if evidence and evidence['trace']==0 else evidence['trace'] if evidence else None
        if meta['unsupported'] or (not meta['count'] and not(evidence and(evidence['reason']=='no_cooked_physics_shapes' or effective in (1,2)))):
            unresolved.append({'id':key,'mesh':meta['path'],'unsupported':meta['unsupported'],'empty_evidence':evidence})
        used[key]=(local,meta)
        for transform in item.get('instances',[item.get('transform')]):
            if transform is None:raise ValueError('Live instance transform is missing')
            r,v=matrix(transform)
            if not np.isfinite(r).all() or not np.isfinite(v).all():raise ValueError('Non-finite live instance transform')
            if meta['bounds']:
                lo,hi=np.asarray(meta['bounds']);middle=((lo+hi)*.5)@r.T+v;half=np.abs(r)@((hi-lo)*.5);lo,hi=middle-half,middle+half
            else:lo=hi=v
            items.append((key,package,r,v,lo,hi))
    if unresolved:raise ValueError('Live collision geometry is unresolved: '+json.dumps(unresolved))
    extras=[];parts=[];offset=0;ids=dict(base_ids)
    for key in sorted(set(used)-set(base_ids)):
        local,meta=used[key];entry=dict(meta);entry['offset']=offset
        extras.append(entry);parts.append(local);ids[key]=len(base_ids)+len(extras)-1;offset+=len(local)
    local=np.concatenate(parts) if parts else np.empty((0,3,3),dtype='<f8')
    packages=list(base['packages']);package_ids={p:i for i,p in enumerate(packages)}
    for p in observed:
        if p not in package_ids:package_ids[p]=len(packages);packages.append(p)
    instances=np.array([(ids[key],package_ids[p],r,v,lo,hi) for key,p,r,v,lo,hi in items],dtype=INSTANCE_DTYPE)
    total=sum(used[key][1]['count'] for key,*_ in items)
    output.mkdir(parents=True,exist_ok=True);files=[]
    for name,array in [('geometry.f64',np.asarray(local,dtype='<f8')),('instances.bin',instances)]:
        path=output/name;temporary=path.with_suffix(path.suffix+'.tmp');array.tofile(temporary);os.replace(temporary,path)
        files.append({'path':name,'bytes':path.stat().st_size,'sha256':file_hash(path)})
    # Revision/timing/world-session metadata does not change collision geometry.
    # Identical observations can reuse the native view and its expensive rails.
    identity={'base':base['source_fingerprint'],'packages':observed,'instance_packages':packages,
              'geometry':extras,'files':files,'default_shape_complexity':default}
    fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    manifest={'magic':'S3O1','schema':1,'base_fingerprint':base['source_fingerprint'],'source_fingerprint':fingerprint,
              'generation':scene.get('generation'),'scene_revision':scene.get('revision'),'packages':observed,'instance_packages':packages,
              'geometry_base_count':len(base_ids),'geometry':extras,'geometry_file':files[0],'instance_file':files[1],
              'instance_count':len(instances),'instance_stride':152,'triangle_count':int(total),
              'default_shape_complexity':default,'completeness':{'complete':True,'missing_geometry_count':0,'unsupported_meshes':0,'unresolved_empty_meshes':0},
              'skipped_disabled_components':skipped,'files':files,'binary_bytes':sum(f['bytes'] for f in files),'export_seconds':time.perf_counter()-started}
    path=output/'manifest.json';temporary=path.with_suffix('.json.tmp');temporary.write_text(json.dumps(manifest,separators=(',',':')));os.replace(temporary,path)
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--scene',type=Path,required=True);args=parser.parse_args()
    scene=json.loads(args.scene.read_text());settings=scene.get('physicsSettings',{})
    report=export_world(args.output,physics_settings=settings)
    print(json.dumps({'manifest':str((args.output/'manifest.json').resolve()),'bytes':report['binary_bytes'],
                      'geometry_triangles':report['geometry_triangle_count'],'instances':report['instance_count'],
                      'terrain_triangles':report['terrain_triangle_count'],'static_triangles':report['static_triangle_count'],
                      'seconds':report['export_seconds'],'source_fingerprint':report['source_fingerprint']}))
