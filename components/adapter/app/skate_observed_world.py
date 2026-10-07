"""Remember authoritative package observations without treating unload as deletion."""
from pathlib import Path
import copy,hashlib,json,os,re
from skate_map_cache import package_path


class ObservedWorldState:
    def __init__(self,folder):
        self.folder=Path(folder);self.loaded={};self.stamps={}

    @staticmethod
    def _stamp(path):
        try:
            value=path.stat()
            return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)
        except FileNotFoundError:return None

    def preview(self,scene,world_session,base_fingerprint):
        if not isinstance(world_session,str) or not world_session:raise ValueError('A trustworthy world_session is required')
        if not isinstance(base_fingerprint,str) or re.fullmatch('[0-9a-f]{64}',base_fingerprint) is None:
            raise ValueError('A verified base collision fingerprint is required')
        if not isinstance(scene.get('world'),str) or not scene['world']:
            raise ValueError('The observed world asset path is required')
        if scene.get('errors') or scene.get('complete') is False:raise ValueError('Cannot remember an incomplete package inventory')
        if not isinstance(scene.get('packages'),list):raise ValueError('Explicit observed packages are required')
        key=hashlib.sha256(world_session.encode()).hexdigest();path=self.folder/(key+'.json')
        stamp=self._stamp(path);old=self.loaded.get(key)
        if old is None or self.stamps.get(key)!=stamp:
            # Another helper, setup or manual cache repair may have replaced the
            # file. Never let a RAM hit hide that change or resurrect its old data.
            old=json.loads(path.read_text()) if stamp is not None else None
            if self._stamp(path)!=stamp:raise ValueError('Saved package observations changed while reading; retry preparation')
        existed=old is not None and stamp is not None
        if old is None:old={'schema':2,'world_session':world_session,'world':scene['world'],
                          'base_fingerprint':base_fingerprint,'packages':{}}
        if old.get('schema')!=2 or old.get('base_fingerprint')!=base_fingerprint:
            raise ValueError('Saved package observations belong to a different map cache version; '
                             'rebuild this save\'s observation cache before applying live collision: '+str(path))
        if old.get('world_session')!=world_session or old.get('world')!=scene['world']:
            raise ValueError('World session identity does not match saved package observations')
        state={**old,'packages':dict(old['packages'])};observed={package_path(p):[] for p in scene['packages']}
        for item in scene.get('objects',[]):
            package=package_path(item['name'])
            if package not in observed:raise ValueError('Live object belongs to an unobserved package: '+package)
            observed[package].append(item)
        changed=[]
        for package,objects in observed.items():
            if state['packages'].get(package)!=objects:
                changed.append(package)
                # Own the remembered values: callers may mutate a scene object
                # later, which must not silently mutate the comparison baseline.
                state['packages'][package]=copy.deepcopy(objects)
            # An explicitly observed empty package deletes its previous objects.
            # A package absent from this snapshot retains its prior observation.
        state['generation']=scene.get('generation');state['revision']=scene.get('revision')
        merged={**scene,'packages':sorted(state['packages']),
                'objects':[item for package in sorted(state['packages']) for item in state['packages'][package]],
                'world_session':world_session,'changed_observed_packages':sorted(changed)}
        return merged,(key,path,state,stamp,bool(changed) or not existed)

    def commit(self,pending):
        key,path,state,expected,changed=pending
        if self._stamp(path)!=expected:
            raise ValueError('Saved package observations changed during preparation; retry before committing')
        if not changed:
            # Generation/revision are diagnostic only. Keep the fresh RAM state
            # without rewriting an identical multi-megabyte package inventory.
            self.loaded[key]=state;self.stamps[key]=expected;return False
        self.folder.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.json.tmp');temporary.write_text(json.dumps(state,separators=(',',':')))
        if self._stamp(path)!=expected:
            temporary.unlink(missing_ok=True)
            raise ValueError('Saved package observations changed during commit; retry preparation')
        os.replace(temporary,path);self.loaded[key]=state;self.stamps[key]=self._stamp(path)
        return True
