"""Reuse exact verified S3O1 overlays without changing map or native identities.

This is an optimization of export only. Callers still supply a fresh complete
inventory, merged with authoritative observations of currently unloaded packages.
"""
from pathlib import Path
import hashlib,json,os
import numpy as np
from skate_authored_gate import live_included,validate_scene_policy
from skate_map_cache import package_path,trace_policy
from skate_world_export import file_hash


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


class OverlayReuseStore:
    def __init__(self,prepared):
        self.root=Path(prepared).resolve()
        self.folder=self.root/'verified-overlays-v1'
        # Changes to decoding/filtering/export semantics invalidate this optional
        # lookup, while the existing S3W1/S3O1 content identities stay untouched.
        source=Path(__file__).resolve().parent
        self.contract={name:file_hash(source/name) for name in
            ('skate_overlay_reuse.py','skate_world_export.py','skate_map_cache.py',
             'skate_authored_gate.py','skate_spline_collision.py')}

    def identity(self,base,world,scene,world_session):
        if not isinstance(world_session,str) or not world_session:
            raise ValueError('Overlay reuse requires a save identity')
        if not isinstance(scene.get('world'),str) or not scene['world']:
            raise ValueError('Overlay reuse requires a world asset')
        if scene.get('complete') is False or scene.get('errors'):
            raise ValueError('Cannot reuse an incomplete live scene')
        if not isinstance(scene.get('packages'),list):
            raise ValueError('Overlay reuse requires observed packages')
        observed=sorted({package_path(p) for p in scene['packages']})
        default=trace_policy(scene.get('physicsSettings',{}).get('engine_default'))
        if default!=base['default_shape_complexity']:
            raise ValueError('Live project collision policy differs from base world')
        policy=world.authored.policy if world.authored else None
        if policy is not None:validate_scene_policy(scene,policy)
        if base.get('completeness',{}).get('authored_policy')!=world.metadata.get('authored_policy'):
            raise ValueError('Live and base authored collision policies differ')
        # Object order can change when UE enumerates components again. Preserve
        # every field (including transforms, enabled state and spline collision)
        # but do not make enumeration order create a different semantic snapshot.
        objects=[];geometry={}
        for item in scene.get('objects',[]):
            if package_path(item['name']) not in observed:
                raise ValueError('Live object belongs to an unobserved package')
            objects.append(_json(item))
            if not live_included(item,policy):continue
            key=world.live_geometry_id(item)
            if key in geometry:continue
            local,meta=world._geometry(key)
            if meta is None:raise ValueError('Live collision mesh is missing: '+str(key))
            # Exact decoded bytes also cover newly exported meshes that were not
            # present in MapCache.metadata when this helper started.
            payload=np.ascontiguousarray(local,dtype='<f8')
            geometry[key]={'metadata':meta,'sha256':hashlib.sha256(memoryview(payload.reshape(-1)).cast('B')).hexdigest()}
        identity={'schema':1,'contract':self.contract,'base':base['source_fingerprint'],
                  'world_session':world_session,'world':scene['world'],
                  'default_shape_complexity':default,'query_policy':scene.get('query_policy'),
                  'authored_policy':world.metadata.get('authored_policy'),
                  'packages':observed,'geometry':geometry}
        digest=hashlib.sha256(_json(identity).encode())
        # Objects are already canonical JSON. Stream their canonical array into
        # the digest instead of encoding those documents a second time as huge
        # escaped strings (the real snapshot contains tens of thousands).
        digest.update(b'\n[')
        for index,item in enumerate(sorted(objects)):
            if index:digest.update(b',')
            digest.update(item.encode())
        digest.update(b']')
        return digest.hexdigest()

    def validate(self,path,base):
        path=Path(path).resolve()
        if not path.is_relative_to(self.root):raise ValueError('Overlay escapes prepared data')
        value=json.loads(path.read_text(encoding='utf-8'))
        if value.get('magic')!='S3O1' or value.get('schema')!=1 or value.get('completeness',{}).get('complete') is not True:
            raise ValueError('Cached overlay is not complete S3O1')
        if value.get('base_fingerprint')!=base['source_fingerprint'] or value.get('default_shape_complexity')!=base['default_shape_complexity']:
            raise ValueError('Cached overlay has a different base or policy')
        if value.get('geometry_base_count')!=len(base['geometry']):
            raise ValueError('Cached overlay base geometry count differs')
        records=value['files']
        if len(records)!=2 or len({r['path'] for r in records})!=2:
            raise ValueError('Cached overlay must declare both exact payloads')
        if value['geometry_file'] not in records or value['instance_file'] not in records or value['geometry_file']==value['instance_file']:
            raise ValueError('Cached overlay payload references disagree')
        for record in records:
            payload=(path.parent/record['path']).resolve()
            if not payload.is_relative_to(path.parent):raise ValueError('Overlay payload escapes its directory')
            if payload.stat().st_size!=record['bytes'] or file_hash(payload)!=record['sha256']:
                raise ValueError('Cached overlay payload checksum/length mismatch')
        if value['geometry_file']['bytes']!=sum(g['count'] for g in value['geometry'])*72:
            raise ValueError('Cached overlay geometry count mismatch')
        if value['instance_stride']!=152 or value['instance_file']['bytes']!=value['instance_count']*152:
            raise ValueError('Cached overlay instance count mismatch')
        if value['binary_bytes']!=sum(r['bytes'] for r in records):
            raise ValueError('Cached overlay binary byte count mismatch')
        inputs={'base':value['base_fingerprint'],'packages':value['packages'],
                'instance_packages':value['instance_packages'],'geometry':value['geometry'],
                'files':records,'default_shape_complexity':value['default_shape_complexity']}
        if hashlib.sha256(_json(inputs).encode()).hexdigest()!=value['source_fingerprint']:
            raise ValueError('Cached overlay content identity mismatch')
        return value

    def select(self,identity,base):
        try:
            target=self.folder/(identity+'.json')
            if not target.exists():return None
            saved=json.loads(target.read_text(encoding='utf-8'))
            if saved.get('schema')!=1 or saved.get('identity')!=identity:
                raise ValueError('Cached overlay lookup identity mismatch')
            path=(self.root/saved['manifest']).resolve()
            if not path.is_relative_to(self.root):raise ValueError('Overlay lookup escapes prepared data')
            if file_hash(path)!=saved['manifest_sha256']:raise ValueError('Cached overlay manifest changed')
            overlay=self.validate(path,base)
            if overlay['source_fingerprint']!=saved['source_fingerprint']:
                raise ValueError('Cached overlay lookup fingerprint mismatch')
            return path,overlay
        except (OSError,ValueError,KeyError,TypeError) as error:
            print('OVERLAY REUSE IGNORED',str(error),flush=True)
            return None

    def remember(self,identity,path,base):
        path=Path(path).resolve();overlay=self.validate(path,base)
        record={'schema':1,'identity':identity,'manifest':path.relative_to(self.root).as_posix(),
                'manifest_sha256':file_hash(path),'source_fingerprint':overlay['source_fingerprint']}
        self.folder.mkdir(parents=True,exist_ok=True)
        target=self.folder/(identity+'.json');temporary=target.with_suffix('.json.tmp')
        temporary.write_text(_json(record),encoding='utf-8');os.replace(temporary,target)
