"""Validate the persistent exposed-edge cache without constructing a simulation.

Offline setup verifies every payload. Startup checks the pinned manifest and
native-rail header; the native loader verifies payloads while loading them.
"""
from pathlib import Path
import hashlib
import json
import math
import re
import stat
import struct


SHA = re.compile(r'[0-9a-f]{64}\Z')
DWR_HEADER = struct.Struct('<4sI32s32sQQQ32s')
DWR_FORMAT = 'exposed_native_rails_v1'
GRAPH_FORMAT = 'incremental_grind_v1'
GRAPH_HEADER = struct.Struct('<4sI32s32s32sQ16s')
GRAPH_NAME = re.compile(r'([0-9a-f]{64})(?:-[0-9a-f]{64})?\.dgi1\Z')
MAX_GRAPH_CHAIN = 256
MAX_SEGMENTS = 50_000_000
MAX_RAIL_STATS = 1024 * 1024


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def hash_value(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def contained(root, relative):
    root = Path(root).resolve()
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('Exposed-edge path must be relative')
    target = root / relative
    if target.resolve() != target or not target.is_relative_to(root):
        raise ValueError('Exposed-edge path escapes its cache or is linked')
    return target


def vector(value):
    return (isinstance(value, list) and len(value) == 3 and
            all(type(v) in (int, float) and math.isfinite(v) for v in value))


def bounds(value):
    return (isinstance(value, dict) and vector(value.get('min')) and vector(value.get('max')) and
            all(a <= b for a, b in zip(value['min'], value['max'])))


def manifest(root, path, *, base, scene, algorithm=None, expected_sha256=None, verify_payloads=False):
    """Check exact base/scene ownership; never accept an incomplete checkpoint."""
    root = Path(root).resolve()
    path = Path(path)
    cache_root = root / 'exposed-edges-v1'
    if (not path.is_absolute() or path.resolve() != path or not path.is_relative_to(cache_root)
            or not path.is_file()):
        raise ValueError('Exposed-edge manifest escapes its cache or is missing')
    if not hash_value(base) or (scene is not None and not hash_value(scene)):
        raise ValueError('Invalid exposed-edge world identity')
    raw = path.read_bytes()
    observed_sha = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and (not hash_value(expected_sha256) or observed_sha != expected_sha256):
        raise ValueError('Exposed-edge manifest checksum mismatch')
    value = json.loads(raw)
    del raw
    if (not isinstance(value, dict) or value.get('magic') != 'DWE1' or type(value.get('schema')) is not int
            or value['schema'] != 1 or value.get('complete') is not True
            or value.get('base_fingerprint') != base or value.get('scene_fingerprint') != scene
            or not hash_value(value.get('algorithm_sha256'))
            or (algorithm is not None and value['algorithm_sha256'] != algorithm)):
        raise ValueError('Exposed-edge manifest is incomplete or belongs to different inputs')
    parameters = value.get('parameters')
    if (not isinstance(parameters, dict) or not parameters or any(
            type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in parameters.values())):
        raise ValueError('Invalid exposed-edge parameters')
    if set(parameters) != {'cell_size_cm', 'halo_cm', 'truck_distance_cm', 'tolerance_cm'}:
        raise ValueError('Exposed-edge parameters are incomplete')
    expected_path = cache_root / value['algorithm_sha256'] / base / ((scene or 'base') + '.json')
    if path != expected_path:
        raise ValueError('Exposed-edge manifest is not at its native cache location')
    cells = value.get('cells')
    if not isinstance(cells, list):
        raise ValueError('Exposed-edge manifest has no cell inventory')
    # The manifest path already authenticates its parent. Every legal payload
    # is one checksum-named leaf below this single directory. Authenticate that
    # directory once, then check each distinct leaf once during this call only.
    payload_dir = path.parent / 'payloads'
    if payload_dir.resolve() != payload_dir:
        raise ValueError('Exposed-edge payload directory escapes its cache or is linked')
    keys, total, previous_key, payloads = set(), 0, None, {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise ValueError('Invalid exposed-edge cell')
        key, count = cell.get('key'), cell.get('segments')
        if (not isinstance(key, list) or len(key) != 3 or any(type(v) is not int for v in key)
                or tuple(key) in keys or not bounds(cell)
                or type(count) is not int or count < 0 or type(cell.get('bytes')) is not int
                or cell['bytes'] != 16 + count * 48 or not hash_value(cell.get('sha256'))):
            raise ValueError('Invalid or duplicate exposed-edge cell')
        if previous_key is not None and tuple(key) <= previous_key:
            raise ValueError('Exposed-edge cell order is not deterministic')
        previous_key = tuple(key)
        size, halo = parameters['cell_size_cm'], parameters['halo_cm']
        if cell['min'] != [v * size for v in key] or cell['max'] != [(v + 1) * size for v in key]:
            raise ValueError('Exposed-edge cell ownership bounds disagree')
        keys.add(tuple(key))
        dependencies = cell.get('dependencies')
        if not isinstance(dependencies, list) or not dependencies or any(not bounds(b) for b in dependencies):
            raise ValueError('Invalid exposed-edge dependency bounds')
        if not any(all(b['min'][i] <= cell['min'][i] - halo and b['max'][i] >= cell['max'][i] + halo
                       for i in range(3)) for b in dependencies):
            raise ValueError('Exposed-edge cell omits its dependency halo')
        relative = cell.get('path')
        if relative != 'payloads/' + cell['sha256'] + '.edges':
            # Preserve rejection of absolute, linked, and escaping malformed
            # paths before reporting a noncanonical checksum filename.
            contained(path.parent, relative)
            raise ValueError('Exposed-edge payload name does not match its checksum')
        identity = (cell['sha256'], cell['bytes'], count)
        previous_identity = payloads.get(relative)
        if previous_identity is not None and previous_identity != identity:
            raise ValueError('Conflicting metadata for the same exposed-edge payload')
        if previous_identity is None:
            payload = payload_dir / (cell['sha256'] + '.edges')
            try:
                info = payload.lstat()
            except FileNotFoundError:
                info = None
            if info is not None and (stat.S_ISLNK(info.st_mode) or
                    getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise ValueError('Exposed-edge payload escapes its cache or is linked')
            if verify_payloads:
                if (info is None or not stat.S_ISREG(info.st_mode) or info.st_size != cell['bytes']
                        or digest(payload) != cell['sha256']):
                    raise ValueError('Exposed-edge payload is missing or changed')
                with payload.open('rb') as stream:
                    header = stream.read(16)
                    if header != struct.pack('<4sIQ', b'DWC1', 1, count):
                        raise ValueError('Invalid exposed-edge payload header')
                    for _ in range(count):
                        row = struct.unpack('<6d', stream.read(48))
                        if not all(math.isfinite(v) for v in row) or row[:3] == row[3:]:
                            raise ValueError('Invalid exposed-edge segment')
            payloads[relative] = identity
        total += count
    if type(value.get('segments')) is not int or value['segments'] != total:
        raise ValueError('Exposed-edge manifest segment count disagrees')
    return {'path': path.relative_to(root).as_posix(), 'bytes': path.stat().st_size,
            'sha256': observed_sha, 'base_fingerprint': base, 'scene_fingerprint': scene,
            'algorithm_sha256': value['algorithm_sha256'], 'cells': len(cells), 'segments': total}


def rail_cache_record(root, declaration, exposed, *, native_path=False, verify_payloads=False):
    """Bind exact ordered native rails to a verified DWE identity.

    Only offline validation reads all point data. The launch check reads a fixed
    header and file metadata; native loading performs its own complete check.
    """
    if isinstance(declaration, dict) and declaration.get('format') == GRAPH_FORMAT:
        return graph_cache_record(root, declaration, exposed, native_path=native_path, verify_payloads=verify_payloads)
    if (not isinstance(declaration, dict) or type(declaration.get('schema')) is not int
            or declaration['schema'] != 1 or declaration.get('ready') is not True
            or declaration.get('format') != DWR_FORMAT
            or declaration.get('manifest_sha256') != exposed['sha256']
            or declaration.get('algorithm_sha256') != exposed['algorithm_sha256']
            or not hash_value(declaration.get('sha256'))
            or any(type(declaration.get(k)) is not int for k in ('bytes', 'rails', 'points', 'segments'))
            or not isinstance(declaration.get('path'), str)):
        raise ValueError('Native rail cache is missing or belongs to different exposed edges; resume offline map setup')
    root = Path(root).resolve()
    if native_path:
        path = Path(declaration['path'])
        if not path.is_absolute() or path.resolve() != path or not path.is_relative_to(root):
            raise ValueError('Native rail cache path escapes its cache or is linked')
    else:
        path = contained(root, declaration['path'])
    expected = contained(root, exposed['path']).parent / 'native-rails' / (exposed['sha256'] + '.dwr1')
    if path != expected:
        raise ValueError('Native rail cache is not at its exact native cache location')
    count, points, segments = (declaration[k] for k in ('rails', 'points', 'segments'))
    source = exposed['segments']
    if (not 0 <= source <= MAX_SEGMENTS or not 0 <= count <= source
            or not count * 2 <= points <= source + count or segments != points - count):
        raise ValueError('Native rail cache counts exceed its exposed source')
    if not path.is_file() or path.stat().st_size != declaration['bytes']:
        raise ValueError('Native rail cache is missing or changed')
    with path.open('rb') as stream:
        header = stream.read(DWR_HEADER.size)
        if len(header) != DWR_HEADER.size:
            raise ValueError('Invalid native rail cache header')
        magic, version, algorithm, dwe, stored_count, stored_points, stats_len, payload_sha = DWR_HEADER.unpack(header)
        if (magic != b'DWR1' or version != 1 or algorithm.hex() != exposed['algorithm_sha256']
                or dwe.hex() != exposed['sha256'] or stored_count != count or stored_points != points
                or not 0 < stats_len <= MAX_RAIL_STATS
                or declaration['bytes'] != DWR_HEADER.size + stats_len + count * 8 + points * 12):
            raise ValueError('Native rail cache header identity, counts or size disagree')
        if verify_payloads:
            full_hash, payload_hash = hashlib.sha256(header), hashlib.sha256()

            def read_exact(size):
                raw = stream.read(size)
                if len(raw) != size:
                    raise ValueError('Native rail cache payload is truncated')
                full_hash.update(raw)
                payload_hash.update(raw)
                return raw

            stats = json.loads(read_exact(stats_len))
            if (not isinstance(stats, dict) or type(stats.get('provider_segments')) is not int
                    or stats['provider_segments'] != segments):
                raise ValueError('Native rail cache statistics disagree with polylines')
            consumed = 0
            for _ in range(count):
                length, = struct.unpack('<Q', read_exact(8))
                if not 2 <= length <= points - consumed:
                    raise ValueError('Invalid native rail cache polyline length')
                remaining, previous = length, None
                while remaining:
                    chunk = min(remaining, 4096)
                    for point in struct.iter_unpack('<3f', read_exact(chunk * 12)):
                        if any(not math.isfinite(v) or abs(v) >= 1e7 for v in point) or point == previous:
                            raise ValueError('Invalid native rail cache point or zero-length primitive')
                        previous = point
                    remaining -= chunk
                consumed += length
            if (consumed != points or stream.read(1) or payload_hash.digest() != payload_sha
                    or full_hash.hexdigest() != declaration['sha256']):
                raise ValueError('Native rail cache checksum or point count mismatch')
    return {'schema': 1, 'ready': True, 'format': DWR_FORMAT, 'path': path.relative_to(root).as_posix(),
            'bytes': declaration['bytes'], 'sha256': declaration['sha256'],
            'manifest_sha256': exposed['sha256'], 'algorithm_sha256': exposed['algorithm_sha256'],
            'rails': count, 'points': points, 'segments': segments}


def graph_lineage(root, path, *, algorithm=None, manifest_sha256=None, expected_sha256=None, verify_payloads=False):
    """Validate the exact immutable graph chain, reading only headers at launch.

    Native loading authenticates and decodes every body. Offline verification
    also hashes bodies here. Header and embedded body pins give the collector
    an exact ownership proof without hashing the whole base on every checkpoint.
    """
    root, path = Path(root).resolve(), Path(path)
    lineage, seen = [], set()
    expected = manifest_sha256
    while True:
        if len(lineage) > MAX_GRAPH_CHAIN:
            raise ValueError('Incremental grind chain needs offline compaction')
        if (not path.is_absolute() or path.resolve() != path or not path.is_file()
                or not path.is_relative_to(root / 'incremental-grind-v1')):
            raise ValueError('Incremental grind cache escapes its cache or is missing')
        parts = path.relative_to(root).parts
        name = GRAPH_NAME.fullmatch(path.name)
        if (len(parts) != 3 or parts[0] != 'incremental-grind-v1' or not hash_value(parts[1]) or not name):
            raise ValueError('Incremental grind cache is not at its exact native cache location')
        if path in seen:
            raise ValueError('Incremental grind cache lineage contains a cycle')
        seen.add(path)
        with path.open('rb') as stream:
            header = stream.read(GRAPH_HEADER.size)
            if len(header) != GRAPH_HEADER.size:
                raise ValueError('Invalid incremental grind cache header')
            magic, version, graph_pin, snapshot, payload_sha, payload_bytes, reserved = GRAPH_HEADER.unpack(header)
            if (magic not in (b'DGI1', b'DGD1') or version != 1 or any(reserved)
                    or graph_pin.hex() != parts[1] or (algorithm is not None and graph_pin.hex() != algorithm)
                    or snapshot.hex() != name[1] or (expected is not None and snapshot.hex() != expected)
                    or payload_bytes + GRAPH_HEADER.size != path.stat().st_size
                    or path.stat().st_size > 16 * 1024**3):
                raise ValueError('Incremental grind header identity or size disagrees')
            algorithm = graph_pin.hex()
            parent = None
            if magic == b'DGI1':
                if payload_bytes < 96:
                    raise ValueError('Truncated incremental grind base inventory')
            else:
                parent_snapshot = stream.read(32)
                length_raw = stream.read(8)
                if len(parent_snapshot) != 32 or len(length_raw) != 8:
                    raise ValueError('Truncated incremental grind parent identity')
                length, = struct.unpack('<Q', length_raw)
                if not 0 < length <= 256 or payload_bytes < 32 + 8 + length + 1 + 8:
                    raise ValueError('Invalid incremental grind parent name length')
                try:
                    parent_name = stream.read(length).decode('utf-8')
                except UnicodeDecodeError as error:
                    raise ValueError('Invalid incremental grind parent name') from error
                parent_match = GRAPH_NAME.fullmatch(parent_name)
                if not parent_match or parent_match[1] != parent_snapshot.hex():
                    raise ValueError('Incremental grind parent path or snapshot identity disagrees')
                rebind = stream.read(1)
                changed_raw = stream.read(8)
                if rebind not in (b'\0', b'\1') or len(changed_raw) != 8 or struct.unpack('<Q', changed_raw)[0] > 2_000_000:
                    raise ValueError('Invalid incremental grind delta inventory')
                parent = contained(path.parent, parent_name)
                expected = parent_snapshot.hex()
            if verify_payloads:
                stream.seek(GRAPH_HEADER.size)
                full_hash, body_hash = hashlib.sha256(header), hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    full_hash.update(block); body_hash.update(block)
                if body_hash.digest() != payload_sha:
                    raise ValueError('Incremental grind payload checksum mismatch')
                if not lineage and expected_sha256 is not None and full_hash.hexdigest() != expected_sha256:
                    raise ValueError('Incremental grind file checksum mismatch')
        lineage.append({'path': path.relative_to(root).as_posix(), 'bytes': path.stat().st_size,
                        'header_sha256': hashlib.sha256(header).hexdigest(), 'payload_sha256': payload_sha.hex(),
                        'manifest_sha256': snapshot.hex(), 'format': magic.decode('ascii')})
        if parent is None:
            return lineage
        path = parent


def graph_cache_record(root, declaration, exposed, *, native_path=False, verify_payloads=False):
    if (not isinstance(declaration, dict) or type(declaration.get('schema')) is not int
            or declaration['schema'] != 1 or declaration.get('ready') is not True or declaration.get('format') != GRAPH_FORMAT
            or declaration.get('manifest_sha256') != exposed['sha256']
            or declaration.get('extraction_algorithm_sha256') != exposed['algorithm_sha256']
            or not hash_value(declaration.get('algorithm_sha256')) or not hash_value(declaration.get('sha256'))
            or not isinstance(declaration.get('path'), str)
            or any(type(declaration.get(k)) is not int for k in ('bytes', 'rails', 'points', 'segments'))):
        raise ValueError('Incremental grind cache is missing or belongs to different exposed edges')
    root = Path(root).resolve()
    path = Path(declaration['path']) if native_path else contained(root, declaration['path'])
    count, points, segments = (declaration[k] for k in ('rails', 'points', 'segments'))
    source = exposed['segments']
    if (not 0 <= source <= MAX_SEGMENTS or not 0 <= count <= source
            or not count * 2 <= points <= source + count or segments != points - count
            or declaration['bytes'] < GRAPH_HEADER.size):
        raise ValueError('Incremental grind cache counts exceed its exposed source')
    lineage = graph_lineage(root, path, algorithm=declaration['algorithm_sha256'], manifest_sha256=exposed['sha256'],
                            expected_sha256=declaration['sha256'], verify_payloads=verify_payloads)
    canonical = path.parent / (exposed['sha256'] + '.dgi1')
    if path != canonical:
        # The accepted generation preserves this Session's owner history; a
        # fresh native Session restores the canonical same-geometry graph.
        graph_lineage(root, canonical, algorithm=declaration['algorithm_sha256'], manifest_sha256=exposed['sha256'],
                      verify_payloads=verify_payloads)
    if lineage[0]['bytes'] != declaration['bytes']:
        raise ValueError('Incremental grind cache size changed')
    if 'lineage' in declaration and declaration['lineage'] != lineage:
        raise ValueError('Incremental grind recorded lineage changed')
    if lineage[0]['format'] == 'DGI1':
        with path.open('rb') as stream:
            stream.seek(GRAPH_HEADER.size)
            inventory = struct.unpack('<11Qd', stream.read(96))
        if (inventory[0] != source or inventory[4] != count or inventory[10] != segments
                or any(v > MAX_SEGMENTS for v in (*inventory[:5], *inventory[6:11]))
                or not math.isfinite(inventory[11])):
            raise ValueError('Incremental grind base counts disagree')
    return {'schema': 1, 'ready': True, 'format': GRAPH_FORMAT, 'path': path.relative_to(root).as_posix(),
            'bytes': declaration['bytes'], 'sha256': declaration['sha256'], 'manifest_sha256': exposed['sha256'],
            'algorithm_sha256': declaration['algorithm_sha256'], 'extraction_algorithm_sha256': exposed['algorithm_sha256'],
            'rails': count, 'points': points, 'segments': segments, 'lineage': lineage}


def declared_grind_cache(declaration):
    """Native graph records take precedence over the compatibility DWR alias."""
    return declaration.get('graph_cache', declaration.get('rail_cache'))


def prepared_record(root, declaration, *, base, scene=None, verify_payloads=True):
    if (not isinstance(declaration, dict) or type(declaration.get('schema')) is not int
            or declaration['schema'] != 1 or declaration.get('ready') is not True
            or declaration.get('base_fingerprint') != base or declaration.get('scene_fingerprint') != scene
            or not hash_value(declaration.get('manifest_sha256')) or not hash_value(declaration.get('algorithm_sha256'))
            or not isinstance(declaration.get('manifest_file'), str)):
        raise ValueError('Exposed-edge preparation is missing or incomplete')
    record = manifest(root, Path(declaration['manifest_file']), base=base, scene=scene,
                      algorithm=declaration['algorithm_sha256'], expected_sha256=declaration['manifest_sha256'],
                      verify_payloads=verify_payloads)
    if any(type(declaration.get(k)) is not int or declaration[k] != record[k] for k in ('cells', 'segments')):
        raise ValueError('Exposed-edge preparation counts disagree')
    record['rail_cache'] = rail_cache_record(root, declared_grind_cache(declaration), record,
                                           native_path=True, verify_payloads=verify_payloads)
    return {'schema': 1, **record}


def check_record(root, record, *, base, scene=None, verify_payloads=False):
    if (not isinstance(record, dict) or type(record.get('schema')) is not int or record['schema'] != 1
            or not hash_value(record.get('sha256')) or not hash_value(record.get('algorithm_sha256'))
            or any(type(record.get(k)) is not int for k in ('bytes', 'cells', 'segments'))):
        raise ValueError('Exposed edges are not prepared; resume offline map setup')
    value = manifest(root, contained(root, record.get('path')), base=base, scene=scene,
                     algorithm=record['algorithm_sha256'], expected_sha256=record['sha256'], verify_payloads=verify_payloads)
    value['rail_cache'] = rail_cache_record(root, declared_grind_cache(record), value, verify_payloads=verify_payloads)
    if any(record.get(k) != value[k] for k in value):
        raise ValueError('Exposed-edge preparation record does not match its manifest')
    return value
