"""Offline compatibility checks shared by the public installer and launcher.

The release builder supplies release-manifest.json. Private game assets and
generated collision are referenced by runtime.json, never added to that bundle.
"""
from pathlib import Path, PurePosixPath
import hashlib
import json
import re
import struct

from runtime_paths import load_paths


class PreflightError(ValueError):
    pass


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def contained(root, relative):
    if not isinstance(relative, str) or not relative or any(c in relative for c in '\\:\0'):
        raise PreflightError('Package paths must be relative and use forward slashes')
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(p in ('.', '..') for p in relative.split('/')):
        raise PreflightError('Package path escapes its directory')
    root = Path(root).resolve()
    result = (root / relative).resolve()
    if not result.is_relative_to(root):
        raise PreflightError('Package path escapes its directory')
    return result


def read_json(path, label):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as error:
        raise PreflightError(f'{label} is missing or invalid: {error}') from error


def check_package(root):
    root = Path(root).resolve()
    manifest = read_json(root / 'release-manifest.json', 'Release manifest')
    if not isinstance(manifest, dict) or manifest.get('schema') != 1 or manifest.get('product') != 'DragonwildsSkate':
        raise PreflightError('Unsupported release manifest')
    records = manifest.get('files')
    if not isinstance(records, list) or not records:
        raise PreflightError('Release manifest contains no files')
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise PreflightError('Invalid release file record')
        name = record.get('path')
        path = contained(root, name)
        if name.casefold() in seen:
            raise PreflightError('Duplicate release file: ' + name)
        seen.add(name.casefold())
        expected = record.get('sha256', '')
        size = record.get('bytes')
        if not re.fullmatch('[0-9a-f]{64}', expected) or type(size) is not int or size < 0:
            raise PreflightError('Invalid release file record: ' + name)
        if not path.is_file() or path.stat().st_size != size or digest(path) != expected:
            raise PreflightError('Release file is missing or changed: ' + name)
    return manifest


def check_game(game_root, compatibility):
    game = Path(game_root).resolve()
    win64 = game / 'RSDragonwilds/Binaries/Win64'
    required = {
        'game_exe_sha256': win64 / 'RSDragonwilds-Win64-Shipping.exe',
        'ue4ss_sha256': win64 / 'ue4ss/UE4SS.dll',
    }
    for key, path in required.items():
        expected = compatibility.get(key, '')
        if not re.fullmatch('[0-9a-f]{64}', expected):
            raise PreflightError('Release does not specify tested compatibility: ' + key)
        if not path.is_file() or digest(path) != expected:
            label = 'Dragonwilds executable' if key == 'game_exe_sha256' else 'UE4SS loader'
            raise PreflightError(label + ' does not match this tested mod version')
    # Steam's installed build also identifies data changes that reuse an EXE.
    steam_manifest = game.parent.parent / 'appmanifest_1374490.acf'
    try:
        acf = steam_manifest.read_text(encoding='utf-8-sig')
    except OSError as error:
        raise PreflightError('Cannot verify the Dragonwilds Steam build') from error
    build = re.search(r'"buildid"\s+"(\d+)"', acf)
    if not build or build.group(1) != str(compatibility.get('steam_build')):
        raise PreflightError('Dragonwilds game data is from a different Steam build')
    if not (game / 'RSDragonwilds/Content/Paks').is_dir():
        raise PreflightError('Dragonwilds content directory is missing')
    return {'steam_build': build.group(1), 'game_root': str(game)}


def check_external_prerequisites(game_root, records):
    if not records:
        return
    required = {'dwmapi.dll', 'ue4ss/UE4SS.dll', 'ue4ss/Mods/shared/UEHelpers/UEHelpers.lua'}
    if (not isinstance(records, list) or len(records) != 3
            or any(not isinstance(row, dict) for row in records)
            or {row.get('destination') for row in records} != required):
        raise PreflightError('External UE4SS prerequisite manifest is incomplete')
    root = Path(game_root) / 'RSDragonwilds/Binaries/Win64'
    for row in records:
        path = contained(root, row['destination'])
        if (not re.fullmatch('[0-9a-f]{64}', str(row.get('sha256', '')))
                or type(row.get('bytes')) is not int or row['bytes'] <= 0
                or not path.is_file() or path.stat().st_size != row['bytes'] or digest(path) != row['sha256']):
            raise PreflightError('External UE4SS prerequisite is missing or changed: ' + row['destination'])


def check_assets(root):
    root = Path(root).resolve()
    manifest = read_json(root / 'private/game.json', 'Prepared Skate assets')
    fields = {'version', 'character_scene', 'initial_animation', 'action_graph', 'motion_graph'}
    if not isinstance(manifest, dict) or set(manifest) != fields or type(manifest['version']) is not int or manifest['version'] != 1:
        raise PreflightError('Unsupported prepared Skate asset manifest')
    if not isinstance(manifest['initial_animation'], str) or not manifest['initial_animation'].strip():
        raise PreflightError('Prepared Skate assets lack an initial animation')
    for key, suffix in [('character_scene', '.glb'), ('action_graph', '.stategraph'), ('motion_graph', '.stategraph')]:
        path = contained(root, manifest[key])
        if path.suffix != suffix or not path.is_file() or path.stat().st_size == 0:
            raise PreflightError('Prepared Skate asset is missing: ' + key)
    # The current visual adapter consumes the example's canonical character GLB.
    if manifest['character_scene'] != 'private/skater.glb':
        raise PreflightError('This adapter requires the example converter character layout')
    glb = root / 'private/skater.glb'
    with glb.open('rb') as stream:
        header = stream.read(12)
    if len(header) != 12 or struct.unpack('<III', header) != (0x46546c67, 2, glb.stat().st_size):
        raise PreflightError('Prepared character GLB is invalid')
    for relative in ['private/stock/physics-skeletons.json', 'private/stock/skater-collections.json']:
        path = root / relative
        if not path.is_file() or path.stat().st_size == 0:
            raise PreflightError('Prepared Skate asset is missing: ' + relative)
    return {'manifest_version': 1, 'assets': str(root)}


def map_preparation_identity(app, paths, compatibility):
    """Bind authored geometry to its exact exporter and collision-policy code."""
    app = Path(app).resolve()
    names = ('authored_policy.py', 'skate_authored_gate.py', 'skate_map_cache.py',
             'skate_spline_collision.py', 'skate_world_export.py')
    exporter_root = Path(paths['map_export']).parent.resolve()
    files = []
    for path in sorted(exporter_root.rglob('*')):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()) or not path.resolve().is_relative_to(exporter_root):
            raise PreflightError('Exporter contains a linked path')
        if path.is_file():
            files.append({'path': path.relative_to(exporter_root).as_posix(),
                          'bytes': path.stat().st_size, 'sha256': digest(path)})
    inventory_hash = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'compatibility': compatibility, 'exporter_inventory_sha256': inventory_hash,
            'modules': {name: digest(app / name) for name in names}}


def check_building_catalogue(path):
    catalogue = read_json(path, 'Prepared building catalogue')
    if (not isinstance(catalogue, dict) or catalogue.get('magic') != 'S3BL1'
            or catalogue.get('schema') != 1 or catalogue.get('complete') is not True
            or catalogue.get('errors') != [] or not isinstance(catalogue.get('data'), dict)
            or not catalogue['data'] or not isinstance(catalogue.get('derived'), dict)
            or not catalogue['derived'] or not re.fullmatch('[0-9a-f]{64}', str(catalogue.get('mapping_sha256', '')))):
        raise PreflightError('Building metadata is missing or incomplete; run offline map setup')
    return catalogue


def check_map(root, *, expected_preparation=None, worker_sha256=None, require_grind=True, require_buildings=False,
              require_exposed=False, verify_exposed_payloads=False):
    """Verify the published base and every compact binary before starting a relay."""
    root = Path(root).resolve()
    pointer = read_json(root / 'compact-world-base.json', 'Prepared Dragonwilds map')
    if not isinstance(pointer, dict) or pointer.get('schema') != 1 or not re.fullmatch(
            '[0-9a-f]{64}', str(pointer.get('source_fingerprint', ''))):
        raise PreflightError('Unsupported prepared-map pointer')
    if expected_preparation is not None and pointer.get('preparation') != expected_preparation:
        raise PreflightError('Prepared map belongs to a different game build or collision exporter')
    manifest_path = contained(root, pointer.get('manifest'))
    from skate_world_export import validate_export
    try:
        world = validate_export(manifest_path)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise PreflightError('Prepared Dragonwilds map is incomplete or changed: ' + str(error)) from error
    if world['source_fingerprint'] != pointer['source_fingerprint']:
        raise PreflightError('Prepared-map fingerprint mismatch')
    result = {'map_manifest': str(manifest_path), 'map_fingerprint': pointer['source_fingerprint']}
    if require_buildings:
        record = pointer.get('building_catalogue', {})
        if (not isinstance(record, dict) or record.get('path') != 'building-catalogue.json'
                or type(record.get('bytes')) is not int or record['bytes'] <= 0
                or not re.fullmatch('[0-9a-f]{64}', str(record.get('sha256', '')))):
            raise PreflightError('Building metadata is not prepared; preserve the old generation with archive-map, then run prepare-map')
        catalogue_file = contained(root, record['path'])
        if (not catalogue_file.is_file() or catalogue_file.stat().st_size != record['bytes']
                or digest(catalogue_file) != record['sha256']):
            raise PreflightError('Building metadata is missing or changed; preserve it with archive-map, then run prepare-map')
        check_building_catalogue(catalogue_file)
        result['building_catalogue'] = str(catalogue_file)
    if require_grind:
        record = pointer.get('grind_file', {})
        if not isinstance(record, dict) or not re.fullmatch('[0-9a-f]{64}', str(record.get('sha256', ''))):
            raise PreflightError('Original grind cache is not prepared; run offline map setup')
        rail_file = contained(root, record.get('path'))
        if (type(record.get('bytes')) is not int or record['bytes'] <= 0
                or not rail_file.is_file() or rail_file.stat().st_size != record['bytes']
                or digest(rail_file) != record['sha256']):
            raise PreflightError('Original grind cache is missing or changed; resume offline map setup')
        if worker_sha256 is not None and record.get('worker_sha256') != worker_sha256:
            raise PreflightError('Original grind cache needs verification with this worker; resume offline map setup')
        result['rail_file'] = str(rail_file)
    if require_exposed:
        from skate_exposed_cache import check_record
        record = pointer.get('exposed_edges')
        try:
            expanded = check_record(root, record, base=pointer['source_fingerprint'], verify_payloads=verify_exposed_payloads)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise PreflightError('Exposed ledges and rails need preparation; resume offline map setup: ' + str(error)) from error
        if worker_sha256 is not None and record.get('worker_sha256') != worker_sha256:
            raise PreflightError('Exposed edges need verification with this worker; resume offline map setup')
        result['exposed_edge_manifest'] = str(contained(root, expanded['path']))
        result['exposed_edge_algorithm'] = expanded['algorithm_sha256']
    return result


def check_installation(app_root):
    app = Path(app_root).resolve()
    package = app.parent
    manifest = check_package(package)
    paths = load_paths(app)
    if 'game_root' not in paths:
        raise PreflightError('Run setup to choose the Dragonwilds installation')
    game = check_game(paths['game_root'], manifest.get('compatibility', {}))
    check_external_prerequisites(paths['game_root'], manifest.get('install', {}).get('external_prerequisites', []))
    assets = check_assets(paths['assets'])
    world = check_map(paths['map_data'],
        expected_preparation=map_preparation_identity(app, paths, manifest.get('compatibility', {})),
        worker_sha256=digest(paths['worker']), require_exposed=True)
    # Every executable/helper is one of the exact files verified above. Data,
    # logs and owned prepared assets may live outside the release directory.
    records = {contained(package, row['path']) for row in manifest['files']}
    for key in ['worker', 'transport_library', 'lua_transport_library', 'render_library', 'map_export', 'gamepad_library', 'mount_icon']:
        if paths[key] not in records:
            raise PreflightError('Runtime file is not part of this release: ' + key)
    for key in ['python_runtime', 'relay_launcher', 'board_deck_texture','building_reader_library','building_export','building_rule']:
        if key in paths and paths[key] not in records:
            raise PreflightError('Launcher file is not part of this release: ' + key)
    if 'building_rule' in manifest.get('install',{}).get('runtime',{}):
        from skate_building_setup import verify_building_supplement
        buildings=verify_building_supplement(paths,manifest,world['map_fingerprint'])
        world['building_catalogue']=str(buildings['catalogue_path'])
    return {'schema': 1, 'release': manifest.get('version'), **game, **assets, **world}
