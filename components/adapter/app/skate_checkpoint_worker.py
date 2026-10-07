"""One managed checkpoint-validation job, isolated from the relay's Python GIL.

The parent assigns this child to its kill-on-close job BEFORE writing stdin.
The child never starts a simulation, reads controller input, or retires files.
Only a native-accepted exposed checkpoint can produce retained=True.
"""
from contextlib import redirect_stdout
from pathlib import Path
import json
import os
import re
import sys

MAX_REQUEST_BYTES = 256 * 1024
MAX_ERROR_CHARS = 512
SHA = re.compile(r'[0-9a-f]{64}\Z')


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f'Invalid {label} fields')
    return value


def _text(value, label, limit=32767):
    if not isinstance(value, str) or not value or len(value) > limit or '\0' in value:
        raise ValueError(f'Invalid {label}')
    return value


def _hash(value, label):
    if not isinstance(value, str) or SHA.fullmatch(value) is None:
        raise ValueError(f'Invalid {label}')


def _path(value, label, *, directory=False, within=None):
    path = Path(_text(value, label))
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError(f'{label} must be a canonical absolute path')
    if within is not None and not path.is_relative_to(within):
        raise ValueError(f'{label} escapes its private cache')
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError(f'{label} is not an existing {"directory" if directory else "file"}')
    return path


def _pairs(rows):
    value = {}
    for key, item in rows:
        if key in value:
            raise ValueError('Duplicate checkpoint request field')
        value[key] = item
    return value


def _constant(value):
    raise ValueError(f'Non-finite JSON constant: {value}')


def read_request(stream):
    raw = stream.readline(MAX_REQUEST_BYTES + 1)
    if isinstance(raw, bytes):
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError('Checkpoint request is too large')
        raw = raw.decode('utf-8')
    elif not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_REQUEST_BYTES:
        raise ValueError('Checkpoint request is too large or not UTF-8 text')
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    _object(value, ('op', 'root', 'worker', 'descriptor', 'cached', 'result', 'parent_pid'), 'checkpoint request')
    if value['op'] not in ('remember', 'promote'):
        raise ValueError('Unknown checkpoint operation')
    if type(value['parent_pid']) is not int or not 0 < value['parent_pid'] <= 0xffffffff or value['parent_pid'] == os.getpid():
        raise ValueError('Invalid checkpoint parent process')

    root = _path(value['root'], 'map root', directory=True)
    worker = _path(value['worker'], 'native worker')
    descriptor = _object(value['descriptor'], ('mode', 'world_session', 'revision'), 'descriptor')
    if descriptor['mode'] != 'whole_world':
        raise ValueError('Checkpoint requires a whole-world descriptor')
    _text(descriptor['world_session'], 'save identity', 256)
    if type(descriptor['revision']) is not int or not 0 <= descriptor['revision'] <= 0xffffffff:
        raise ValueError('Invalid descriptor revision')
    cached = _object(value['cached'], ('world_manifest', 'scene_overlay', 'report'), 'cached world')
    _path(cached['world_manifest'], 'base manifest', within=root)
    _path(cached['scene_overlay'], 'scene overlay', within=root / 'prepared')
    report = _object(cached['report'], ('world', 'world_session', 'source_fingerprint', 'scene_fingerprint'), 'cache report')
    _text(report['world'], 'world asset', 4096)
    if report['world_session'] != descriptor['world_session']:
        raise ValueError('Checkpoint save identities disagree')
    _hash(report['source_fingerprint'], 'base fingerprint')
    _hash(report['scene_fingerprint'], 'scene fingerprint')

    result = _object(value['result'], ('ok', 'collision_revision', 'world'), 'native result')
    if result['ok'] is not True or type(result['collision_revision']) is not int:
        raise ValueError('Checkpoint requires a successful native result')
    expected = 0 if value['op'] == 'remember' else descriptor['revision']
    if result['collision_revision'] != expected:
        raise ValueError('Checkpoint native revision does not match the operation')
    world = _object(result['world'], ('scene_checkpoint', 'scene_fingerprint'), 'accepted native world')
    if world['scene_fingerprint'] != report['scene_fingerprint']:
        raise ValueError('Checkpoint native scene is not the accepted cached scene')
    checkpoint = world['scene_checkpoint']
    required = {'schema', 'durable', 'format', 'scene_fingerprint', 'manifest_file', 'manifest_sha256', 'algorithm_sha256'}
    if (not isinstance(checkpoint, dict) or not required <= checkpoint.keys()
            or not set(checkpoint) <= required | {'rail_cache', 'graph_cache'}
            or not {'rail_cache', 'graph_cache'}.intersection(checkpoint)):
        raise ValueError('Invalid native checkpoint fields')
    if 'graph_cache' in checkpoint and 'rail_cache' in checkpoint and checkpoint['graph_cache'] != checkpoint['rail_cache']:
        raise ValueError('Native grind aliases disagree')
    if (type(checkpoint['schema']) is not int or checkpoint['schema'] != 1 or checkpoint['durable'] is not True or
            checkpoint['format'] != 'exposed_edges_v1' or checkpoint['scene_fingerprint'] != report['scene_fingerprint']):
        raise ValueError('Checkpoint requires durable exposed edges for the accepted scene')
    _hash(checkpoint['manifest_sha256'], 'exposed manifest hash')
    _hash(checkpoint['algorithm_sha256'], 'edge algorithm hash')
    _path(checkpoint['manifest_file'], 'exposed manifest', within=root / 'exposed-edges-v1')
    from skate_exposed_cache import declared_grind_cache, GRAPH_FORMAT, DWR_FORMAT
    rails = declared_grind_cache(checkpoint)
    graph = isinstance(rails, dict) and rails.get('format') == GRAPH_FORMAT
    fields = {'schema', 'ready', 'format', 'path', 'bytes', 'sha256', 'manifest_sha256', 'algorithm_sha256',
              'rails', 'points', 'segments', 'cache_hit'}
    if graph:
        fields.add('extraction_algorithm_sha256')
    rails = _object(rails, fields, 'native grind record')
    _path(rails['path'], 'native grind cache', within=root / ('incremental-grind-v1' if graph else 'exposed-edges-v1'))
    for field in ('schema', 'bytes', 'rails', 'points', 'segments'):
        if type(rails[field]) is not int or not 0 <= rails[field] <= 2**63 - 1:
            raise ValueError(f'Invalid native rail {field}')
    for field in ('sha256', 'manifest_sha256', 'algorithm_sha256'):
        _hash(rails[field], 'native rail ' + field)
    if (rails['schema'] != 1 or rails['ready'] is not True or type(rails['cache_hit']) is not bool or
            rails['format'] not in (DWR_FORMAT, GRAPH_FORMAT) or rails['manifest_sha256'] != checkpoint['manifest_sha256'] or
            rails.get('extraction_algorithm_sha256', rails['algorithm_sha256']) != checkpoint['algorithm_sha256']):
        raise ValueError('Native rail identity does not match the checkpoint')
    if graph:
        _hash(rails['extraction_algorithm_sha256'], 'graph extraction algorithm')
    # WarmWorldStore performs the authoritative DWE/DWR, S3O1 and catalog checks.
    return value, root, worker


def checkpoint_child(stream, output, diagnostics=None):
    diagnostics = sys.stderr if diagnostics is None else diagnostics
    result = {'ok': False, 'retained': False, 'error': None}
    try:
        request, root, worker = read_request(stream)
        from skate_process import ParentProcess, Singleton
        # Holding this named object also prevents replacement launch while a
        # dying parent's child finishes. Parent ChildJob owns forced shutdown.
        with Singleton() as keeper, ParentProcess(request['parent_pid']) as parent:
            if keeper.acquired:
                raise ValueError('Checkpoint requires an existing managed launcher')
            if parent.exited():
                raise RuntimeError('Checkpoint parent exited before validation')
            with redirect_stdout(diagnostics):
                from skate_warm_world import WarmWorldStore
                store = WarmWorldStore(root, worker)
                method = store.remember if request['op'] == 'remember' else store.promote
                retained = method(request['descriptor'], request['cached'], request['result'])
            if parent.exited():
                raise RuntimeError('Checkpoint parent exited during validation')
            if type(retained) is not bool:
                raise RuntimeError('Checkpoint store did not return explicit catalog status')
            result = {'ok': True, 'retained': retained,
                      'error': None if retained else 'Checkpoint catalog publication was not confirmed'}
    except Exception as error:
        result['error'] = str(error).replace('\r', ' ').replace('\n', ' ')[:MAX_ERROR_CHARS]
        print('CHECKPOINT WORKER:', result['error'], file=diagnostics)
    output.write(json.dumps(result, separators=(',', ':'), allow_nan=False) + '\n')
    output.flush()
    return 0


if __name__ == '__main__':
    if sys.argv[1:] != ['_child']:
        raise SystemExit('This helper is started only by the managed mod launcher.')
    raise SystemExit(checkpoint_child(sys.stdin.buffer, sys.stdout))
