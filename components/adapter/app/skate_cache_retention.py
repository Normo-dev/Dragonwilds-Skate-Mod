"""Conservative retirement of positively recorded grind caches.

At startup the launcher owns Singleton and has not spawned its child. During a
session the caller must hold the native lease, drain Python cache/checkpoint
writers, and prove the resident native checkpoint is successfully catalogued.
No raw geometry, overlays, observations, assets, or game saves are eligible.
Unknown files and interrupted preparations are protection roots, never garbage.
"""
from pathlib import Path
import hashlib
import json
import os
import struct
import threading
import time

from skate_exposed_cache import contained, digest, hash_value, GRAPH_NAME, GRAPH_HEADER, GRAPH_FORMAT, graph_lineage

NAME = 'generated-grind-retention-v1.json'
MAGIC = 'DWSRET1'
LOCK = threading.RLock()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _load(root):
    path = contained(root, NAME)
    if not path.exists():
        return {'families': {}, 'records': {}, 'pending': []}
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Retention catalog is too large')
    value = json.loads(path.read_text(encoding='utf-8'))
    data = value['data']
    if (value.get('magic') != MAGIC or value.get('schema') != 1 or
            value.get('sha256') != hashlib.sha256(encoded(data)).hexdigest() or
            set(data) != {'families', 'records', 'pending'} or
            not isinstance(data['families'], dict) or not isinstance(data['records'], dict) or
            not isinstance(data['pending'], list)):
        raise ValueError('Retention catalog is invalid; preserving caches')
    for key, record in data['records'].items():
        if key != _key(record):
            raise ValueError('Retention record identity changed')
        _validate(root, record)
    for family, keys in data['families'].items():
        if (not hash_value(family) or not isinstance(keys, list) or len(keys) > 2 or
                len(keys) != len(set(keys)) or any(key not in data['records'] or
                _family(data['records'][key]['save']) != family for key in keys)):
            raise ValueError('Retention family is invalid')
    for row in data['pending']:
        _file(root, row)
    return data


def _write(root, data):
    path = contained(root, NAME)
    temporary = contained(root, NAME + '.tmp')
    value = {'magic': MAGIC, 'schema': 1, 'data': data,
             'sha256': hashlib.sha256(encoded(data)).hexdigest()}
    with temporary.open('wb') as stream:
        stream.write(encoded(value)); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def _family(save):
    if (not isinstance(save, dict) or set(save) != {'world_session', 'world'} or
            any(not isinstance(v, str) or not v for v in save.values())):
        raise ValueError('Retention needs an exact save/world identity')
    return hashlib.sha256(encoded(save)).hexdigest()


def _key(record):
    identity = [_family(record['save']), record['manifest']['path'], record['manifest']['sha256']]
    if record['rails']['kind'] == 'graph':
        identity.extend((record['rails']['path'], record['rails']['header_sha256']))
    return hashlib.sha256(encoded(identity)).hexdigest()


def _file(root, row):
    fields = {'path', 'sha256', 'bytes', 'kind'}
    if isinstance(row, dict) and row.get('kind') == 'graph':
        fields.add('header_sha256')
    if (not isinstance(row, dict) or set(row) != fields or
            not hash_value(row['sha256']) or type(row['bytes']) is not int or row['bytes'] < 0):
        raise ValueError('Invalid owned cache file record')
    path = contained(root, row['path']); parts = Path(row['path']).parts
    kind = row['kind']
    if kind == 'graph':
        good = (len(parts) == 3 and parts[0] == 'incremental-grind-v1' and hash_value(parts[1])
                and GRAPH_NAME.fullmatch(path.name) and hash_value(row['header_sha256']))
    elif kind == 'warm':
        good = len(parts) == 2 and parts[0] == 'warm-start-v1' and path.suffix == '.json' and hash_value(path.stem)
    else:
        good = (len(parts) in (4, 5) and parts[0] == 'exposed-edges-v1' and
                hash_value(parts[1]) and hash_value(parts[2]))
        if kind == 'manifest':
            good = good and len(parts) == 4 and path.suffix == '.json' and hash_value(path.stem)
        elif kind == 'rails':
            good = good and len(parts) == 5 and parts[3] == 'native-rails' and path.suffix == '.dwr1' and hash_value(path.stem)
        elif kind == 'cell':
            good = good and len(parts) == 5 and parts[3] == 'payloads' and path.suffix == '.edges' and path.stem == row['sha256']
        else:
            good = False
    if not good:
        raise ValueError('Owned cache path is outside the generated-file allowlist')
    return path


def _validate(root, record):
    fields = {'save', 'base', 'scene', 'algorithm', 'manifest', 'rails', 'warms'}
    graph = record.get('rails', {}).get('kind') == 'graph'
    if graph:
        fields.add('graph_lineage')
    if set(record) != fields:
        raise ValueError('Invalid retention record')
    _family(record['save'])
    if not all(hash_value(record[k]) for k in ('base', 'scene', 'algorithm')):
        raise ValueError('Invalid retention identity')
    for key in ('manifest', 'rails'):
        if record[key]['kind'] != ('graph' if key == 'rails' and graph else key):
            raise ValueError('Retention file kind changed')
        _file(root, record[key])
    if not isinstance(record['warms'], list) or not record['warms']:
        raise ValueError('Retention warm pointer ownership is missing')
    for warm in record['warms']:
        if warm['kind'] != 'warm':
            raise ValueError('Retention warm pointer kind changed')
        _file(root, warm)
    prefix = f"exposed-edges-v1/{record['algorithm']}/{record['base']}/"
    if record['manifest']['path'] != prefix + record['scene'] + '.json':
        raise ValueError('Retention paths do not match their identities')
    if graph:
        lineage = record['graph_lineage']
        if (not isinstance(lineage, list) or not 1 <= len(lineage) <= 257 or lineage[0] != record['rails']
                or len({row['path'] for row in lineage}) != len(lineage)):
            raise ValueError('Retention graph lineage is incomplete')
        namespace = _namespace(record['rails']['path'])
        for row in lineage:
            if row.get('kind') != 'graph' or _namespace(row['path']) != namespace:
                raise ValueError('Retention graph ancestor outside its cache')
            _file(root, row)
    elif record['rails']['path'] != prefix + 'native-rails/' + record['manifest']['sha256'] + '.dwr1':
        raise ValueError('Retention native rails do not match their identities')


def _graph_rows(lineage):
    # For graph rows sha256 pins the body; header_sha256 pins the header which
    # names that body checksum, algorithm, exact snapshot and payload length.
    return [{'path': row['path'], 'bytes': row['bytes'], 'sha256': row['payload_sha256'],
             'header_sha256': row['header_sha256'], 'kind': 'graph'} for row in lineage]


def _outputs(record):
    return [record['manifest'], *record.get('graph_lineage', [record['rails']]), *record['warms']]


def record_accepted(root, pointer, saved):
    """Called only after WarmWorldStore validates and publishes native acceptance.

    Catalog failure is optional-optimization failure: it must not block gameplay.
    Publishing the warm pointer first makes a crash leave a protected unknown.
    """
    try:
        root = Path(root).resolve(); pointer = Path(pointer)
        checkpoint = saved.get('accepted_checkpoint', {})
        if checkpoint.get('format') != 'exposed_edges_v1':
            return False
        identity = saved['identity']; marker = contained(root, checkpoint['path'])
        rails = checkpoint['rail_cache']
        if digest(pointer) != hashlib.sha256(encoded(saved)).hexdigest():
            # WarmWorldStore's compact JSON does not sort keys, so compare the
            # parsed exact document while pinning its actual bytes below.
            if json.loads(pointer.read_text(encoding='utf-8')) != saved:
                raise ValueError('Accepted warm pointer changed before recording')
        record = {'save': {k: identity[k] for k in ('world_session', 'world')},
                  'base': identity['base_fingerprint'], 'scene': saved['scene_fingerprint'],
                  'algorithm': checkpoint['algorithm_sha256'],
                  'manifest': {'path': checkpoint['path'], 'sha256': checkpoint['sha256'],
                               'bytes': marker.stat().st_size, 'kind': 'manifest'},
                  'rails': {'path': rails['path'], 'sha256': rails['sha256'], 'bytes': rails['bytes'], 'kind': 'rails'},
                  'warms': [{'path': pointer.relative_to(root).as_posix(), 'sha256': digest(pointer),
                             'bytes': pointer.stat().st_size, 'kind': 'warm'}]}
        if rails.get('format') == GRAPH_FORMAT:
            record['graph_lineage'] = _graph_rows(rails['lineage'])
            record['rails'] = record['graph_lineage'][0]
        _validate(root, record)
        with LOCK:
            data = _load(root); key = _key(record); family = _family(record['save'])
            # The same scene may be reused after a worker update. Preserve exact
            # ownership of both pointer names until that scene is retired.
            aliases = {encoded(row): row for row in data['records'].get(key, {}).get('warms', []) + record['warms']}
            record['warms'] = list(aliases.values())
            data['records'][key] = record
            data['families'][family] = ([key] + [k for k in data['families'].get(family, []) if k != key])[:2]
            _write(root, data)
        return True
    except Exception as error:
        print('GENERATED CACHE RECORD SKIPPED', str(error), flush=True)
        return False


def _namespace(relative):
    parts = Path(relative).parts
    if parts and parts[0] == 'exposed-edges-v1':
        return '/'.join(parts[:3])
    if parts and parts[0] == 'incremental-grind-v1':
        return '/'.join(parts[:2])
    return None


def _manifest(root, path):
    """Read only manifest metadata; never load/hash retained cell payloads."""
    value = json.loads(path.read_text(encoding='utf-8'))
    relative = path.relative_to(root).as_posix(); namespace = _namespace(relative)
    parts = Path(relative).parts
    if (len(parts) != 4 or value.get('magic') != 'DWE1' or value.get('schema') != 1 or
            value.get('complete') is not True or value.get('algorithm_sha256') != parts[1] or
            value.get('base_fingerprint') != parts[2] or
            path.name != (value.get('scene_fingerprint') or 'base') + '.json' or
            not isinstance(value.get('cells'), list)):
        raise ValueError('Unrecognized manifest protects its namespace')
    cells = {}
    total = 0
    for cell in value['cells']:
        count = cell['segments']; sha = cell['sha256']
        if (not hash_value(sha) or type(count) is not int or count < 0 or
                cell['bytes'] != 16 + count * 48 or cell['path'] != 'payloads/' + sha + '.edges'):
            raise ValueError('Unrecognized cell reference protects its namespace')
        name = namespace + '/' + cell['path']
        cells[name] = {'path': name, 'sha256': sha, 'bytes': cell['bytes'], 'kind': 'cell'}
        total += count
    if value.get('segments') != total:
        raise ValueError('Manifest segment count changed')
    sha = digest(path)
    return sha, cells, namespace + '/native-rails/' + sha + '.dwr1'


def _matches(root, row):
    path = _file(root, row)
    if not path.exists():
        return True
    if not path.is_file() or path.stat().st_size != row['bytes']:
        return False
    if row['kind'] == 'graph':
        with path.open('rb') as stream:
            header = stream.read(GRAPH_HEADER.size)
            if len(header) != GRAPH_HEADER.size or hashlib.sha256(header).hexdigest() != row['header_sha256']:
                return False
            magic, version, algorithm, snapshot, payload_sha, size, reserved = GRAPH_HEADER.unpack(header)
            if (magic not in (b'DGI1', b'DGD1') or version != 1 or any(reserved)
                    or algorithm.hex() != Path(row['path']).parts[1] or snapshot.hex() != GRAPH_NAME.fullmatch(path.name)[1]
                    or payload_sha.hex() != row['sha256'] or size + GRAPH_HEADER.size != row['bytes']):
                return False
            return hashlib.file_digest(stream, 'sha256').hexdigest() == row['sha256']
    if digest(path) != row['sha256']:
        return False
    if row['kind'] in ('cell', 'rails'):
        with path.open('rb') as stream:
            header = stream.read(128 if row['kind'] == 'rails' else 16)
        if row['kind'] == 'cell':
            return len(header) == 16 and header[:8] == struct.pack('<4sI', b'DWC1', 1) and row['bytes'] == 16 + 48 * struct.unpack('<Q', header[8:])[0]
        return (len(header) == 128 and header[:8] == struct.pack('<4sI', b'DWR1', 1) and
                header[40:72].hex() == path.stem and header[8:40].hex() == Path(row['path']).parts[1])
    return True


def collect(root, *, budget_seconds=8.0, stopped=lambda: False):
    """Best-effort collection under proven startup or native-lease quiescence.

    The time budget limits planning; no deletion starts from an incomplete mark.
    Startup bounds tracked accepted history between launches. Same-session
    callers additionally own the lease, Python-writer, and active-root gates.
    """
    started = time.monotonic(); root = Path(root).resolve(); removed = freed = 0
    def active():
        if stopped():
            raise RuntimeError('Maintenance owner exited; preserving remaining caches')
    try:
        with LOCK:
            active()
            data = _load(root)
            if not data['records'] and not data['pending']:
                return {'status': 'empty', 'removed': 0, 'bytes': 0}
            retained = {key for keys in data['families'].values() for key in keys}
            if set(data['records']) == retained and not data['pending']:
                return {'status': 'retained', 'removed': 0, 'bytes': 0}
            owned = data['records']; keep = set(); candidates = {row['path']: row for row in data['pending']}
            retired_by_path = {}; known_warm = {}; known_manifests = {}; skipped = set(); known_graphs = set()
            for key, record in owned.items():
                known_manifests.setdefault(record['manifest']['path'], set()).add(record['manifest']['sha256'])
                if key in retained and (not _file(root, record['manifest']).is_file() or
                        not _file(root, record['rails']).is_file()):
                    skipped.add(_namespace(record['manifest']['path']))
                for row in _outputs(record):
                    if row['kind'] == 'graph':
                        known_graphs.add(row['path'])
                    if key in retained:
                        keep.add(row['path'])
                    else:
                        candidates[row['path']] = row
                if key in retained and record['rails']['kind'] == 'graph':
                    # Fresh native Sessions look up the canonical graph for the
                    # exact DWE fingerprint and rebind owners. A repeated scene
                    # may checkpoint a later immutable generation instead.
                    canonical = _file(root, record['rails']).parent / (record['manifest']['sha256'] + '.dgi1')
                    try:
                        lineage = graph_lineage(root, canonical, manifest_sha256=record['manifest']['sha256'])
                        keep.update(row['path'] for row in lineage)
                    except Exception:
                        skipped.add(_namespace(record['rails']['path']))
                if key not in retained:
                    retired_by_path.setdefault(record['manifest']['path'], set()).add(record['manifest']['sha256'])
                for warm in record['warms']:
                    known_warm.setdefault((warm['path'], warm['sha256']), set()).add(key)
            unknown_scenes = set()
            warm_folder = contained(root, 'warm-start-v1')
            for path in warm_folder.glob('*.json'):
                relative = path.relative_to(root).as_posix(); contained(root, relative)
                keys = known_warm.get((relative, digest(path)), set())
                if keys and not keys.intersection(retained):
                    continue
                keep.add(relative)
                value = json.loads(path.read_text(encoding='utf-8'))
                checkpoint = value.get('accepted_checkpoint', {})
                if checkpoint.get('format') == 'exposed_edges_v1':
                    marker = contained(root, checkpoint['path']); keep.add(marker.relative_to(root).as_posix())
                    rail = checkpoint['rail_cache']
                    keep.add(contained(root, rail['path']).relative_to(root).as_posix())
                    if rail.get('format') == GRAPH_FORMAT:
                        lineage = graph_lineage(root, contained(root, rail['path']), algorithm=rail['algorithm_sha256'],
                                                manifest_sha256=checkpoint['sha256'])
                        keep.update(row['path'] for row in lineage)
                else:
                    unknown_scenes.add((value['identity']['base_fingerprint'], value['scene_fingerprint']))
            exposed = contained(root, 'exposed-edges-v1')
            for algorithm in exposed.iterdir() if exposed.exists() else []:
                if not hash_value(algorithm.name) or not algorithm.is_dir() or algorithm.resolve() != algorithm:
                    continue
                for folder in algorithm.iterdir():
                    if not hash_value(folder.name) or not folder.is_dir() or folder.resolve() != folder:
                        continue
                    namespace = folder.relative_to(root).as_posix()
                    if any(folder.glob('*.incomplete.jsonl')):
                        skipped.add(namespace); continue
                    for path in folder.glob('*.json'):
                        active()
                        relative = path.relative_to(root).as_posix()
                        try:
                            contained(root, relative)
                            sha, cells, rails = _manifest(root, path)
                            if relative in known_manifests and sha not in known_manifests[relative]:
                                skipped.add(namespace)
                            retire = (relative not in keep and sha in retired_by_path.get(relative, set()) and
                                      (folder.name, path.stem) not in unknown_scenes and path.name != 'base.json')
                            if retire:
                                candidates.update(cells)
                            else:
                                keep.update(cells); keep.update((relative, rails))
                        except Exception:
                            skipped.add(namespace)
                        if time.monotonic() - started > budget_seconds:
                            return {'status': 'deferred', 'removed': 0, 'bytes': 0}
            graph_root = contained(root, 'incremental-grind-v1')
            for folder in graph_root.iterdir() if graph_root.exists() else []:
                if not hash_value(folder.name) or not folder.is_dir() or folder.resolve() != folder:
                    continue
                namespace = folder.relative_to(root).as_posix()
                # Uncatalogued payloads include interrupted/native-staged work.
                # They protect their complete ancestor chain just like unknown
                # DWE manifests protect their shared extraction payloads.
                for path in folder.glob('*.dgi1'):
                    active(); relative = path.relative_to(root).as_posix()
                    if relative in known_graphs and relative not in keep:
                        continue
                    try:
                        lineage = graph_lineage(root, path, algorithm=folder.name)
                        keep.update(row['path'] for row in lineage)
                    except Exception:
                        skipped.add(namespace)
                    if time.monotonic() - started > budget_seconds:
                        return {'status': 'deferred', 'removed': 0, 'bytes': 0}
                # Until positively catalogued, native lookup pointers are
                # protection roots. Preserve this graph namespace if a pointer
                # is unrecognized rather than infer an unsafe deletion target.
                if any(folder.glob('*.json')) or any(folder.glob('*.tmp')):
                    skipped.add(namespace)
            for record in owned.values():
                if any(_namespace(row['path']) in skipped for row in record.get('graph_lineage', [])):
                    skipped.add(_namespace(record['manifest']['path']))
            candidates = {name: row for name, row in candidates.items() if name not in keep and _namespace(name) not in skipped}
            # Any changed owned bytes preserve the entire affected namespace.
            for row in candidates.values():
                active()
                if not _matches(root, row):
                    skipped.add(_namespace(row['path']))
                if time.monotonic() - started > budget_seconds:
                    return {'status': 'deferred', 'removed': 0, 'bytes': 0}
            for record in owned.values():
                if any(_namespace(row['path']) in skipped for row in record.get('graph_lineage', [])):
                    skipped.add(_namespace(record['manifest']['path']))
            candidates = {name: row for name, row in candidates.items() if _namespace(name) not in skipped}
            data['pending'] = list(candidates.values())
            active()
            _write(root, data)  # Durable ownership survives deletion of a DWE.
            for row in sorted(candidates.values(), key=lambda row: (row['kind'] != 'manifest', row['path'])):
                active()
                path = _file(root, row)
                if path.exists() and _matches(root, row):
                    path.unlink(); removed += 1; freed += row['bytes']
            # Keep records if any output remains (including protected/unknown).
            data['pending'] = [row for row in candidates.values() if _file(root, row).exists()]
            for key, record in list(owned.items()):
                if key not in retained and not any(_file(root, row).exists() and _matches(root, row)
                        for row in _outputs(record)):
                    del owned[key]
            _write(root, data)
            return {'status': 'complete', 'removed': removed, 'bytes': freed, 'protected_namespaces': len(skipped),
                    'elapsed_ms': round((time.monotonic() - started) * 1000)}
    except Exception as error:
        return {'status': 'skipped', 'removed': removed, 'bytes': freed, 'reason': str(error)}


def launch_maintenance(paths):
    """Called inside the launcher's existing Singleton, before spawning children."""
    try:
        from skate_process import process_images
        targets = {os.path.normcase(str(Path(paths[key]).resolve())) for key in
                   ('worker', 'python_runtime', 'map_export', 'building_export', 'dotnet') if key in paths}
        names = {Path(value).name.casefold() for value in targets}
        # The game itself may be open. A matching helper with unreadable identity
        # is not proof that its old cache writer has exited; preserve everything.
        for pid, name, image in process_images(names):
            if image is None or os.path.normcase(str(Path(image).resolve())) in targets:
                return {'status': 'skipped', 'reason': 'An existing helper may still be writing caches', 'removed': 0, 'bytes': 0}
        return collect(paths['map_data'])
    except Exception as error:
        return {'status': 'skipped', 'reason': str(error), 'removed': 0, 'bytes': 0}


def maintenance_child(stream, output):
    """One stdin request from a parent holding a native maintenance lease.

    Caller must keep the lease until this child exits and assign it to its
    existing kill-on-parent-exit ChildJob. This extra Singleton handle prevents
    a replacement launcher from starting while a dying parent's collector exits.
    The child performs no native calls, simulation, or input itself.
    Before dispatch the parent must prove its latest resident exposed checkpoint
    was catalogued successfully; a finished/failed checkpoint future is not proof.
    """
    from skate_process import ParentProcess, Singleton
    result = {'status': 'skipped', 'removed': 0, 'bytes': 0}
    try:
        line = stream.readline(16385)
        if len(line) > 16384:
            raise ValueError('Maintenance request is too large')
        request = json.loads(line)
        if (set(request) != {'op', 'root', 'parent_pid', 'budget_seconds'} or request['op'] != 'collect' or
                type(request['parent_pid']) is not int or request['parent_pid'] <= 0 or
                type(request['budget_seconds']) not in (int, float) or not 0 < request['budget_seconds'] <= 60):
            raise ValueError('Invalid maintenance request')
        root = Path(request['root'])
        if not root.is_absolute() or root.resolve() != root or not root.is_dir():
            raise ValueError('Invalid private map root')
        with Singleton() as keeper, ParentProcess(request['parent_pid']) as parent:
            if keeper.acquired:
                raise ValueError('Maintenance requires an existing managed launcher')
            result = collect(root, budget_seconds=request['budget_seconds'], stopped=parent.exited)
    except Exception as error:
        result['reason'] = str(error)
    output.write(json.dumps(result, separators=(',', ':')) + '\n'); output.flush()
    return 0


if __name__ == '__main__':
    import sys
    if sys.argv[1:] != ['_child']:
        raise SystemExit('This helper is started only by the managed mod launcher.')
    raise SystemExit(maintenance_child(sys.stdin, sys.stdout))
