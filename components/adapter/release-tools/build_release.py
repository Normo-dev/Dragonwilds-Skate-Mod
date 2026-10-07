"""Allowlist-only public staging and audit. This tool never publishes a ZIP.

The input recipe is private build configuration. Its machine paths are never
copied into the release. A supplied binary/source hash identifies each build.
"""
from __future__ import annotations
import argparse
import ast
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
if (Path(__file__).resolve().parent.parent/'skate_preflight.py').is_file():
    sys.path.insert(1, str(Path(__file__).resolve().parent.parent))
elif (Path(__file__).resolve().parent.parent/'app/skate_preflight.py').is_file():
    sys.path.insert(1, str(Path(__file__).resolve().parent.parent/'app'))
from skate_install import InstallError, relative, safe, sha, encoded

class BuildError(ValueError):
    pass

FORBIDDEN_PARTS = {'private', 'mailbox', 'skate-mailbox', 'map-data', 'dragonwilds-map-data',
    'dragonwilds-map-authored-v1', 'logs', 'savegames', '.git', '.codex', 'codex-primary-runtime',
    'deployment-backups', 'converter', '__pycache__'}
FORBIDDEN_SUFFIXES = {'.iso', '.pak', '.utoc', '.ucas', '.usmap', '.glb', '.gltf', '.rwcmset',
    '.stategraph', '.pdb', '.dmp', '.sav', '.points', '.visibility', '.f64', '.npy', '.npz'}
FORBIDDEN_NAMES = {'geometry.npy', 'instances.bin', 'scene-collision.json', 'runtime.json',
    'skate_config.lua', 'mount-probe.json', 'spell-probe.json', 'map-import-metadata.json',
    'building-catalogue.json', 'building-supplement.json', 'oodle-data-shared.dll'}
PRIVATE_PATH = re.compile(rb'(?i)(?:[a-z]:[\\/]+(?:Users[\\/]+(?![\\/]|runneradmin[\\/])|(?:SteamLibrary|CodexData)[\\/])|'
    rb'(?:/home/|/Users/)[^/\s]+/(?:Documents/Codex|\.cache/codex)|'
    rb'[\\/](?:codex-primary-runtime|https-github-com-rehan-remade-universal)[\\/])')
SHA = re.compile(r'^[0-9a-f]{64}$')
# These unchanged public NuGet binaries contain their upstream authors' PDB
# build paths. Exceptions bind the exact bytes and destination, never a pattern.
UPSTREAM_BINARY_PATHS = {
    'exporter/NAudio.Core.dll':'fcf493fc47a2f478a65303886b975fbdbf714cbb1f2d79f7fce97e4bb16b01a8',
    'exporter/OggVorbisEncoder.dll':'5c3c602bed65fa22915d4d56fe62c3ed584dfaf48fd2c31b49519ed211f77ff1',
    'building-exporter/NAudio.Core.dll':'fcf493fc47a2f478a65303886b975fbdbf714cbb1f2d79f7fce97e4bb16b01a8',
    'building-exporter/OggVorbisEncoder.dll':'5c3c602bed65fa22915d4d56fe62c3ed584dfaf48fd2c31b49519ed211f77ff1',
}
PYTHON_SEEDS = ('skate_launcher', 'skate_setup', 'skate_cache_worker')
LUA_GENERATED = {'skate_config'}
LUA_EXTERNAL = {'UEHelpers'}
LUA_DIAGNOSTICS = {'map_import', 'main.original', 'mount_playtest', 'mount_probe',
    'mount_probe_entry', 'spell_playtest', 'mount_control_probe', 'mount_equip_probe', 'spline_probe'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked_file(path, expected=None):
    path = Path(path)
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()) or not path.is_file():
        raise BuildError('Missing or linked input file: ' + path.name)
    if expected is not None and (not SHA.fullmatch(expected) or digest(path) != expected):
        raise BuildError('Input hash mismatch: ' + path.name)
    return path.resolve()


def public_path(name, *, source=False, resource_exceptions=()):
    try:
        relative(name)
    except InstallError as error:
        raise BuildError(str(error)) from error
    p = PurePosixPath(name); lowered = {x.casefold() for x in p.parts}
    if lowered & FORBIDDEN_PARTS:
        raise BuildError('Private or build-only directory in public payload: ' + name)
    if p.name.casefold() in FORBIDDEN_NAMES or p.name.casefold().startswith(('oo2core', 'oodleue')):
        raise BuildError('Private/generated/proprietary file in public payload: ' + name)
    if p.suffix.lower() in FORBIDDEN_SUFFIXES and name not in resource_exceptions:
        raise BuildError('Game data or generated binary fixture in public payload: ' + name)
    if not source and any(x.casefold() in ('tests', 'test', 'benchmarks', 'examples') for x in p.parts):
        raise BuildError('Runtime payload includes test/example files: ' + name)
    if not source and p.name.startswith(('test_', 'check_', 'probe_')):
        raise BuildError('Runtime payload includes a diagnostic entrypoint: ' + name)
    return name


def leak_check(raw, label):
    if PRIVATE_PATH.search(raw) or PRIVATE_PATH.search(raw.replace(b'\x00', b'')):
        normalized=raw.replace(b'\x00',b'').replace(b'\\',b'/').lower()
        personal=os.environ.get('USERPROFILE','').replace('\\','/').lower().encode()
        private=any(b'/'+part+b'/' in normalized for part in (b'codexdata',b'steamlibrary',b'codex-primary-runtime',b'https-github-com-rehan-remade-universal'))
        if not private and not (personal and personal in normalized) and UPSTREAM_BINARY_PATHS.get(label)==sha(raw):
            return
        raise BuildError('Developer path or Codex runtime leak: ' + label)


def audit_zip(raw, label, *, worker=False, allowed_resources=None):
    resources = allowed_resources or {}
    seen=set(); total=0; names=[]
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for item in archive.infolist():
            if item.is_dir(): continue
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise BuildError('Linked source archive entry: '+item.filename)
            name=public_path(item.filename, source=True, resource_exceptions=resources)
            if name.casefold() in seen: raise BuildError('Duplicate archive entry: ' + label + '/' + name)
            seen.add(name.casefold()); total+=item.file_size
            if total>1024**3: raise BuildError('Source archive exceeds the audit size bound')
            content=archive.read(item)
            if name in resources and sha(content)!=resources[name]: raise BuildError('Source resource exception hash mismatch: '+name)
            leak_check(content,label+'/'+name);names.append(name)
        if worker:
            required={'LICENSE','UPSTREAM-README.md','SOURCE-PROVENANCE.json','BUILDING.md','DEPENDENCY-NOTICES.json'}
            if not required<=set(names):raise BuildError('Worker source archive lacks source/license/build provenance')
            provenance=json.loads(archive.read('SOURCE-PROVENANCE.json'))
            paths=[row['path'] for row in provenance.get('files',[])]
            if not paths or len(paths)!=len(set(paths)) or set(paths)|{'SOURCE-PROVENANCE.json'}!=set(names):
                raise BuildError('Worker source inventory does not cover the exact archive')
            for entry in provenance['files']:
                if sha(archive.read(entry['path']))!=entry['sha256']:raise BuildError('Worker source inventory hash mismatch: '+entry['path'])
    return names


def worker_binding(archive,binary_sha256):
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        try:report=json.loads(source.read('SCENE-CACHE-VERIFICATION.json'))
        except KeyError as error:raise BuildError('Worker source lacks final binary/source binding evidence') from error
        if report.get('artifact_sha256')!=binary_sha256:
            raise BuildError('Worker source verification belongs to a different executable')
        rows=json.loads(source.read('SOURCE-PROVENANCE.json'))['files']
        selected=sorted([{'path':r['path'],'sha256':r['sha256']} for r in rows
                         if r['path'].endswith('.rs') or PurePosixPath(r['path']).name in ('Cargo.toml','Cargo.lock')],key=lambda r:r['path'])
        fingerprint=sha(json.dumps(selected,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('utf-8'))
        if report.get('compiled_source_sha256')!=fingerprint:
            raise BuildError('Worker compiled source fingerprint does not match archived Rust/Cargo inputs')
        return report


def python_closure(root, seeds=PYTHON_SEEDS, extra_modules=None):
    root=Path(root).resolve();pending=list(seeds);seen=set();external=set();edges={}
    extra_modules=extra_modules or {}
    while pending:
        name=pending.pop()
        if name in seen:continue
        if not re.fullmatch(r'[A-Za-z_]\w*',name):raise BuildError('Unsupported local Python module: '+name)
        path=Path(extra_modules[name]) if name in extra_modules else safe(root,name+'.py')
        if not path.is_file():raise BuildError('Required Python module is missing: '+name)
        if name.startswith(('test_','check_','probe_')):raise BuildError('Diagnostic Python module reached runtime closure: '+name)
        tree=ast.parse(path.read_text(encoding='utf-8-sig'),filename=name+'.py');seen.add(name);deps=set()
        for node in ast.walk(tree):
            imports=[]
            if isinstance(node,ast.Import):imports=[x.name for x in node.names]
            elif isinstance(node,ast.ImportFrom):
                if node.level:raise BuildError('Relative package import needs an explicit collector: '+name)
                if node.module:imports=[node.module]
            elif isinstance(node,ast.Call) and (getattr(node.func,'id','')=='__import__' or getattr(node.func,'attr','')=='import_module'):
                if not node.args or not isinstance(node.args[0],ast.Constant) or not isinstance(node.args[0].value,str):
                    raise BuildError('Nonliteral dynamic import needs review: '+name+':'+str(node.lineno))
                imports=[node.args[0].value]
            for module in imports:
                first=module.split('.')[0]
                if first in extra_modules or (root/(first+'.py')).is_file():deps.add(first);pending.append(first)
                else:external.add(first)
            # Python worker entrypoints launched by file path are not import statements.
            if isinstance(node,ast.Constant) and isinstance(node.value,str) and re.fullmatch(r'[A-Za-z_]\w*\.py',node.value):
                child=node.value[:-3]
                if child in extra_modules or (root/node.value).is_file():deps.add(child);pending.append(child)
        edges[name]=sorted(deps)
    unexpected=external-set(sys.stdlib_module_names)-{'numpy','PIL'}
    if unexpected:raise BuildError('Unprovided Python dependencies: '+', '.join(sorted(unexpected)))
    return {'modules':sorted(seen),'external':sorted(external),'edges':edges}


def lua_requires(text):
    # Strip comments while preserving quoted strings. Every production require
    # must have a literal module name; generated names need explicit review.
    pattern=r'--\[(=*)\[.*?\]\1\]|--[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*|[^\s]'
    tokens=[m[0] for m in re.finditer(pattern,text,re.S) if not m[0].startswith('--')]
    result=[]
    for i,token in enumerate(tokens):
        if token!='require':continue
        j=i+1
        if j<len(tokens) and tokens[j]=='(':j+=1
        elif j<len(tokens) and tokens[j]==',' and i>0 and tokens[i-1]=='(':
            j+=1  # pcall(require, "module")
        if j>=len(tokens) or tokens[j][0] not in ('"',"'"):
            raise BuildError('Nonliteral Lua require needs review')
        name=tokens[j][1:-1]
        if not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',name):raise BuildError('Unsupported Lua module path: '+name)
        result.append(name)
    return result


def lua_closure(root, bootstrap):
    root=Path(root).resolve();pending=['bridge'];seen=set();edges={};external=set()
    for name in lua_requires(Path(bootstrap).read_text(encoding='utf-8-sig')):pending.append(name)
    while pending:
        name=pending.pop()
        if name in seen:continue
        if name in LUA_GENERATED|LUA_EXTERNAL:external.add(name);continue
        if name in LUA_DIAGNOSTICS or name.endswith(('_playtest','_probe_entry')):
            raise BuildError('Diagnostic Lua module reached runtime closure: '+name)
        path=safe(root,name.replace('.','/')+'.lua')
        if not path.is_file():raise BuildError('Required Lua module is missing: '+name)
        seen.add(name);deps=lua_requires(path.read_text(encoding='utf-8-sig'));edges[name]=sorted(set(deps));pending.extend(deps)
    return {'modules':sorted(seen),'external':sorted(external),'edges':edges}


@dataclass
class Entry:
    destination:str
    data:bytes
    component:str
    source_label:str


class Allowlist:
    def __init__(self):self.entries={};self.identities={}
    def add(self,destination,data,component,source_label,*,source=False):
        public_path(destination,source=source)
        if not isinstance(source_label,str) or not source_label or re.search(r'(^[A-Za-z]:|^/|\\)',source_label):
            raise BuildError('Source provenance labels must be relative descriptions')
        leak_check(source_label.encode(),destination+' source label');leak_check(data,destination)
        if destination.casefold() in self.identities:raise BuildError('Duplicate allowlist destination: '+destination)
        self.identities[destination.casefold()]=destination;self.entries[destination]=Entry(destination,data,component,source_label)
    def file(self,path,destination,component,source_label,expected=None,*,source=False):
        path=checked_file(path,expected);self.add(destination,path.read_bytes(),component,source_label,source=source)
    def inventory(self):
        return [{'path':name,'bytes':len(e.data),'sha256':sha(e.data)} for name,e in sorted(self.entries.items())]
    def source_records(self):
        return [{'path':name,'component':e.component,'source':e.source_label,'sha256':sha(e.data)} for name,e in sorted(self.entries.items())]


def add_python(allow,root,sources_manifest,downloads):
    root=Path(root).resolve();downloads=Path(downloads).resolve()
    document=json.loads(Path(sources_manifest).read_text(encoding='utf-8-sig'));names=[]
    if document.get('schema')!=1 or len(document.get('dependencies',[]))!=3:raise BuildError('Expected explicit Python, NumPy and Pillow archive provenance')
    for item in document['dependencies']:
        filename=relative(item['filename']);archive_path=checked_file(downloads/filename,item['sha256']);is_python=filename.startswith('python-')
        if not (is_python or filename.startswith(('numpy-','pillow-'))):raise BuildError('Unapproved Python runtime dependency')
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if member.is_dir():continue
                relative(member.filename)
                parts=PurePosixPath(member.filename).parts
                if any(p.casefold() in ('tests','test','benchmarks','examples','__pycache__') for p in parts):continue
                if is_python and member.filename.endswith('._pth'):continue
                if member.filename.endswith(('.pyc','.pyo')):continue
                name=member.filename if is_python else 'Lib/site-packages/'+member.filename
                public_path('python/'+name)
                expected=archive.read(member);actual=safe(root,name)
                if not actual.is_file() or sha(actual.read_bytes())!=sha(expected):raise BuildError('Prepared Python runtime differs from its published archive: '+name)
                allow.add('python/'+name,expected,'python-runtime',filename+':'+member.filename)
                names.append(name)
    python_zips=[name for name in names if re.fullmatch(r'python\d+\.zip',name)]
    if len(python_zips)!=1 or 'python.exe' not in names:raise BuildError('Missing embedded Python executable or stdlib archive')
    pth=python_zips[0][:-4]+'._pth'
    allow.add('python/'+pth,(python_zips[0]+'\n.\nLib/site-packages\n../app\nimport site\n').encode(),'python-runtime','generated isolated app search path')
    allow.add('notices/python-sources.json',encoded(document),'python-notices','official archive URLs and checksums')


def source_zip(entries):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,data in sorted(entries.items()):
            public_path(name,source=True);leak_check(data,name)
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,data)
    return out.getvalue()


def audit_package(root):
    root=Path(root).resolve();manifest=json.loads((root/'release-manifest.json').read_text(encoding='utf-8-sig'))
    seen=set();listed=set()
    for row in manifest['files']:
        name=public_path(row['path'],source=row['path'].startswith('sources/'))
        if name.casefold() in seen:raise BuildError('Duplicate manifest path')
        seen.add(name.casefold());listed.add(name)
        path=safe(root,name);raw=path.read_bytes()
        if len(raw)!=row['bytes'] or sha(raw)!=row['sha256']:raise BuildError('Staged file does not match manifest: '+name)
        leak_check(raw,name)
        if name.startswith('sources/') and name.endswith('.zip'):
            audit_zip(raw,name,worker=name=='sources/worker-source.zip')
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if actual!=listed|{'release-manifest.json'}:raise BuildError('Unlisted or missing staged file: '+', '.join(sorted(actual^(listed|{'release-manifest.json'}))))
    leak_check((root/'release-manifest.json').read_bytes(),'release-manifest.json')
    exceptions=[{'path':p,'sha256':s} for p,s in UPSTREAM_BINARY_PATHS.items() if p in listed]
    return {'schema':1,'files':len(listed),'bytes':sum(r['bytes'] for r in manifest['files']),
            'manifest_sha256':digest(root/'release-manifest.json'),'private_path_leaks':0,'reviewed_upstream_binary_path_exceptions':exceptions,
            'unlisted_files':0,'published':False}


def write_stage(allow,manifest,output):
    output=Path(output).resolve()
    if output.exists():raise BuildError('Choose a new empty staging path; existing stages are never overwritten')
    output.mkdir(parents=True)
    try:
        for name,entry in sorted(allow.entries.items()):
            target=safe(output,name);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(entry.data)
        manifest={**manifest,'files':allow.inventory()}
        (output/'release-manifest.json').write_bytes(encoded(manifest))
        return audit_package(output)
    except BaseException:
        # Keep incomplete evidence. No recursive deletion or publish happens.
        raise


def record_file(allow,record,destination,component):
    if set(record)-{'path','sha256','label'} or not {'path','sha256','label'}<=record.keys():
        raise BuildError('Binary/artwork input requires path, sha256 and relative source label')
    allow.file(record['path'],destination,component,record['label'],record['sha256'])


def add_text_tree(allow,root,prefix,component):
    root=Path(root).resolve();count=0
    for path in sorted(root.rglob('*')):
        if path.is_dir():
            if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):
                raise BuildError('Linked notice directory')
            continue
        name=path.relative_to(root).as_posix()
        if path.suffix.lower() not in ('','.md','.txt','.json','.nuspec','.toml','.html','.rst'):
            raise BuildError('Unexpected file type in notice tree: '+name)
        raw=checked_file(path).read_bytes()
        try:raw.decode('utf-8-sig')
        except UnicodeError as error:raise BuildError('Non-text notice: '+name) from error
        allow.add(prefix+'/'+name,raw,component,component+'/'+name);count+=1
    if not count:raise BuildError('Missing required notice tree: '+component)


def add_inventory(allow,root,inventory,prefix,component):
    root=Path(root).resolve();document=json.loads(Path(inventory).read_text(encoding='utf-8-sig'))
    if document.get('schema')!=1 or not document.get('files'):raise BuildError('Missing component byte inventory')
    for row in document['files']:
        name=public_path(row['path']);path=safe(root,name)
        if path.stat().st_size!=row['bytes']:raise BuildError('Component byte count mismatch: '+name)
        allow.file(path,prefix+'/'+name,component,component+'/'+name,row['sha256'])
    allow.add('notices/'+component+'-inventory.json',encoded(document),component+'-notices',component+' designated byte inventory')


def rust_sources(root):
    root=Path(root).resolve();names=['Cargo.toml','Cargo.lock']
    if (root/'build.rs').is_file():names.append('build.rs')
    names.extend(p.relative_to(root).as_posix() for p in sorted((root/'src').rglob('*.rs')))
    if len(names)<3:raise BuildError('Native helper source is missing')
    return {name:checked_file(safe(root,name)).read_bytes() for name in names}


def build(recipe,output):
    """Stage exact reviewed inputs; the recipe itself is deliberately private."""
    if recipe.get('schema')!=1:raise BuildError('Unsupported private build recipe')
    version=recipe.get('version')
    if not isinstance(version,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,79}',version):
        raise BuildError('Version must be a short filename-safe identifier')
    allow=Allowlist();adapter={};runtime={};host=[];loader=[];prerequisites=[]
    app=Path(recipe['app_root']).resolve();lua=Path(recipe['lua_root']).resolve()
    wizard=recipe.get('wizard')
    if wizard is not None and set(wizard)!={'Setup.cmd','setup-wizard.ps1'}:
        raise BuildError('Wizard requires exactly its reviewed launcher and Windows Forms source')
    py=python_closure(app,seeds=PYTHON_SEEDS+(('skate_wizard',) if wizard is not None else ()),
                      extra_modules={'skate_install':recipe['installer']})
    lu=lua_closure(lua,recipe['bootstrap'])
    for module in py['modules']:
        if module=='skate_install':continue  # Bound to the reviewed installer input below.
        name=module+'.py';data=checked_file(app/name).read_bytes()
        allow.add('app/'+name,data,'adapter-python','adapter/app/'+name);adapter['app/'+name]=data
    installer=checked_file(recipe['installer']).read_bytes()
    allow.add('app/skate_install.py',installer,'installer','adapter/app/skate_install.py');adapter['app/skate_install.py']=installer
    if wizard is not None:
        for name,record in wizard.items():
            record_file(allow,record,name,'setup-wizard')
            adapter['wizard/'+name]=allow.entries[name].data
        wizard_tests=Path(wizard['Setup.cmd']['path']).with_name('test_skate_wizard.py')
        adapter['release-tools/wizard/test_skate_wizard.py']=checked_file(wizard_tests).read_bytes()
        adapter['release-tools/wizard/skate_wizard.py']=checked_file(app/'skate_wizard.py').read_bytes()
    bootstrap=checked_file(recipe['bootstrap']).read_bytes()
    allow.add('host/main.lua',bootstrap,'adapter-lua','adapter/host/bootstrap.lua');adapter['host/main.lua']=bootstrap
    host.append({'source':'host/main.lua','destination':'main.lua'})
    for module in lu['modules']:
        name=module.replace('.','/')+'.lua';data=checked_file(safe(lua,name)).read_bytes()
        allow.add('host/'+name,data,'adapter-lua','adapter/host/'+name);adapter['host/'+name]=data
        host.append({'source':'host/'+name,'destination':name})
    own=recipe['own_license'];license_data=checked_file(own['path'],own['sha256']).read_bytes()
    allow.add('LICENSE',license_data,'adapter-license','GPL-3.0-only draft declaration');adapter['LICENSE']=license_data
    worker=recipe['worker'];record_file(allow,worker['binary'],'bin/dragonwilds_skate_worker.exe','worker')
    archive=checked_file(worker['source_zip'],worker['source_sha256']).read_bytes();audit_zip(archive,'worker source',worker=True)
    worker_binding(archive,worker['binary']['sha256'])
    allow.add('sources/worker-source.zip',archive,'worker-source','licensed upstream snapshot and documented adapter changes',source=True)
    runtime['worker']='bin/dragonwilds_skate_worker.exe'
    helper_meta=[]
    if not {'ipc','render'} <= recipe['helpers'].keys() or recipe['helpers'].keys()-{'ipc','render','buildings'}:
        raise BuildError('Only the required ipc/render and optional buildings helpers are supported')
    for key,filename in [('ipc','skate_ipc.dll'),('render','skate_render.dll'),('buildings','skate_buildings.dll')]:
        if key not in recipe['helpers']:continue
        record=recipe['helpers'][key];record_file(allow,record['binary'],'bin/'+filename,key+'-helper')
        source=rust_sources(record['source_root'])
        source_records=[{'path':name,'sha256':sha(data)} for name,data in sorted(source.items())]
        source_fingerprint=sha(json.dumps(source_records,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode())
        if record.get('source_sha256')!=source_fingerprint:raise BuildError('Native helper source differs from its reviewed build: '+key)
        for name,data in source.items():adapter['helpers/'+key+'/'+name]=data
        helper_meta.append({'component':key,'binary':'bin/'+filename,'sha256':sha(allow.entries['bin/'+filename].data),
                            'compiled_source_sha256':source_fingerprint,'source_files':source_records})
        add_text_tree(allow,record['notices_root'],'notices/helpers/'+key,key+'-helper-notices')
        if key=='buildings':runtime['building_reader_library']='bin/'+filename
    runtime.update(transport_library='bin/skate_ipc.dll',lua_transport_library='bin/skate_ipc.dll',render_library='bin/skate_render.dll')
    if 'building_rule' in recipe:
        if 'buildings' not in recipe['helpers'] or 'building_exporter' not in recipe:
            raise BuildError('Building collision rule requires its reader and supplementary exporter')
        record_file(allow,recipe['building_rule'],'config/building-collision-rule.json','building-rule')
        runtime['building_rule']='config/building-collision-rule.json'
    for key,prefix,path_key in [('exporter','exporter','map_export'),('building_exporter','building-exporter','building_export')]:
        if key not in recipe:
            if key=='exporter':raise BuildError('The base map exporter is required')
            continue
        exporter=recipe[key];add_inventory(allow,exporter['root'],exporter['inventory'],prefix,prefix)
        executable=prefix+'/DragonwildsMapExport.exe'
        if executable not in allow.entries:raise BuildError('Self-contained exporter executable is missing: '+key)
        archive=checked_file(exporter['source_zip'],exporter['source_sha256']).read_bytes();audit_zip(archive,prefix+' source')
        designated=json.loads(Path(exporter['inventory']).read_text(encoding='utf-8-sig')).get('source_archive',{})
        if designated.get('sha256')!=sha(archive) or designated.get('bytes')!=len(archive):
            raise BuildError('Exporter source archive differs from its reviewed binary designation: '+key)
        allow.add('sources/'+prefix+'-source.zip',archive,prefix+'-source','managed exporter and CUE4Parse source',source=True)
        add_text_tree(allow,exporter['notices_root'],'notices/'+prefix,prefix+'-notices')
        runtime[path_key]=executable
    python=recipe['python'];add_python(allow,python['root'],python['sources'],python['downloads'])
    runtime.update(python_runtime='python/python.exe',relay_launcher='app/skate_launcher.py')
    record_file(allow,recipe['sdl']['binary'],'bin/SDL3.dll','SDL3')
    add_text_tree(allow,recipe['sdl']['notices_root'],'notices/SDL3','SDL3-notices');runtime['gamepad_library']='bin/SDL3.dll'
    for key,filename in [('mount_icon','skateboard.png'),('board_deck_texture','skateboard-deck.png')]:
        if key not in recipe['artwork']:
            if key=='mount_icon':raise BuildError('Mount artwork is required')
            continue
        record_file(allow,recipe['artwork'][key],'artwork/'+filename,'artwork');runtime[key]='artwork/'+filename
    framework=recipe['loader'];expected={'dwmapi.dll','ue4ss/UE4SS.dll','ue4ss/Mods/shared/UEHelpers/UEHelpers.lua'}
    optional={'ue4ss/UE4SS-settings.ini','ue4ss/Mods/mods.txt'}
    if not expected<=set(framework['files']) or set(framework['files'])-expected-optional:
        raise BuildError('Loader allowlist requires the reviewed proxy, loader and UEHelpers; only optional reviewed default settings may be added')
    external=framework.get('mode')=='external'
    if framework.get('mode','bundled') not in ('external','bundled'):raise BuildError('Unknown loader distribution mode')
    for name,record in sorted(framework['files'].items()):
        if external and name in expected:
            path=checked_file(record['path'],record['sha256'])
            prerequisites.append({'destination':name,'sha256':record['sha256'],'bytes':path.stat().st_size})
            continue
        record_file(allow,record,'loader/'+name,'UE4SS');loader.append({'source':'loader/'+name,'destination':name})
    add_text_tree(allow,framework['notices_root'],'notices/UE4SS','UE4SS-notices')
    if external:
        allow.add('notices/UE4SS-PREREQUISITE.json',encoded({'schema':1,'mode':'external_manual_prerequisite',
            'version':'v3.0.1-1152-ge3ba1016','source_revision':'e3ba1016562d6c0868c410d0a71e88bfcdbf691b',
            'release_page':'https://github.com/UE4SS-RE/RE-UE4SS/releases','files':prerequisites,
            'redistribution_status':'No UE4SS binary or UEHelpers source is included. UEPseudo licensing closure has not been established.'}),
            'external-prerequisite','reviewed official UE4SS release identity')
    compatibility=json.loads(Path(recipe['compatibility']).read_text(encoding='utf-8-sig'))
    if set(compatibility)!={'steam_build','game_exe_sha256','ue4ss_sha256'} or not all(SHA.fullmatch(compatibility[k]) for k in ('game_exe_sha256','ue4ss_sha256')):
        raise BuildError('Exact tested game/loader compatibility is required')
    if compatibility['ue4ss_sha256']!=framework['files']['ue4ss/UE4SS.dll']['sha256']:raise BuildError('Selected loader differs from tested compatibility')
    if 'building_rule' in recipe:
        rule=json.loads(allow.entries[runtime['building_rule']].data)
        if not isinstance(rule,dict) or rule.get('schema')!='S3BUILDINGRULE1' or rule.get('verified') is not True or rule.get('game_exe_sha256')!=compatibility['game_exe_sha256']:
            raise BuildError('Building rule is not verified for the packaged game identity')
    closure={'schema':1,'python':py,'lua':lu,'generated_runtime':['app/runtime.json','skate_config.lua'],'helpers':helper_meta}
    allow.add('notices/runtime-closure.json',encoded(closure),'adapter-provenance','static import and literal require closure');adapter['SOURCE-CLOSURE.json']=encoded(closure)
    adapter['BUILDING.md']=ADAPTER_BUILDING.encode()
    adapter['README.md']=ADAPTER_BUILDING.encode()
    adapter['Build-Helpers.ps1']=HELPER_BUILD.encode()
    for name in ('build_release.py','skate_install.py','BUILDER-CONTRACT.md','INSTALLER-CONTRACT.md'):
        adapter['release-tools/'+name]=checked_file(Path(__file__).with_name(name)).read_bytes()
    for name in ('test_build_release.py','test_skate_install.py'):
        path=Path(__file__).with_name(name)
        if not path.is_file():raise BuildError('Required release tool verification source is missing: '+name)
        adapter['release-tools/'+name]=path.read_bytes()
    allow.add('sources/adapter-source.zip',source_zip(adapter),'adapter-source','exact Python Lua native helper and release tool source',source=True)
    if 'readme' in recipe:
        record_file(allow,recipe['readme'],'README.md','release-docs')
    else:
        allow.add('README.md',RELEASE_README.encode(),'release-docs','draft installation and preparation instructions')
    for record in recipe.get('documents',[]):
        destination=record.get('destination','')
        if not destination.startswith('docs/') or Path(destination).suffix.lower() not in ('.md','.txt'):
            raise BuildError('Additional documentation must be an explicit docs Markdown/text file')
        record_file(allow,{k:v for k,v in record.items() if k!='destination'},destination,'release-docs')
    allow.add('notices/PACKAGE-PROVENANCE.json',encoded({'schema':1,'status':'draft_pending_publish_approval_and_public_playtests','files':allow.source_records()}),'release-provenance','exact reviewed input inventory')
    manifest={'schema':1,'product':'DragonwildsSkate','version':version,'status':'draft_pending_publish_approval_and_public_playtests',
              'compatibility':compatibility,'install':{'schema':1,'runtime':runtime,'host_files':host,'loader_files':loader,
                                                      'external_prerequisites':prerequisites}}
    return write_stage(allow,manifest,output)


ADAPTER_BUILDING='''# Adapter source candidate

The source in this archive exactly matches the package adapter. Newly authored
adapter code is released under GPL-3.0-only. Retained upstream code keeps its
original licenses and notices. Gameplay acceptance and publication are separate.

Python runtime modules are under app; UE4SS scripts are under host. Native helper
sources are under helpers. Build helpers with Build-Helpers.ps1 using Rust 1.96.0
and the Windows MSVC toolchain. Cargo.lock files pin dependencies. Their exact
license texts are in the package notices/helpers directories. Cargo downloads
dependencies from the registry; no game assets or converters are included.

The worker and managed exporter have separate corresponding-source archives and
build instructions. The private package recipe is deliberately omitted: supply
local input locations and their reviewed SHA256 values to release-tools/build_release.py.
The builder only stages a folder. It does not create or publish a release ZIP.
'''
HELPER_BUILD=r'''param([Parameter(Mandatory=$true)][string]$OutputDirectory,
 [string]$CargoExecutable="cargo",[string]$RustcExecutable="rustc",[switch]$Offline)
$ErrorActionPreference="Stop"
$root=(Resolve-Path -LiteralPath $PSScriptRoot).Path
$output=[IO.Path]::GetFullPath($OutputDirectory)
$rustc=(Get-Command -Name $RustcExecutable -ErrorAction Stop).Source
$version=& $rustc --version
if($LASTEXITCODE -ne 0 -or $version -notmatch '^rustc 1\.96\.0 '){throw "Rust 1.96.0 is required"}
$cargoHome=if($env:CARGO_HOME){[IO.Path]::GetFullPath($env:CARGO_HOME)}else{Join-Path $env:USERPROFILE '.cargo'}
$remaps=@($root,$cargoHome,$env:USERPROFILE)
foreach($value in $remaps){if(-not $value -or $value.Contains("=") -or $value.Contains([char]31)){throw "Unsupported remap path"}}
$names=@('CARGO_ENCODED_RUSTFLAGS','RUSTFLAGS','RUSTC','RUSTC_WRAPPER','RUSTC_WORKSPACE_WRAPPER','CARGO_INCREMENTAL')
$previous=@{}
foreach($name in $names){$previous[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
try {
 $flags=@("--remap-path-prefix=$root=/source","--remap-path-prefix=$cargoHome=/build/cargo","--remap-path-prefix=$env:USERPROFILE=/build/user")
 $env:CARGO_ENCODED_RUSTFLAGS=$flags -join [char]31
 $env:RUSTFLAGS=$null;$env:RUSTC=$rustc;$env:RUSTC_WRAPPER=$null;$env:RUSTC_WORKSPACE_WRAPPER=$null;$env:CARGO_INCREMENTAL='0'
 $closure=Get-Content -LiteralPath (Join-Path $root 'SOURCE-CLOSURE.json') -Raw | ConvertFrom-Json
 $components=@($closure.helpers | ForEach-Object { $_.component })
 if($components.Count -lt 2 -or $components -notcontains 'ipc' -or $components -notcontains 'render' -or
    @($components | Select-Object -Unique).Count -ne $components.Count -or
    @($components | Where-Object { $_ -notin @('ipc','render','buildings') }).Count -ne 0){throw 'Invalid source helper closure'}
 foreach($component in $components) {
  $arguments=@("build","--release","--locked","--manifest-path",(Join-Path $root "helpers/$component/Cargo.toml"),"--target-dir",(Join-Path $output $component))
  if($Offline){$arguments+="--offline"}
  & $CargoExecutable @arguments
  if($LASTEXITCODE -ne 0){throw "Helper build failed: $component"}
 }
} finally {foreach($name in $names){[Environment]::SetEnvironmentVariable($name,$previous[$name],'Process')}}
'''
RELEASE_README='''# Dragonwilds Skate - draft package

This candidate is not approved for publication. Public playtests and publishing
approval are pending. Newly authored adapter code has a GPL-3.0-only
license; upstream components retain the licenses included in notices and sources.

Known blocker: lightweight player-built floors and walls are not yet connected
to active collision. Building diagnostics are not completed building support.
See docs/MAIN-STATUS.md before testing. Historical draft packages are rollback
archives; use the single main development build for continued testing.

It uses the imported Skate 3 example simulation and animations with Dragonwilds collision
and character adapters. Game assets, maps, mappings, saves and converters are not
included. Obtain the separately distributed converter described in docs/SETUP-DRAFT.md
and prepare private assets from your own files; use the bundled offline
setup command to generate Dragonwilds collision locally. Preparation is required
before skating can start. Only the exact game build in release-manifest.json is supported.

From an extracted folder, run python/python.exe app/skate_install.py --help.
First install UE4SS v3.0.1-1152-ge3ba1016 from the official project releases:
https://github.com/UE4SS-RE/RE-UE4SS/releases. The exact three required file hashes
and sizes are in release-manifest.json and notices/UE4SS-PREREQUISITE.json.
Experimental release downloads can change; a different build will be refused.
This package does not include UE4SS or UEHelpers. Their redistribution license
closure has not been established; the official download is a separate prerequisite.

Choose the Dragonwilds installation explicitly. The installer refuses incompatible
shared loaders and an enabled earlier DragonwildsSkateProbe prototype. It records
owned hashes and backups; uninstall preserves modified files, shared loaders,
private prepared data and saves. Run uninstall from the extracted distribution,
not the installed Python process. Read app/skate_setup.py --help and
app/skate_launcher.py --help through the bundled Python for setup and diagnostics.

The package is portable before installation. Do not relocate an installed copy
without reinstalling; its generated paths refer to the chosen game directory.
Credits: Universal Modder and the Skate 3 Rust Engine (SK8-ENGINE), including
dumbad's research and Chasm's rewrite; the Apache-licensed mashup host; CUE4Parse,
UE4SS, SDL3, Python, NumPy, Pillow, Rust and their credited dependencies. This adapter
includes original optional mount/deck artwork. Original game models/animations
remain private.

Sources and exact dependency notices are included. No network service or account
is needed by the installed adapter.
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    stage=commands.add_parser('stage');stage.add_argument('--recipe',required=True);stage.add_argument('--output',required=True)
    audit=commands.add_parser('audit');audit.add_argument('--package',required=True)
    args=parser.parse_args()
    try:
        result=build(json.loads(Path(args.recipe).read_text(encoding='utf-8-sig')),args.output) if args.command=='stage' else audit_package(args.package)
        print(json.dumps(result,indent=2));return 0
    except (BuildError,InstallError,OSError,ValueError,KeyError,zipfile.BadZipFile) as error:
        print(json.dumps({'ok':False,'error':str(error)}));return 1


if __name__=='__main__':raise SystemExit(main())
