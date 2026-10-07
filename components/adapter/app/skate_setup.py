"""Prepare private game data locally; this module never downloads game assets."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid

# Embedded Python may use an isolated _pth file. The signed/hashed app directory
# is the only extra module search location needed by this entry point.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_paths import load_paths
from skate_preflight import (check_package, check_game, check_assets, check_map,
                            check_building_catalogue, map_preparation_identity, PreflightError, contained, digest, read_json)
from skate_asset_import import import_assets
from skate_process import offline_maintenance


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def fresh_directory(path):
    path = Path(path).resolve()
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError('Destination already contains files: ' + str(path))
    path.mkdir(parents=True, exist_ok=True)
    return path


def check_app(app):
    app = Path(app).resolve()
    manifest = check_package(app.parent)
    paths = load_paths(app)
    check_game(paths.get('game_root', ''), manifest.get('compatibility', {}))
    listed = {(app.parent / row['path']).resolve() for row in manifest['files']}
    if paths['map_export'] not in listed or paths['map_export'].suffix.lower() != '.exe':
        raise ValueError('Setup requires the exact portable exporter from this release')
    # Public setup is intentionally limited to the installer's separate data tree.
    data = paths['game_root'] / 'DragonwildsSkateData'
    if data.resolve() != data:
        raise ValueError('Private data directory must not be a junction or symbolic link')
    for key, child in [('assets', 'assets'), ('map_data', 'map-data'), ('mailbox', 'mailbox')]:
        expected = data / child
        if paths[key] != expected or expected.resolve() != expected:
            raise ValueError('Run the installer to configure private data: ' + key)
    return manifest, paths


def run_export(executable, arguments, log):
    with Path(log).open('w', encoding='utf-8') as stream:
        result = subprocess.run([str(executable), *map(str, arguments)],
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
    if result.returncode:
        raise RuntimeError('Map export failed; see ' + str(log))
    # Older exporter builds recorded an omitted heightfield but returned zero.
    # Treat that explicit diagnostic as failure as well.
    with Path(log).open(encoding='utf-8', errors='replace') as stream:
        if any('TERRAIN OMITTED ' in line or 'MESH FAILED ' in line for line in stream):
            raise RuntimeError('Map export omitted collision; see ' + str(log))


def prepare_world_lips(worker, manifest, log, *, assets=None, exposed_edges=False):
    """Prepare persistent grind data offline, before publishing readiness."""
    command = {'op': 'prepare_world', 'manifest': str(Path(manifest).resolve()), 'exposed_edges': exposed_edges}
    if exposed_edges:
        if assets is None:
            raise ValueError('Prepared source assets are needed to verify the skateboard dimensions')
        check_assets(assets)
        command['assets'] = str(Path(assets).resolve())
    expected = read_json(manifest, 'Exported world').get('source_fingerprint')
    with Path(log).open('w', encoding='utf-8') as stream:
        result = subprocess.run([str(worker)], input=json.dumps(command) + '\n',
            stdout=subprocess.PIPE, stderr=stream, text=True, encoding='utf-8',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
    try:
        response = json.loads(result.stdout.strip())
    except ValueError as error:
        raise RuntimeError('Original grind cache preparation returned invalid output; see ' + str(log)) from error
    if (result.returncode or not isinstance(response, dict) or response.get('ok') is not True
            or response.get('status') != 'prepared' or response.get('session_loaded') is not False
            or response.get('source_fingerprint') != expected):
        detail = response.get('error') if isinstance(response, dict) else None
        detail = detail[:1200] if isinstance(detail, str) else 'Unexpected preparation response'
        with Path(log).open('a', encoding='utf-8') as stream:
            stream.write('\nPreparation failed: ' + detail + '\n')
        raise RuntimeError('Original grind cache preparation failed: ' + detail + '; see ' + str(log))
    return response


def grind_record(paths, target, manifest, log):
    """Legacy-only verification, retained for explicit recovery/migration tools."""
    prepared = prepare_world_lips(paths['worker'], manifest, log)
    record = original_grind_record(paths, target, prepared)
    save(target / 'grind-preparation.json', prepared)
    return record


def original_grind_record(paths, target, prepared):
    rail_file = Path(prepared['rail_file']).resolve()
    if not rail_file.is_relative_to(target) or not rail_file.is_file():
        raise ValueError('Prepared grind file is outside the private map directory')
    record = {'path': rail_file.relative_to(target).as_posix(), 'bytes': rail_file.stat().st_size,
              'sha256': digest(rail_file), 'worker_sha256': digest(paths['worker'])}
    return record


def prepare_grind_records(paths, target, manifest, log):
    """Publish neither legacy nor expanded metadata until both caches verify."""
    from skate_exposed_cache import prepared_record
    prepared = prepare_world_lips(paths['worker'], manifest, log, assets=paths['assets'], exposed_edges=True)
    original = original_grind_record(paths, target, prepared)
    expanded = prepared_record(target, prepared.get('exposed_edges'), base=prepared['source_fingerprint'])
    expanded['worker_sha256'] = digest(paths['worker'])
    save(target / 'grind-preparation.json', prepared)
    return {'grind_file': original, 'exposed_edges': expanded}


def building_record(target):
    path = Path(target) / 'building-catalogue.json'
    catalogue = check_building_catalogue(path)
    if catalogue['mapping_sha256'] != digest(Path(target) / 'Mappings.usmap'):
        raise ValueError('Building catalogue belongs to different mappings')
    return {'path': path.name, 'bytes': path.stat().st_size, 'sha256': digest(path)}


def game_archive_identity(paks):
    """Bind a resumed export to actual installed bytes, not just Steam metadata."""
    records = []
    for path in sorted(Path(paks).iterdir()):
        if path.is_file() and path.suffix.lower() in ('.pak', '.utoc', '.ucas'):
            before = path.stat()
            hashed = digest(path)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('Game data changed while being verified; let Steam finish updating')
            records.append({'path': path.name, 'bytes': before.st_size, 'sha256': hashed})
    if not records:
        raise ValueError('The installed game has no archive files')
    return records


def probe_identity(probe):
    """World/pawn names and capture time do not change the collision rules."""
    stable = {key: value for key, value in probe.items()
              if key not in ('world', 'pawn', 'setup_provenance')}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def check_probe_origin(probe, paths, compatibility):
    provenance = probe.get('setup_provenance', {})
    if (not isinstance(provenance, dict)
            or Path(provenance.get('game_root', '')).resolve() != paths['game_root']
            or str(provenance.get('game_build')) != str(compatibility.get('steam_build'))
            or provenance.get('dump_attempted') is not True
            or provenance.get('dump_outcome') != 'returned'):
        raise ValueError('Enter a solo world with this mod to capture settings for this game build')
    requested = provenance.get('usmap_requested_at_unix')
    if type(requested) not in (int, float) or not 0 < requested <= time.time() + 60:
        raise ValueError('The mapping dump has no valid capture time')
    return requested


def check_mapping(path, requested):
    path = Path(path).resolve()
    if not path.is_file() or path.suffix.lower() != '.usmap' or path.stat().st_size < 16:
        raise ValueError('Select the mappings dumped from this installed game')
    if path.stat().st_mtime < requested - 2:
        raise ValueError('The mappings predate this capture; close the game, run refresh-probe, then restart into a solo world')
    with path.open('rb') as stream:
        header = stream.read(3)
    # The exact CUE parser shipped with the exporter supports USMAP versions 0..5.
    # Its parser validates the full mapping during export; this rejects stale or
    # unrelated files before a lengthy setup begins.
    if header[:2] != b'\xc4\x30' or header[2] > 5:
        raise ValueError('The selected file is not a supported USMAP mapping')
    return path


def discover_inputs(paths, compatibility, mappings=None, probe_file=None):
    probe_file = Path(probe_file or paths['mailbox'] / 'collision-policy-probe.json').resolve()
    probe = read_json(probe_file, 'In-game collision probe')
    requested = check_probe_origin(probe, paths, compatibility)
    if mappings is not None:
        return check_mapping(mappings, requested), probe_file
    directory = paths['game_root'] / 'RSDragonwilds/Binaries/Win64/ue4ss'
    candidates = []
    for path in directory.glob('RSDragonwilds-*.usmap'):
        try:
            candidates.append(check_mapping(path, requested))
        except ValueError:
            pass
    if len(candidates) != 1:
        raise ValueError('Expected one fresh Dragonwilds mapping dump; select it explicitly with --mappings')
    return candidates[0], probe_file


def refresh_probe(paths, compatibility):
    """Preserve this mod's old capture so the next fresh Lua load dumps again."""
    mailbox = paths['mailbox']
    candidates = []
    for filename, schema in [('collision-policy-probe.json', 'S3COLLISIONPOLICY1'),
                             ('setup-status.json', 'S3SETUP1')]:
        file = mailbox / filename
        if not file.exists():
            continue
        if file.is_symlink() or file.resolve() != file:
            raise ValueError('Refusing a linked setup capture: ' + filename)
        value = read_json(file, 'Setup capture')
        provenance = value.get('setup_provenance', {}) if filename.startswith('collision') else value
        if (value.get('schema') != schema
                or Path(provenance.get('game_root', '')).resolve() != paths['game_root']
                or str(provenance.get('game_build')) != str(compatibility.get('steam_build'))):
            raise ValueError('Refusing an unrecognized setup capture: ' + filename)
        candidates.append(file)
    if not candidates:
        return {'status': 'no_capture', 'message': 'Start Dragonwilds and enter a solo world to capture settings.'}
    archive = mailbox / ('setup-capture-backup-' + str(time.time_ns()))
    archive.mkdir()
    moved = []
    try:
        for file in candidates:
            destination = archive / file.name
            file.rename(destination)
            moved.append((file, destination))
    except BaseException:
        for original, destination in reversed(moved):
            if not original.exists():
                destination.rename(original)
        raise
    return {'status': 'capture_archived', 'backup': str(archive),
            'message': 'Start Dragonwilds again and enter a solo world for a new settings and mapping capture.'}


def archive_map(paths):
    """Preserve one recognized private generation for an explicit offline update."""
    game = Path(paths['game_root']).resolve()
    data = game / 'DragonwildsSkateData'
    target = data / 'map-data'
    configured = Path(os.path.abspath(paths['map_data']))
    if configured != target:
        raise ValueError('Map archiving is limited to this installation\'s private map-data directory')
    for path in (data, target):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()) or path.resolve() != path:
            raise ValueError('Map archiving refuses a linked private data path')
    if not target.is_dir():
        raise ValueError('There is no owned map generation to archive')
    # Check children before following them; a foreign reparse point must never
    # redirect traversal or carry an outside target into an archive operation.
    pending = [target]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('Map archiving refuses linked entries: ' + entry.name)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
    marker = target / 'setup-state.json'
    state = read_json(marker, 'Owned map preparation state')
    identity = state.get('identity', {}) if isinstance(state, dict) else {}
    if (not isinstance(identity, dict) or identity.get('schema') != 1
            or identity.get('product') != 'DragonwildsSkateSetup'
            or not isinstance(identity.get('target'), str)
            or Path(identity.get('target', '')).resolve() != target
            or type(state.get('ready')) is not bool or not isinstance(state.get('completed'), list)
            or any(not isinstance(name, str) for name in state['completed'])):
        raise ValueError('Map generation has no matching setup ownership; all files were preserved')
    marker_bytes = marker.read_bytes()
    backup = data / ('map-data-backup-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex)
    if backup.exists() or backup.is_symlink() or backup.resolve() != backup:
        raise ValueError('Map backup destination already exists or is linked; all files were preserved')
    # Both absolute paths must remain exact siblings inside the selected private
    # data directory before the directory rename. No recursive delete is used.
    if target.resolve().parent != data.resolve() or backup.resolve().parent != data.resolve():
        raise ValueError('Map archive paths leave the private data directory')
    with offline_maintenance(paths):
        if marker.read_bytes() != marker_bytes or backup.exists() or target.resolve() != target:
            raise ValueError('Map preparation changed during archiving; retry with the game and helpers closed')
        target.rename(backup)
    return {'status': 'map_archived', 'backup': str(backup), 'previous_ready': state['ready'],
            'message': 'All old files were preserved. Run prepare-map without --resume for a new generation. '
                       'To restore the old generation, close the game and helpers and move this backup back '
                       'to map-data only while that destination is absent.'}


def prepare_map(paths, compatibility, mappings, probe_file, *, resume=False,
                runner=run_export, build_world=None, archive_identity=game_archive_identity):
    """Resumable owned-file export; never publishes an incomplete world pointer."""
    from authored_policy import generate
    target = Path(paths['map_data']).resolve()
    mappings, probe_file = Path(mappings).resolve(), Path(probe_file).resolve()
    probe = read_json(probe_file, 'In-game collision probe')
    if probe.get('schema') != 'S3COLLISIONPOLICY1' or probe.get('complete') is not True or probe.get('errors'):
        raise ValueError('Enter a solo world to collect a complete collision probe first')
    settings = probe.get('physicsSettings', {})
    if settings.get('engine_default') not in (1, 2, 3):
        raise ValueError('The collision probe lacks the engine collision policy')
    requested = check_probe_origin(probe, paths, compatibility)
    check_mapping(mappings, requested)
    paks = Path(paths['game_root']) / 'RSDragonwilds/Content/Paks'
    identity = {'schema': 1, 'product': 'DragonwildsSkateSetup', 'target': str(target),
                'compatibility': compatibility, 'mapping_sha256': digest(mappings),
                'probe_policy_sha256': probe_identity(probe), 'exporter_sha256': digest(paths['map_export']),
                'preparation': map_preparation_identity(Path(__file__).parent, paths, compatibility),
                'game_archives': archive_identity(paks)}
    marker = target / 'setup-state.json'
    if resume:
        state = read_json(marker, 'Map preparation state')
        if state.get('identity') != identity:
            raise ValueError('This map preparation belongs to different inputs; close the game and helpers, '
                             'run archive-map to preserve it, then run prepare-map without --resume')
    else:
        fresh_directory(target)
        state = {'identity': identity, 'completed': [], 'ready': False}
        save(marker, state)
    if state.get('ready'):
        # A geometry or compatibility change needs a fresh generation. A missing
        # grind file alone can be repaired offline without exporting the map again.
        check_map(target, expected_preparation=identity['preparation'], require_grind=False)
        try:
            check_map(target, expected_preparation=identity['preparation'], worker_sha256=digest(paths['worker']),
                      require_exposed=True, verify_exposed_payloads=True)
        except PreflightError:
            pointer = read_json(target / 'compact-world-base.json', 'Prepared map')
            pointer.update(prepare_grind_records(paths, target, contained(target, pointer['manifest']),
                                                target / 'setup-logs/grind-rails.log'))
            save(target / 'compact-world-base.json', pointer)
            check_map(target, expected_preparation=identity['preparation'], worker_sha256=digest(paths['worker']), require_exposed=True)
        return state
    logs = target / 'setup-logs'
    logs.mkdir(exist_ok=True)
    local_mapping = target / 'Mappings.usmap'
    if local_mapping.exists() and digest(local_mapping) != identity['mapping_sha256']:
        raise ValueError('Private mappings changed during preparation')
    if not local_mapping.exists():
        shutil.copy2(mappings, local_mapping)
    if digest(local_mapping) != identity['mapping_sha256']:
        raise ValueError('Mappings changed during copying; the generation was not published')
    save(target / 'collision-policy-source.json', probe)
    def step(name, callback):
        if name in state['completed']:
            return
        state['stage'] = name
        save(marker, state)
        print('Preparing map: ' + name, flush=True)
        callback()
        state['completed'].append(name)
        save(marker, state)
    configs = target / 'owned-config'
    step('collision-settings', lambda: runner(paths['map_export'], [paks, configs, 'config'], logs / 'config.log'))
    def make_policy():
        texts, hashes = [], {}
        for name in ('BaseEngine.ini', 'DefaultEngine.ini'):
            raw = (configs / name).read_bytes()
            texts.append(raw.decode('utf-8-sig'))
            hashes[name] = hashlib.sha256(raw).hexdigest()
        save(target / 'authored-policy.json', generate(probe, texts, hashes))
    step('collision-policy', make_policy)
    step('terrain', lambda: runner(paths['map_export'], [paks, target, local_mapping, '/Maps/World/', 'terrain'], logs / 'terrain.log'))
    step('objects-and-buildings', lambda: runner(paths['map_export'], [paks, target, local_mapping, '/Maps/World/', 'static-authored', target / 'authored-policy.json'], logs / 'objects.log'))
    if archive_identity(paks) != identity['game_archives']:
        raise ValueError('Game files changed during export; this generation cannot be published')
    def default_build():
        from skate_map_cache import MapCache
        from skate_world_export import export_world, authoritative_inputs, validate_export
        world = MapCache(target)
        if world.authored is None or not world.terrain_meta:
            raise ValueError('The exported map has no complete authored world or terrain')
        destination = target / 'compact-world-v1'
        exported = export_world(destination, cache=world, physics_settings=settings)
        validate_export(destination / 'manifest.json')
        print('Preparing map: exposed ledges and rails (saved for future launches)', flush=True)
        rails = prepare_grind_records(paths, target, destination / 'manifest.json', logs / 'grind-rails.log')
        # Publish only after all source checks and exact binary checksums passed.
        save(target / 'compact-world-base.json', {'schema': 1,
            'inputs': authoritative_inputs(world, settings['engine_default']),
            'manifest': 'compact-world-v1/manifest.json', 'source_fingerprint': exported['source_fingerprint'],
            'preparation': identity['preparation'], **rails})
    step('complete-world-index', build_world or default_build)
    if not (target / 'compact-world-base.json').is_file():
        raise ValueError('No complete world was published')
    state['ready'], state['stage'], state['finished_unix'] = True, 'ready', time.time()
    save(marker, state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    assets = commands.add_parser('import-assets')
    assets.add_argument('source', type=Path, help='Assets folder prepared from your owned Skate 3 files')
    maps = commands.add_parser('prepare-map')
    maps.add_argument('--mappings', type=Path, help='Optional explicit fresh UE4SS mapping dump')
    maps.add_argument('--probe', type=Path, help='Optional explicit setup collision probe')
    maps.add_argument('--resume', action='store_true')
    commands.add_parser('refresh-probe', help='With the game closed, preserve an old capture and request a new one on next launch')
    commands.add_parser('archive-map', help='With the game/helpers closed, preserve an owned map generation for a fresh preparation')
    commands.add_parser('prepare-buildings', help='With the game/helpers closed, prepare only supplementary building metadata')
    buildings=commands.add_parser('import-buildings',help='Import a verified owned supplement with identical base/game/tool/mapping pins')
    buildings.add_argument('source',type=Path,help='Private map-data directory containing the verified supplement')
    args = parser.parse_args()
    manifest, paths = check_app(Path(__file__).resolve().parent)
    if args.operation == 'import-assets':
        result = import_assets(args.source, paths['assets'])
    elif args.operation == 'refresh-probe':
        result = refresh_probe(paths, manifest['compatibility'])
    elif args.operation == 'archive-map':
        result = archive_map(paths)
    elif args.operation in ('prepare-buildings','import-buildings'):
        from skate_building_setup import prepare_buildings
        result=prepare_buildings(paths,manifest,source=args.source if args.operation=='import-buildings' else None)
    else:
        mappings, probe = discover_inputs(paths, manifest['compatibility'], args.mappings, args.probe)
        result = prepare_map(paths, manifest['compatibility'], mappings, probe, resume=args.resume)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
