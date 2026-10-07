"""Optional owned building metadata; never replaces the prepared base map."""
from pathlib import Path
import hashlib,json,os,subprocess,uuid
from skate_preflight import (PreflightError,check_game,check_map,contained,digest,read_json,
                             map_preparation_identity,check_building_catalogue)
from skate_process import offline_maintenance

PRODUCT='DragonwildsSkateBuildings'
MARKER='building-supplement.json'
INVENTORY='notices/building-exporter-inventory.json'

def _plain(path):
    path=Path(os.path.abspath(path))
    if path.is_symlink() or path.resolve()!=path or (hasattr(path,'is_junction') and path.is_junction()):
        raise PreflightError('Building setup refuses linked paths: '+path.name)
    return path

def _file(root,record,label):
    if (not isinstance(record,dict) or type(record.get('bytes')) is not int or record['bytes']<=0
            or not isinstance(record.get('sha256'),str) or len(record['sha256'])!=64
            or any(c not in '0123456789abcdef' for c in record['sha256'])):
        raise PreflightError('Invalid pinned '+label)
    contained(root,record.get('path'))
    path=_plain(Path(root)/record['path'])
    if not path.is_file() or path.stat().st_size!=record['bytes'] or digest(path)!=record['sha256']:
        raise PreflightError('Pinned '+label+' is missing or changed')
    return path

def _release_inputs(paths,manifest):
    """Resolve pins from the checked release, not the captured scene or config."""
    package=_plain(Path(paths['root']).parent)
    if read_json(package/'release-manifest.json','Release manifest')!=manifest:
        raise PreflightError('Building setup release manifest changed')
    if manifest.get('schema')!=1 or manifest.get('product')!='DragonwildsSkate':
        raise PreflightError('Unsupported building release manifest')
    check_game(paths['game_root'],manifest.get('compatibility',{}))
    records={row['path']:row for row in manifest['files']}
    runtime=manifest.get('install',{}).get('runtime',{})
    selected={}
    for key in ('building_export','building_reader_library','building_rule'):
        name=runtime.get(key)
        if name not in records or key not in paths:
            raise PreflightError('This release has no complete building support: '+key)
        file=_file(package,records[name],key)
        if file!=_plain(paths[key]):raise PreflightError('Building runtime path is outside its release pin: '+key)
        selected[key]=file
    if selected['building_export'].suffix.lower()!='.exe' or selected['building_reader_library'].suffix.lower()!='.dll':
        raise PreflightError('Unsupported building runtime file type')
    if INVENTORY not in records:raise PreflightError('Building exporter inventory is not part of this release')
    inventory_file=_file(package,records[INVENTORY],'building exporter inventory')
    inventory=read_json(inventory_file,'Building exporter inventory')
    if inventory.get('schema')!=1 or not isinstance(inventory.get('files'),list) or not inventory['files']:
        raise PreflightError('Invalid building exporter inventory')
    exporter_root=selected['building_export'].parent
    if exporter_root==Path(paths['map_export']).parent:
        raise PreflightError('Supplement exporter must be separate from the prepared map exporter')
    expected=set()
    for row in inventory['files']:
        file=_file(exporter_root,row,'building exporter dependency')
        name=file.relative_to(package).as_posix()
        if file in expected or records.get(name)!=dict(row,path=name):
            raise PreflightError('Building exporter inventory differs from release file records')
        expected.add(file)
    actual=set()
    for file in exporter_root.rglob('*'):
        _plain(file)
        if file.is_file():actual.add(file)
    if actual!=expected or selected['building_export'] not in expected:
        raise PreflightError('Building exporter has missing or additional files')
    game_sha=manifest['compatibility']['game_exe_sha256']
    rule=read_json(selected['building_rule'],'Building collision rule')
    if (not isinstance(rule,dict) or rule.get('schema')!='S3BUILDINGRULE1' or rule.get('verified') is not True
            or rule.get('game_exe_sha256')!=game_sha
            or rule.get('transform_policy')!='entity_local_then_piece_ftransform'
            or not isinstance(rule.get('evidence'),list) or not rule['evidence']
            or any(not isinstance(s,str) or not s for s in rule['evidence'])
            or any(not isinstance(rule.get(k),str) or not rule[k] for k in ('selected_tag','active_profile','inactive_profile'))):
        raise PreflightError('Building collision rule is not verified for this release')
    return {**selected,'game_exe_sha256':game_sha,
            'exporter_inventory_sha256':records[INVENTORY]['sha256'],
            'rule_sha256':records[runtime['building_rule']]['sha256']}

def _base_inputs(paths,manifest,base_fingerprint):
    root=_plain(paths['map_data'])
    if root!=_plain(Path(paths['game_root'])/'DragonwildsSkateData/map-data'):
        raise PreflightError('Building setup requires the installed private map-data directory')
    pointer=read_json(_plain(root/'compact-world-base.json'),'Prepared base map')
    state=read_json(_plain(root/'setup-state.json'),'Owned map setup')
    owner=state.get('identity',{})
    if (not isinstance(pointer,dict) or pointer.get('schema')!=1 or pointer.get('source_fingerprint')!=base_fingerprint
            or not isinstance(base_fingerprint,str) or len(base_fingerprint)!=64
            or any(c not in '0123456789abcdef' for c in base_fingerprint)
            or state.get('ready') is not True or owner.get('product')!='DragonwildsSkateSetup'
            or owner.get('schema')!=1 or owner.get('target')!=str(root)
            or owner.get('compatibility')!=manifest['compatibility']
            or pointer.get('preparation',{}).get('compatibility')!=manifest['compatibility']):
        raise PreflightError('Building supplement needs the matching complete owned base map')
    mapping=_plain(root/'Mappings.usmap')
    mapping_sha=digest(mapping)
    if mapping_sha!=owner.get('mapping_sha256'):
        raise PreflightError('Owned map mappings changed')
    return root,mapping,mapping_sha

def _binding(release,base_fingerprint,mapping_sha):
    return {'schema':1,'product':PRODUCT,'base_fingerprint':base_fingerprint,
            'game_exe_sha256':release['game_exe_sha256'],'mapping_sha256':mapping_sha,
            'exporter_inventory_sha256':release['exporter_inventory_sha256'],'rule_sha256':release['rule_sha256']}

def _catalogue(root,marker,mapping_sha):
    record=marker.get('catalogue',{})
    expected='building-catalogues/'+str(record.get('sha256'))+'/building-catalogue.json'
    if record.get('path')!=expected:raise PreflightError('Building catalogue has no owned content-addressed path')
    file=_file(root,record,'building catalogue')
    if file.stat().st_size>128*1024*1024:raise PreflightError('Building catalogue exceeds size bound')
    if check_building_catalogue(file)['mapping_sha256']!=mapping_sha:
        raise PreflightError('Building catalogue belongs to different mappings')
    return file

def verify_building_supplement(paths,manifest,base_fingerprint):
    release=_release_inputs(paths,manifest)
    root,mapping,mapping_sha=_base_inputs(paths,manifest,base_fingerprint)
    marker=read_json(_plain(root/MARKER),'Building metadata; run prepare-buildings offline')
    expected=_binding(release,base_fingerprint,mapping_sha)
    if not isinstance(marker,dict) or {k:marker.get(k) for k in expected}!=expected:
        raise PreflightError('Building metadata is from different inputs; run prepare-buildings offline')
    catalogue=_catalogue(root,marker,mapping_sha)
    return {'catalogue_path':catalogue,'rule_path':release['building_rule'],
            'game_exe_sha256':release['game_exe_sha256'],'catalogue_sha256':marker['catalogue']['sha256'],
            'rule_sha256':release['rule_sha256'],'supplement_base_fingerprint':base_fingerprint,
            'mapping_path':mapping,'supplement_mapping_sha256':mapping_sha}

def _atomic_json(path,value):
    path=_plain(path);temporary=_plain(path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp'))
    try:
        with temporary.open('xb') as stream:
            stream.write((json.dumps(value,indent=2)+'\n').encode());stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()

def _run(executable,arguments,log):
    with log.open('x',encoding='utf8') as stream:
        result=subprocess.run([str(executable),*map(str,arguments)],stdin=subprocess.DEVNULL,
                              stdout=stream,stderr=subprocess.STDOUT,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=False)
    if result.returncode:raise PreflightError('Building export failed; see '+str(log))

def prepare_buildings(paths,manifest,*,source=None,runner=_run):
    """Export or import only a verified supplement. Keep every base/cache byte."""
    with offline_maintenance(paths):
        release=_release_inputs(paths,manifest)
        world=check_map(paths['map_data'],expected_preparation=map_preparation_identity(paths['root'],paths,manifest['compatibility']),require_grind=False)
        base=world['map_fingerprint'];root,mapping,mapping_sha=_base_inputs(paths,manifest,base)
        binding=_binding(release,base,mapping_sha);marker_file=_plain(root/MARKER)
        before=marker_file.read_bytes() if marker_file.exists() else None
        if before is not None:
            previous=read_json(marker_file,'Existing building supplement')
            if not isinstance(previous,dict) or previous.get('schema')!=1 or previous.get('product')!=PRODUCT:
                raise PreflightError('Refusing to replace an unknown building supplement')
            if {k:previous.get(k) for k in binding}==binding:
                _catalogue(root,previous,mapping_sha)
                return {'status':'already_prepared','base_fingerprint':base,'supplement':str(marker_file)}
        if source is not None:
            source=_plain(source)
            imported=read_json(_plain(source/MARKER),'Imported verified building supplement')
            if not isinstance(imported,dict) or {k:imported.get(k) for k in binding}!=binding:
                raise PreflightError('Imported building supplement has different pinned inputs')
            catalogue=_catalogue(source,imported,mapping_sha)
        else:
            stage=_plain(root/('building-export-stage-'+uuid.uuid4().hex));stage.mkdir()
            _atomic_json(stage/'owner.json',binding)
            runner(release['building_export'],[Path(paths['game_root'])/'RSDragonwilds/Content/Paks',stage,mapping,'/Game/','buildings'],stage/'export.log')
            catalogue=_plain(stage/'building-catalogue.json')
            if catalogue.stat().st_size>128*1024*1024:raise PreflightError('Building catalogue exceeds size bound')
            if check_building_catalogue(catalogue)['mapping_sha256']!=mapping_sha:
                raise PreflightError('Exporter returned a catalogue for different mappings')
        raw=catalogue.read_bytes();sha=hashlib.sha256(raw).hexdigest()
        if source is not None and sha!=imported['catalogue']['sha256']:
            raise PreflightError('Imported catalogue changed while being copied')
        relative='building-catalogues/'+sha+'/building-catalogue.json'
        destination=_plain(root/relative);destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists():
            if destination.read_bytes()!=raw:raise PreflightError('Owned catalogue content changed')
        else:
            temporary=_plain(destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.tmp'))
            try:
                with temporary.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
                if destination.exists():raise PreflightError('Building catalogue destination appeared during publication')
                os.replace(temporary,destination)
            finally:
                if temporary.exists():temporary.unlink()
        # Revalidate after export/copy, before the single publication operation.
        if (_release_inputs(paths,manifest)!=release or _base_inputs(paths,manifest,base)!=(root,mapping,mapping_sha)
                or (marker_file.read_bytes() if marker_file.exists() else None)!=before):
            raise PreflightError('Building setup inputs changed before publication')
        marker={**binding,'catalogue':{'path':relative,'bytes':len(raw),'sha256':sha}}
        _catalogue(root,marker,mapping_sha);_atomic_json(marker_file,marker)
        return {'status':'prepared','base_fingerprint':base,'supplement':str(marker_file),'catalogue':str(destination)}
