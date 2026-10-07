"""Transactional import of locally prepared, user-owned Skate assets.

The sibling stage is retained as an ownership receipt. Only files named by its
immutable source inventory may be created there. The source is never modified.
"""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import shutil

from skate_preflight import check_assets, digest, read_json


PRODUCT = 'DragonwildsSkateAssetImport'
RESERVE = 256 * 1024 * 1024


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def _plain(path):
    """Keep lexical identity until links (including parent junctions) are checked."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Asset import refuses a linked path: ' + str(part))
    if path.resolve() != path:
        raise ValueError('Asset import path changes after resolution: ' + str(path))
    return path


def _inventory(root):
    root = _plain(root)
    if not root.is_dir():
        raise ValueError('Prepared asset directory is missing: ' + str(root))
    result, seen = [], set()
    for path in sorted(root.rglob('*'), key=lambda item: item.relative_to(root).as_posix()):
        _plain(path)
        if not path.resolve().is_relative_to(root):
            raise ValueError('Prepared asset path leaves its directory: ' + str(path))
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError('Prepared assets contain a non-file entry: ' + str(path))
        relative = path.relative_to(root).as_posix()
        if relative.casefold() in seen:
            raise ValueError('Prepared assets contain duplicate Windows paths: ' + relative)
        seen.add(relative.casefold())
        before = path.stat()
        hashed = digest(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Prepared asset changed while being verified: ' + relative)
        result.append({'path': relative, 'bytes': before.st_size, 'sha256': hashed})
    if not result:
        raise ValueError('Prepared asset directory is empty')
    return result


def _matches(path, record):
    _plain(path)
    return path.is_file() and path.stat().st_size == record['bytes'] and digest(path) == record['sha256']


def _validate_stage(stage, records):
    """Reject unrelated files before any resumed write, even inside owned trees."""
    _plain(stage)
    allowed_roots = {'owner.json', 'payload', 'pending', 'original-empty-target'}
    for item in stage.iterdir():
        _plain(item)
        if item.name not in allowed_roots:
            raise ValueError('Asset stage contains an unknown entry: ' + str(item))
    if not (stage / 'owner.json').is_file():
        raise ValueError('Asset stage has no valid ownership marker')
    names = {record['path'] for record in records}
    directories = set()
    for name in names:
        directories.update(parent.as_posix() for parent in PurePosixPath(name).parents if parent.as_posix() != '.')
    payload = stage / 'payload'
    if payload.exists():
        if not payload.is_dir():
            raise ValueError('Asset stage payload is not a directory')
        for item in payload.rglob('*'):
            _plain(item)
            relative = item.relative_to(payload).as_posix()
            if (item.is_dir() and relative not in directories) or (not item.is_dir() and (not item.is_file() or relative not in names)):
                raise ValueError('Asset stage contains an unknown payload entry: ' + relative)
    pending = stage / 'pending'
    if pending.exists():
        if not pending.is_dir():
            raise ValueError('Asset stage pending area is not a directory')
        expected = {str(i) + '.part' for i in range(len(records))}
        for item in pending.iterdir():
            _plain(item)
            if not item.is_file() or item.name not in expected:
                raise ValueError('Asset stage contains an unknown pending entry: ' + str(item))
    empty = stage / 'original-empty-target'
    if empty.exists() and (not empty.is_dir() or any(empty.iterdir())):
        raise ValueError('Preserved empty asset directory was changed')


def _copy_verified(source, pending, record):
    _plain(source)
    _plain(pending)
    hashed = hashlib.sha256()
    total = 0
    with source.open('rb') as incoming, pending.open('wb') as outgoing:
        while block := incoming.read(1024 * 1024):
            outgoing.write(block)
            hashed.update(block)
            total += len(block)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    if total != record['bytes'] or hashed.hexdigest() != record['sha256']:
        raise ValueError('Prepared asset changed during copying: ' + record['path'])


def import_assets(source, target):
    """Import/resume a matching owned transaction and publish verified assets.

    No resume switch is needed: the source path and complete byte inventory must
    match the stage owner. Foreign data and modified completed files are refused.
    """
    source, target = _plain(source), _plain(target)
    records = _inventory(source)
    check_assets(source)
    if source == target:
        return {'already_prepared': True, **check_assets(target)}
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError('Asset source and destination must not contain each other')
    stage = _plain(target.with_name(target.name + '.import-stage'))
    if stage.is_relative_to(source) or source.is_relative_to(stage):
        raise ValueError('Asset source and stage must not contain each other')
    identity = {'schema': 1, 'product': PRODUCT, 'source': str(source),
                'target': str(target), 'files': records}
    owner = {**identity, 'identity_sha256': hashlib.sha256(_encoded(identity)).hexdigest()}
    existed = stage.exists()
    if existed:
        if not stage.is_dir() or not (stage / 'owner.json').is_file():
            raise ValueError('Refusing an unknown asset import stage: ' + str(stage))
        _plain(stage / 'owner.json')
        previous = read_json(stage / 'owner.json', 'Asset import stage owner')
        if previous != owner:
            raise ValueError('Asset import stage belongs to different source data; preserve it and use the original source')
        _validate_stage(stage, records)
    # A completed transaction is idempotent, including an interruption immediately
    # after the atomic rename but before the caller received its success result.
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        if existed and not (stage / 'payload').exists() and target.is_dir() and _inventory(target) == records:
            return {'already_prepared': True, 'resumed': True, **check_assets(target)}
        raise ValueError('Destination already contains files; existing assets were preserved: ' + str(target))
    if not existed:
        target.parent.mkdir(parents=True, exist_ok=True)
        _plain(target.parent)
        stage.mkdir()
        with (stage / 'owner.json').open('xb') as stream:
            stream.write(_encoded(owner))
            stream.flush()
            os.fsync(stream.fileno())
    _validate_stage(stage, records)
    payload, pending = stage / 'payload', stage / 'pending'
    payload.mkdir(exist_ok=True)
    pending.mkdir(exist_ok=True)
    remaining = 0
    for index, record in enumerate(records):
        destination = payload / record['path']
        if destination.exists():
            if not _matches(destination, record):
                raise ValueError('Completed staged asset changed; it was preserved: ' + record['path'])
        else:
            temporary = pending / (str(index) + '.part')
            remaining += max(0, record['bytes'] - (temporary.stat().st_size if temporary.exists() else 0))
    if remaining and shutil.disk_usage(stage).free < remaining + RESERVE:
        raise ValueError('Not enough free disk space to copy prepared assets; the owned stage can be resumed')
    copied = skipped = 0
    for index, record in enumerate(records):
        destination = payload / record['path']
        if destination.exists():
            skipped += 1
            continue
        temporary = pending / (str(index) + '.part')
        if not temporary.exists() or not _matches(temporary, record):
            _copy_verified(source / record['path'], temporary, record)
        # Validate the actual disk bytes, not only the copy's input stream.
        if not _matches(temporary, record):
            raise ValueError('Staged asset failed verification: ' + record['path'])
        destination.parent.mkdir(parents=True, exist_ok=True)
        _plain(destination.parent)
        os.rename(temporary, destination)
        copied += 1
    if _inventory(source) != records:
        raise ValueError('Prepared source inventory changed during import; nothing was published')
    if _inventory(payload) != records:
        raise ValueError('Staged asset inventory changed; nothing was published')
    check_assets(payload)
    _validate_stage(stage, records)
    _plain(target)
    if target.exists():
        if not target.is_dir() or any(target.iterdir()):
            raise ValueError('Destination changed during import; existing data was preserved')
        preserved = stage / 'original-empty-target'
        if preserved.exists():
            raise ValueError('Destination was recreated during a pending import; both directories were preserved')
        os.rename(target, preserved)
    os.rename(payload, target)
    return {'copied_files': copied, 'skipped_files': skipped, 'resumed': existed,
            'inventory_sha256': owner['identity_sha256'], **check_assets(target)}
