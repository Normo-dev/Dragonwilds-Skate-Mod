"""Receipt-based installation. No game processes, input, or saved data are touched.

Release builders place this module beside skate_preflight.py in package/app.
All mutation is confined to the explicitly selected, verified game directory.
"""
from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from skate_preflight import PreflightError, check_game, check_package, digest, read_json

PRODUCT = 'DragonwildsSkate'
PACKAGE = PRODUCT
DATA = PRODUCT + 'Data'
MAILBOX = DATA + '/mailbox'
WIN64 = 'RSDragonwilds/Binaries/Win64'
MOD = WIN64 + '/ue4ss/Mods/' + PRODUCT
STATE = DATA + '/install'
RECEIPT = STATE + '/receipt.json'
LOADER_BINARIES = {WIN64 + '/' + p for p in (
    'dwmapi.dll', 'ue4ss/UE4SS.dll')}
LOADER_CONFIGS = {WIN64 + '/' + p for p in ('ue4ss/UE4SS-settings.ini', 'ue4ss/Mods/mods.txt')}
SHARED_LUA = WIN64 + '/ue4ss/Mods/shared/'
UEHELPERS = SHARED_LUA + 'UEHelpers/UEHelpers.lua'
RUNTIME_REQUIRED = {'worker', 'transport_library', 'lua_transport_library', 'render_library',
                    'map_export', 'gamepad_library', 'mount_icon', 'python_runtime', 'relay_launcher'}
RUNTIME_OPTIONAL = {'dotnet', 'board_deck_texture', 'building_reader_library', 'building_export', 'building_rule'}
RESERVED = re.compile(r'^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)
SHA = re.compile(r'^[0-9a-f]{64}$')


class InstallError(PreflightError):
    pass


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def relative(name):
    if not isinstance(name, str) or not name or any(c in name for c in '\\:\0'):
        raise InstallError('Expected a relative path using forward slashes')
    parts = name.split('/')
    if PurePosixPath(name).is_absolute() or any(not p or p in ('.', '..') or p[-1] in '. '
            or RESERVED.match(p) or any(ord(c) < 32 or c in '<>"|?*' for c in p) for p in parts):
        raise InstallError('Unsafe package or receipt path: ' + name)
    return name


def safe(root, name):
    """Reject traversal and every symlink/junction below the chosen real root."""
    relative(name)
    root = Path(root).resolve()
    path = root
    for part in name.split('/'):
        path = path / part
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise InstallError('Symlinks and junctions cannot be installation targets: ' + name)
    result = path.resolve()
    if not result.is_relative_to(root) or result == root:
        raise InstallError('Path escapes the selected directory: ' + name)
    return result


def file_hash(path):
    if path.exists() and not path.is_file():
        raise InstallError('A directory blocks a required file: ' + str(path))
    return digest(path) if path.is_file() else None


def mailbox_directory(game):
    path = safe(game, MAILBOX)
    if path.exists() and not path.is_dir():
        raise InstallError('A file blocks the private mailbox directory: ' + str(path))
    return path


def owned_path(name):
    relative(name)
    return (name.startswith(PACKAGE + '/') or name.startswith(MOD + '/Scripts/')
            or name == MOD + '/enabled.txt')


def loader_path(name):
    return name in LOADER_BINARIES | LOADER_CONFIGS or (name.startswith(SHARED_LUA) and name.endswith('.lua'))


def game_identity(game, compatibility=None):
    game = Path(game).resolve()
    exe = safe(game, WIN64 + '/RSDragonwilds-Win64-Shipping.exe')
    if not exe.is_file() or not safe(game, 'RSDragonwilds/Content/Paks').is_dir():
        raise InstallError('Select the Dragonwilds game folder containing RSDragonwilds/Binaries and Content/Paks')
    if compatibility is not None:
        expected = compatibility.get('game_exe_sha256', '')
        if not SHA.fullmatch(expected) or digest(exe) != expected:
            raise InstallError('Dragonwilds executable does not match this tested release')
        acf = game.parent.parent / 'appmanifest_1374490.acf'
        try:
            build = re.search(r'"buildid"\s+"(\d+)"', acf.read_text(encoding='utf-8-sig'))
        except OSError as error:
            raise InstallError('Cannot verify the Dragonwilds Steam build') from error
        if not build or build[1] != str(compatibility.get('steam_build')):
            raise InstallError('Dragonwilds game data does not match this tested Steam build')
    return game


def prototype_gate(game):
    probe = WIN64 + '/ue4ss/Mods/DragonwildsSkateProbe'
    enabled = safe(game, probe + '/enabled.txt').is_file()
    mods = safe(game, WIN64 + '/ue4ss/Mods/mods.txt')
    if mods.is_file():
        for line in mods.read_text(encoding='utf-8-sig').splitlines():
            line = re.split(r'[#;]', line, 1)[0].strip()
            if re.fullmatch(r'DragonwildsSkateProbe\s*:\s*1', line, re.I):
                enabled = True
    if enabled:
        raise InstallError('DragonwildsSkateProbe is enabled. Disable the prototype using its existing manager before installing this release; setup will not change its unowned files.')


def load_receipt(game):
    path = safe(game, RECEIPT)
    if not path.exists():
        return None
    receipt = read_json(path, 'Installation receipt')
    if (receipt.get('schema') != 1 or receipt.get('product') != PRODUCT
            or Path(receipt.get('game_root', '')).resolve() != game or not isinstance(receipt.get('files'), list)):
        raise InstallError('Installation receipt does not belong to this game directory')
    seen = set()
    for row in receipt['files']:
        name = relative(row.get('path'))
        if name.casefold() in seen or not owned_path(name) or not SHA.fullmatch(row.get('sha256', '')):
            raise InstallError('Invalid owned file in installation receipt')
        seen.add(name.casefold())
        safe(game, name)
    return receipt


@dataclass
class File:
    path: str
    sha256: str
    kind: str
    source: Path | None = None
    data: bytes | None = None


@dataclass
class Plan:
    game: Path
    package: Path
    manifest: dict
    files: list[File]
    shared: list[dict]
    previous: dict | None

    def summary(self):
        return {'action': 'install', 'game_root': str(self.game), 'version': self.manifest['version'],
                'payload_files': len(self.files), 'shared_loader_files': self.shared,
                'private_data': str(self.game / DATA), 'upgrade': self.previous is not None}


def external_prerequisites(game, spec):
    rows=spec.get('external_prerequisites',[])
    if not isinstance(rows,list):raise InstallError('Invalid external prerequisite list')
    result=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'destination','sha256','bytes'}:
            raise InstallError('External prerequisite requires destination, sha256 and bytes')
        destination=WIN64+'/'+relative(row['destination'])
        if destination not in LOADER_BINARIES|{UEHELPERS} or destination in seen:
            raise InstallError('Unsupported or duplicate external prerequisite')
        seen.add(destination)
        if not isinstance(row['sha256'],str) or not SHA.fullmatch(row['sha256']) or type(row['bytes']) is not int or row['bytes']<=0:
            raise InstallError('Invalid external prerequisite byte identity')
        path=safe(game,destination)
        if not path.is_file() or path.stat().st_size!=row['bytes'] or file_hash(path)!=row['sha256']:
            raise InstallError('Install the exact documented UE4SS prerequisite first; missing or incompatible file: '+destination)
        result.append({'path':destination,'sha256':row['sha256'],'bytes':row['bytes'],
                       'status':'external_prerequisite','preserve_on_uninstall':True})
    if rows and seen!=LOADER_BINARIES|{UEHELPERS}:
        raise InstallError('External UE4SS prerequisite mode requires all three reviewed files')
    return result


def plan_install(package, game):
    package = Path(package).resolve()
    manifest = check_package(package)
    if not isinstance(manifest.get('version'), str) or not manifest['version'].strip():
        raise InstallError('Release version is missing')
    spec = manifest.get('install', {})
    if not isinstance(spec, dict) or spec.get('schema') != 1:
        raise InstallError('A complete install schema 1 payload is required')
    records = {relative(row['path']): row for row in manifest['files']}
    for name in records:
        safe(package, name)
    if any(name.casefold() in ('app/runtime.json', 'release-manifest.json') for name in records):
        raise InstallError('Generated runtime settings and the release manifest cannot be listed payload files')
    runtime = spec.get('runtime')
    if not isinstance(runtime, dict) or not RUNTIME_REQUIRED <= runtime.keys() or runtime.keys() - RUNTIME_REQUIRED - RUNTIME_OPTIONAL:
        raise InstallError('Required native helpers, Python runtime, exporter, launcher, and icon must all be supplied')
    for key, name in runtime.items():
        if not isinstance(name, str) or name not in records or records[name]['bytes'] == 0:
            raise InstallError('Runtime payload is missing: ' + key)
    if runtime['relay_launcher'] != 'app/skate_launcher.py':
        raise InstallError('The launcher must be supplied as app/skate_launcher.py')
    if Path(runtime['map_export']).suffix.lower() == '.dll' and 'dotnet' not in runtime:
        raise InstallError('A DLL exporter requires a packaged dotnet runtime')
    if Path(runtime['map_export']).suffix.lower() not in ('.exe', '.dll'):
        raise InstallError('The map exporter must be an EXE or DLL')
    if 'building_reader_library' in runtime and Path(runtime['building_reader_library']).suffix.lower() != '.dll':
        raise InstallError('The building reader must be a DLL')
    if 'building_export' in runtime and Path(runtime['building_export']).suffix.lower() != '.exe':
        raise InstallError('The supplementary building exporter must be a self-contained EXE')
    if 'building_rule' in runtime and (runtime['building_rule']!='config/building-collision-rule.json'
            or not {'building_reader_library','building_export'}<=runtime.keys()):
        raise InstallError('The building rule requires its pinned reader and supplementary exporter')
    for name in ('app/runtime_paths.py', 'app/skate_preflight.py', 'app/skate_launcher.py', 'app/skate_install.py'):
        if name not in records or not records[name]['bytes']:
            raise InstallError('Required application module is missing: ' + name)
    game = game_identity(game, manifest.get('compatibility', {}))
    mailbox_directory(game)
    if package.is_relative_to(game / PACKAGE) or (game / PACKAGE).is_relative_to(package):
        raise InstallError('Install from a separate extracted release, not the installed package folder')
    prototype_gate(game)
    previous = load_receipt(game)
    files = [File(PACKAGE + '/' + name, row['sha256'], 'package', source=safe(package, name))
             for name, row in records.items()]
    raw_manifest = (package / 'release-manifest.json').read_bytes()
    if json.loads(raw_manifest.decode('utf-8-sig')) != manifest:
        raise InstallError('Release manifest changed during validation')
    files.append(File(PACKAGE + '/release-manifest.json', sha(raw_manifest), 'package', data=raw_manifest))
    hosts = spec.get('host_files')
    if not isinstance(hosts, list) or not hosts:
        raise InstallError('The release contains no host entrypoint')
    main = False
    for row in hosts:
        if not isinstance(row, dict):
            raise InstallError('Invalid host file record')
        source, destination = row.get('source'), relative(row.get('destination'))
        if source not in records or not records[source]['bytes'] or not destination.endswith('.lua') or destination.casefold() == 'skate_config.lua':
            raise InstallError('Invalid host script or generated configuration collision')
        main |= destination == 'main.lua'
        files.append(File(MOD + '/Scripts/' + destination, records[source]['sha256'], 'host', source=safe(package, source)))
    if not main:
        raise InstallError('The release contains no main.lua host entrypoint')
    install_root = game / PACKAGE
    paths = {key: str(install_root / value) for key, value in runtime.items()}
    paths.update(game_root=str(game), assets=str(game / DATA / 'assets'), map_data=str(game / DATA / 'map-data'), mailbox=str(game / DATA / 'mailbox'))
    raw = encoded({'schema': 1, 'paths': paths})
    files.append(File(PACKAGE + '/app/runtime.json', sha(raw), 'generated', data=raw))
    lua = '-- Generated for this installation by DragonwildsSkate setup.\nreturn {\n'
    lua_values = {'mailbox': paths['mailbox'].replace('\\', '/') + '/',
                       'transport_library': paths['lua_transport_library'].replace('\\', '/'),
                       'mount_icon': paths['mount_icon'].replace('\\', '/'),
                       'assets': paths['assets'].replace('\\', '/') + '/',
                       'map_data': paths['map_data'].replace('\\', '/') + '/',
                       'game_root': str(game).replace('\\', '/'),
                       'game_build': str(manifest['compatibility']['steam_build']),
                       'game_exe_sha256': manifest['compatibility']['game_exe_sha256'],
                       'python_runtime': paths['python_runtime'].replace('\\', '/'),
                       'relay_launcher': paths['relay_launcher'].replace('\\', '/')}
    if 'building_reader_library' in paths:
        lua_values['building_reader_library'] = paths['building_reader_library'].replace('\\', '/')
    if {'building_reader_library', 'building_export', 'building_rule'} <= paths.keys():
        lua_values['building_collision'] = True
    for key, value in lua_values.items():
        lua += f'    {key}={json.dumps(value, ensure_ascii=False)},\n'
    raw = (lua + '}\n').encode('utf8')
    files.append(File(MOD + '/Scripts/skate_config.lua', sha(raw), 'generated', data=raw))
    files.append(File(MOD + '/enabled.txt', sha(b''), 'enable', data=b''))
    shared = external_prerequisites(game,spec)
    loaders = spec.get('loader_files', [])
    if not isinstance(loaders, list):
        raise InstallError('Invalid loader file list')
    loader_destinations = {row['path'] for row in shared}
    for row in loaders:
        if not isinstance(row, dict):
            raise InstallError('Invalid loader file record')
        source, destination = row.get('source'), WIN64 + '/' + relative(row.get('destination'))
        if source not in records or not loader_path(destination) or destination in loader_destinations:
            raise InstallError('Unsupported or duplicate loader destination')
        loader_destinations.add(destination)
        existing = file_hash(safe(game, destination))
        expected = records[source]['sha256']
        if destination not in LOADER_CONFIGS and existing and existing != expected:
            raise InstallError('Existing loader differs; setup never replaces shared loader binaries: ' + destination)
        if existing is None:
            files.append(File(destination, expected, 'shared_loader', source=safe(package, source)))
        shared.append({'path': destination, 'sha256': existing or expected,
                       'status': 'reused' if existing else 'installed_shared', 'preserve_on_uninstall': True})
    if not LOADER_BINARIES <= loader_destinations:
        raise InstallError('The tested dwmapi.dll proxy and ue4ss/UE4SS.dll must both be specified, including for loader reuse')
    if UEHELPERS not in loader_destinations:
        raise InstallError('The required shared UEHelpers/UEHelpers.lua must be supplied and hash-checked')
    loader = WIN64 + '/ue4ss/UE4SS.dll'
    expected_loader = manifest.get('compatibility', {}).get('ue4ss_sha256', '')
    selected_loader = next((f.sha256 for f in files if f.path == loader), file_hash(safe(game, loader)))
    if not SHA.fullmatch(expected_loader) or selected_loader != expected_loader:
        raise InstallError('A hash-compatible UE4SS.dll must already exist or be supplied by this release')
    if not any(row['path'] == loader for row in shared):
        shared.append({'path': loader, 'sha256': selected_loader, 'status': 'reused', 'preserve_on_uninstall': True})
    seen = set()
    owned = {r['path']: r for r in previous['files']} if previous else {}
    for item in files:
        if item.path.casefold() in seen:
            raise InstallError('Duplicate installation target: ' + item.path)
        seen.add(item.path.casefold())
        current = file_hash(safe(game, item.path))
        if current is not None and item.kind != 'shared_loader':
            if item.path not in owned:
                raise InstallError('Unowned file collision; no files were changed: ' + item.path)
            if current != owned[item.path]['sha256']:
                raise InstallError('Installed file was modified; preserve or resolve it before updating: ' + item.path)
    for row in owned.values():
        current = file_hash(safe(game, row['path']))
        if current is not None and current != row['sha256']:
            raise InstallError('Installed file was modified; update refused: ' + row['path'])
    return Plan(game, package, manifest, files, shared, previous)


def state_marker(game):
    path = safe(game, STATE + '/owner.json')
    folder = safe(game, STATE)
    if path.exists():
        if read_json(path, 'Installer state owner') != {'schema': 1, 'product': PRODUCT}:
            raise InstallError('Installer state folder has an unknown owner')
    else:
        if folder.exists() and any(folder.iterdir()):
            raise InstallError('Refusing to use an existing unowned installer state folder')
        folder.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(encoded({'schema': 1, 'product': PRODUCT}))


@contextlib.contextmanager
def install_lock(game):
    state_marker(game)
    lock = safe(game, STATE + '/install.lock')
    with lock.open('a+b') as stream:
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise InstallError('Another setup or uninstall operation is running') from error
        try:
            # Windows byte-range locks also deny reads through a second file
            # handle. Acquire the range before reading/initializing its byte.
            stream.seek(0)
            if stream.read(1) == b'':
                stream.write(b'0'); stream.flush()
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def atomic_state(folder, name, data):
    destination = safe(folder, name)
    temporary = safe(folder, name + '.tmp')
    with temporary.open('wb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, destination)


def journal_valid(game, folder, journal):
    if journal.get('schema') != 1 or journal.get('product') != PRODUCT or journal.get('game_root') != str(game):
        raise InstallError('Unknown transaction journal')
    seen = set()
    for row in journal.get('operations', []):
        name = row.get('path')
        if (not (owned_path(name) or loader_path(name) or name == RECEIPT)
                or name.casefold() in seen):
            raise InstallError('Invalid transaction target')
        seen.add(name.casefold()); safe(game, name)
        for key in ('before', 'after'):
            if row.get(key) is not None and not SHA.fullmatch(row[key]):
                raise InstallError('Invalid transaction file hash')
        if row['before'] is not None:
            backup = safe(folder, row['backup'])
            if not backup.is_file() or digest(backup) != row['before']:
                raise InstallError('Transaction backup is missing or changed')


def rollback(game, folder, journal):
    journal_valid(game, folder, journal)
    # Inspect everything before restoring anything. Unknown edits are retained.
    for row in journal['operations']:
        current = file_hash(safe(game, row['path']))
        if current not in (row['before'], row['after']):
            raise InstallError('Rollback stopped to preserve an independently changed file: ' + row['path'])
    for index, row in reversed(list(enumerate(journal['operations']))):
        destination = safe(game, row['path'])
        if file_hash(destination) == row['before']:
            continue
        if row['before'] is None:
            destination.unlink()
        else:
            temporary = safe(folder, f'restore-{index}.tmp')
            shutil.copy2(safe(folder, row['backup']), temporary)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, safe(game, row['path']))
    journal['status'] = 'rolled_back'
    atomic_state(folder, 'journal.json', encoded(journal))


def recover_pending(game):
    transactions = safe(game, STATE + '/transactions')
    if not transactions.exists():
        return []
    recovered = []
    for folder in sorted(transactions.iterdir()):
        safe(game, folder.relative_to(game).as_posix())
        if not folder.is_dir():
            raise InstallError('Unexpected file in installer transaction directory')
        path = safe(folder, 'journal.json')
        if not path.exists():
            continue  # Preparation failed before any installation file was changed.
        journal = read_json(path, 'Transaction journal')
        if journal.get('status') in ('prepared', 'applying'):
            rollback(game, folder, journal); recovered.append(folder.name)
        elif journal.get('status') not in ('committed', 'rolled_back'):
            raise InstallError('Unknown transaction state')
    return recovered


def transaction(game, changes, receipt, *, validate=None, checkpoint=None):
    """Back up before mutation; receipt is the final write in the same transaction."""
    folder = safe(game, STATE + '/transactions/' + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    operations = []
    receipt_bytes = encoded(receipt)
    changes = list(changes) + [File(RECEIPT, sha(receipt_bytes), 'receipt', data=receipt_bytes)]
    for index, item in enumerate(changes):
        if not (owned_path(item.path) or loader_path(item.path) or item.path == RECEIPT):
            raise InstallError('Transaction escaped its permitted installation paths')
        destination = safe(game, item.path)
        before = file_hash(destination)
        after = item.sha256 or None
        if before == after:
            continue
        backup = f'backup-{index}' if before is not None else None
        if backup:
            shutil.copy2(destination, safe(folder, backup))
            if digest(safe(folder, backup)) != before:
                raise InstallError('File changed during backup: ' + item.path)
        staged = f'new-{index}' if after is not None else None
        if staged:
            if item.source is not None:
                shutil.copy2(item.source, safe(folder, staged))
            elif item.data is not None:
                safe(folder, staged).write_bytes(item.data)
            else:
                raise InstallError('Missing transaction payload')
            if digest(safe(folder, staged)) != after:
                raise InstallError('Release payload changed during staging: ' + item.path)
        operations.append({'path': item.path, 'before': before, 'after': after, 'backup': backup, 'staged': staged})
    journal = {'schema': 1, 'product': PRODUCT, 'game_root': str(game), 'status': 'prepared', 'operations': operations}
    atomic_state(folder, 'journal.json', encoded(journal))
    try:
        journal['status'] = 'applying'; atomic_state(folder, 'journal.json', encoded(journal))
        for index, row in enumerate(operations):
            destination = safe(game, row['path'])
            if file_hash(destination) != row['before']:
                raise InstallError('Installation target changed during setup: ' + row['path'])
            if row['after'] is None:
                destination.unlink()
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(safe(folder, row['staged']), safe(game, row['path']))
            if checkpoint:
                checkpoint(index, row['path'])
        if validate:
            validate()
        journal['status'] = 'committed'; atomic_state(folder, 'journal.json', encoded(journal))
    except BaseException as error:
        try:
            rollback(game, folder, journal)
        except Exception as recovery_error:
            raise InstallError(f'Setup failed ({error}); rollback requires recovery: {recovery_error}. Backups remain in {folder}') from error
        raise
    return folder.name


def install(package, game, *, checkpoint=None):
    game = game_identity(game)
    if safe(game, STATE + '/owner.json').is_file():
        # An interrupted write may precede its receipt; recover that known
        # transaction before interpreting its files as ownership collisions.
        manifest = check_package(package)
        game_identity(game, manifest.get('compatibility', {}))
        prototype_gate(game)
    else:
        plan_install(package, game)  # New installs are fully checked before state creation.
    with install_lock(game):
        recover_pending(game)
        plan = plan_install(package, game)
        # Lua writes its first setup status/probe before a Python helper runs.
        # Prepare that directory before enabling the adapter. It is private
        # runtime data, preserved on rollback/uninstall just like assets/cache.
        mailbox_directory(plan.game).mkdir(parents=True, exist_ok=True)
        mailbox_directory(plan.game)
        owned_files = [f for f in plan.files if f.kind != 'shared_loader']
        desired = {f.path for f in owned_files}
        obsolete = [File(row['path'], '', 'obsolete') for row in (plan.previous or {}).get('files', [])
                    if row['path'] not in desired and safe(plan.game, row['path']).exists()]
        # The mod entrypoint is enabled only after all payload/config files exist.
        changes = obsolete + sorted(plan.files, key=lambda f: f.kind == 'enable')
        receipt = {'schema': 1, 'product': PRODUCT, 'game_root': str(plan.game), 'version': plan.manifest['version'],
                   'status': 'installed', 'files': [{'path': f.path, 'sha256': f.sha256, 'kind': f.kind} for f in owned_files],
                   'shared_files': plan.shared, 'private_data_preserved': DATA}
        token = transaction(plan.game, changes, receipt,
                            validate=lambda: (check_game(plan.game, plan.manifest['compatibility']),
                                              external_prerequisites(plan.game,plan.manifest['install'])), checkpoint=checkpoint)
        return {**plan.summary(), 'transaction': token, 'status': 'installed'}


def uninstall(game, *, checkpoint=None):
    game = game_identity(game)
    if Path(sys.executable).resolve().is_relative_to(game / PACKAGE):
        raise InstallError('Run uninstall with the extracted release Python outside the installed DragonwildsSkate folder, so Windows can remove the owned runtime files')
    if not safe(game, RECEIPT).is_file():
        raise InstallError('No owned installation receipt; unowned files will not be removed')
    with install_lock(game):
        recover_pending(game)
        previous = load_receipt(game)
        changes = []; preserved = []; missing = []
        for row in previous['files']:
            current = file_hash(safe(game, row['path']))
            if current is None:
                missing.append(row['path'])
            elif current != row['sha256']:
                preserved.append(row['path'])
            else:
                changes.append(File(row['path'], '', 'remove'))
        # Disable this entrypoint first; keep shared loader/config and all private data.
        changes.sort(key=lambda f: f.path != MOD + '/enabled.txt')
        status = 'uninstalled_with_preserved_files' if preserved else 'uninstalled'
        receipt = {**previous, 'status': status, 'files': [], 'preserved_modified': preserved,
                   'removed_files': [f.path for f in changes], 'missing_files': missing}
        token = transaction(game, changes, receipt, checkpoint=checkpoint)
        return {'status': status, 'removed': len(changes), 'preserved_modified': preserved,
                'enabled_marker_preserved': MOD + '/enabled.txt' in preserved,
                'preserved_shared': previous.get('shared_files', []), 'private_data': str(game / DATA), 'transaction': token}


def recover(game):
    game = game_identity(game)
    if not safe(game, STATE + '/owner.json').exists():
        raise InstallError('No installer state to recover')
    with install_lock(game):
        return {'recovered': recover_pending(game)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'install', 'uninstall', 'recover'])
    parser.add_argument('--game-root', required=True)
    parser.add_argument('--package')
    args = parser.parse_args()
    try:
        if args.action in ('plan', 'install'):
            if not args.package:
                parser.error('--package is required for plan/install')
            result = plan_install(args.package, args.game_root).summary() if args.action == 'plan' else install(args.package, args.game_root)
        elif args.action == 'uninstall':
            result = uninstall(args.game_root)
        else:
            result = recover(args.game_root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (InstallError, OSError, ValueError) as error:
        print(str(error), file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
