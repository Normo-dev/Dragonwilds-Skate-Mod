"""GUI request adapter. Uses the receipt installer and existing private setup APIs.

Release location: app/skate_wizard.py. One JSON request on stdin; JSON result on
stdout. This module never accepts shell commands, kills processes, or deletes
conversion directories. Pinned external tools are downloaded on request or
provided separately by the user.
"""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skate_setup_tools import TOOLS, get_tool_archive
from skate_setup_progress import action_progress, map_progress
_emit_lock = threading.Lock()
ACTIONS = {'check', 'install', 'import-loader', 'import-assets', 'convert',
           'prepare-map', 'prepare-buildings', 'refresh-probe', 'archive-map',
           'verify', 'play', 'capture', 'stop', 'recover', 'uninstall'}

NEXT = {
    'install': 'Go to step 2 to import or convert your own Skate 3 files. Existing private data is retained.',
    'recover': 'Recovery finished. Retry Install / update mod from step 1.',
    'uninstall': 'Uninstall finished. Game saves, shared UE4SS files and private assets/caches are preserved.',
    'import-assets': 'Go to step 3: launch the game for settings capture, then close it before preparation.',
    'convert': 'Your files were converted and imported. Go to step 3 to capture settings in a solo world.',
    'prepare-map': 'Map preparation finished. Choose Prepare building catalogue next.',
    'prepare-buildings': 'Building metadata is prepared. Choose Verify readiness, then Play Dragonwilds.',
    'refresh-probe': 'Launch the game to capture settings in a solo world again, then close it before preparation.',
    'archive-map': 'The previous map is preserved in its backup folder. Prepare / resume map will create a new generation.',
}


def completed(action, details):
    return {'status': 'Completed: ' + action.replace('-', ' '), 'next': NEXT.get(action, ''), 'details': details}


def emit(message):
    with _emit_lock:
        print(json.dumps(message, ensure_ascii=True), flush=True)


def request_value(value):
    if not isinstance(value, dict) or set(value) - {
        'action', 'game', 'source', 'converter_zip', 'extractor_zip', 'loader_zip', 'confirm_tools'}:
        raise ValueError('Invalid setup request')
    if value.get('action') not in ACTIONS:
        raise ValueError('Unknown setup action')
    for key in ('game', 'source', 'converter_zip', 'extractor_zip', 'loader_zip'):
        if key in value and (not isinstance(value[key], str) or '\0' in value[key] or
                             any(ord(c) < 32 for c in value[key])):
            raise ValueError('Invalid path: ' + key)
    if not value.get('game'):
        raise ValueError('Choose your Dragonwilds game folder first')
    if 'confirm_tools' in value and type(value['confirm_tools']) is not bool:
        raise ValueError('Invalid external-tool confirmation')
    return value


def read_member(archive, member, *, size=None, sha256=None):
    """Read exactly one regular member; never extract archive paths."""
    with zipfile.ZipFile(archive) as z:
        matches = [i for i in z.infolist() if i.filename == member]
        if len(matches) != 1:
            raise ValueError('Official ZIP must contain exactly one ' + member)
        info = matches[0]
        if info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError('Archive member is not a regular file')
        if info.file_size <= 0 or info.file_size > 256 * 1024 * 1024 or (size is not None and info.file_size != size):
            raise ValueError('Archive member size does not match this release')
        data = z.read(info)
    if sha256 is not None and hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError('Archive member hash does not match this release: ' + member)
    return data


def tool_bytes(archive, name):
    from skate_preflight import digest
    archive = Path(archive).resolve()
    spec = TOOLS[name]
    if not archive.is_file() or digest(archive) != spec['sha256']:
        raise ValueError('Choose the exact official ' + name + ' ZIP linked in setup; its SHA-256 does not match')
    return read_member(archive, spec['member'])


def launch_command(arguments, *, log=None):
    """Argument vector only. Child output is kept outside the package manifest."""
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    if log is None:
        result = subprocess.run(list(map(str, arguments)), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=flags, check=False)
        text = result.stdout.decode('utf-8', errors='replace')
        if result.returncode:
            raise ValueError(text[-16000:] or 'Setup operation failed')
        return text
    with Path(log).open('xb') as output:
        result = subprocess.run(list(map(str, arguments)), stdin=subprocess.DEVNULL,
                                stdout=output, stderr=subprocess.STDOUT,
                                cwd=str(Path(log).resolve().parent),
                                creationflags=flags, check=False)
    if result.returncode:
        raise ValueError('External tool did not finish. Original files and this attempt were preserved. Log: ' + str(log))
    return str(log)


def install_loader(package, game, archive, manifest):
    import skate_install as install
    rows = manifest['install'].get('external_prerequisites', [])
    expected = {install.WIN64 + '/' + r['destination'] for r in rows}
    if expected != install.LOADER_BINARIES | {install.UEHELPERS} or len(rows) != 3:
        raise ValueError('This release does not declare the three external UE4SS prerequisites')
    changes = []
    for row in rows:
        name = install.WIN64 + '/' + install.relative(row['destination'])
        data = read_member(archive, row['destination'], size=row['bytes'], sha256=row['sha256'])
        existing = install.file_hash(install.safe(game, name))
        if existing not in (None, row['sha256']):
            raise ValueError('A different shared loader already exists; setup will not replace it: ' + name)
        changes.append(install.File(name, row['sha256'], 'shared_loader', data=data))
    install.prototype_gate(game)
    with install.install_lock(game):
        install.recover_pending(game)
        # Recovery can restore an older loader; recheck while holding the lock.
        for item in changes:
            if install.file_hash(install.safe(game, item.path)) not in (None, item.sha256):
                raise ValueError('Shared loader changed while setup was checking it')
        previous = install.load_receipt(game)
        receipt = previous or {'schema': 1, 'product': install.PRODUCT, 'game_root': str(game),
                              'version': manifest['version'], 'status': 'prerequisites_only', 'files': [], 'shared_files': []}
        token = install.transaction(game, changes, receipt,
            validate=lambda: install.external_prerequisites(game, manifest['install']))
    return {'status': 'UE4SS prerequisites verified', 'transaction': token,
            'next': 'Choose Install / update mod. Shared loader files are preserved on uninstall.'}


def installed(package, game):
    import skate_install as install
    from runtime_paths import load_paths
    from skate_preflight import check_package
    root = install.safe(game, install.PACKAGE)
    manifest = check_package(root)
    receipt = install.load_receipt(game)
    if not receipt or receipt.get('status') != 'installed':
        raise ValueError('Install the mod into the selected game folder first')
    paths = load_paths(root / 'app')
    if paths.get('game_root') != game or paths['root'] != root / 'app':
        raise ValueError('Installed settings belong to another location; run Install / update mod')
    for key, name in [('assets', 'assets'), ('map_data', 'map-data'), ('mailbox', 'mailbox')]:
        if paths[key] != install.safe(game, install.DATA + '/' + name):
            raise ValueError('Private data path changed; run the installer')
    return root, paths, manifest


def setup_command(root, operation, *args):
    # Both executables/scripts are already exact manifest-verified payloads.
    return [root / 'python/python.exe', root / 'app/skate_setup.py', operation, *args]


def private_conversion(game, source, converter_zip, extractor_zip, run=launch_command):
    import skate_install as install
    from skate_preflight import digest
    source = Path(source).resolve()
    iso = source.suffix.lower() == '.iso'
    if source.is_dir():
        source = source / 'default.xex'
    if not source.is_file() or (not iso and source.name.casefold() != 'default.xex'):
        raise ValueError('Choose your own Xbox 360 Skate 3 ISO or extracted default.xex')
    if not iso and not (source.parent / 'data').is_dir():
        raise ValueError('default.xex must have its original data folder beside it')
    progress = lambda message: emit({'event': 'progress', 'message': message})
    with action_progress(emit, 'preparing-tools', 'Downloading and checking preparation tools'):
        converter_zip = converter_zip or get_tool_archive(game, 'converter', progress=progress)
        if iso:
            extractor_zip = extractor_zip or get_tool_archive(game, 'extractor', progress=progress)
        converter = tool_bytes(converter_zip, 'converter')
        extractor = tool_bytes(extractor_zip, 'extractor') if iso else None
    root = install.safe(game, install.DATA + '/conversion')
    marker = install.safe(root, 'owner.json')
    owner = {'schema': 1, 'product': 'DragonwildsSkateConversion'}
    if marker.exists():
        if json.loads(marker.read_text(encoding='utf-8')) != owner:
            raise ValueError('Conversion workspace has a different owner')
    else:
        if root.exists() and any(root.iterdir()):
            raise ValueError('Conversion workspace is not empty and has no ownership marker')
        root.mkdir(parents=True, exist_ok=True)
        with marker.open('x', encoding='utf-8') as f: json.dump(owner, f)
    job = install.safe(root, uuid.uuid4().hex)
    job.mkdir()  # A collision never reuses an upstream deletion destination.
    (job / 'job.json').write_text(json.dumps({'schema': 1, 'source': str(source),
        'source_bytes': source.stat().st_size, 'converter_archive_sha256': TOOLS['converter']['sha256'],
        'extractor_archive_sha256': TOOLS['extractor']['sha256'] if iso else None}), encoding='utf-8')
    converter_file = job / 'converter.exe'; converter_file.write_bytes(converter)
    if iso:
        extractor_file = job / 'xdvdfs.exe'; extractor_file.write_bytes(extractor)
        extracted = install.safe(job, 'extracted')
        if extracted.exists(): raise ValueError('Extraction destination unexpectedly exists')
        emit({'event': 'progress', 'message': 'Extracting your ISO into a new private folder. This can take several minutes.'})
        # The Rust extractor supports Windows Unicode and long paths; the older
        # ANSI extractor failed inside ordinary deeply nested game libraries.
        with action_progress(emit, 'extracting-iso', 'Extracting your ISO'):
            run([extractor_file, 'unpack', source, extracted], log=job / 'extract.log')
        source = install.safe(job, 'extracted/default.xex')
        if not source.is_file() or not install.safe(job, 'extracted/data').is_dir():
            raise ValueError('The ISO did not produce default.xex and data. Attempt preserved: ' + str(job))
    destination = install.safe(job, 'converted')
    partial = install.safe(job, 'converted.partial')
    if destination.exists() or partial.exists():
        raise ValueError('Conversion destinations must be new and empty')
    # Recheck containment immediately before the upstream tool, whose contract
    # can remove precisely destination and destination.partial.
    if destination.parent != job.resolve() or partial.parent != job.resolve():
        raise ValueError('Conversion destination escaped its private job')
    emit({'event': 'progress', 'message': 'Converting your Skate 3 files. Original files are preserved. Log: ' + str(job / 'convert.log')})
    with action_progress(emit, 'converting-assets', 'Converting your game files'):
        run([converter_file, '--xex', source, '--out', destination], log=job / 'convert.log')
    assets = install.safe(job, 'converted/assets')
    if not assets.is_dir(): raise ValueError('Converter did not produce assets; attempt preserved: ' + str(job))
    (job / 'completed.json').write_text(json.dumps({'assets': str(assets)}), encoding='utf-8')
    return assets


def run_request(value, package=None):
    value = request_value(value)
    import skate_install as install
    from skate_preflight import check_package, check_installation
    from skate_process import offline_maintenance
    package = Path(package or Path(__file__).resolve().parent.parent).resolve()
    manifest = check_package(package)
    action = value['action']
    # A Steam update must not strand owned files or a running helper. These
    # preserve-only actions retain structural/path/receipt checks, while all
    # game-reading or gameplay actions keep the exact supported build gate.
    repair = action in {'recover', 'uninstall', 'stop', 'archive-map'}
    game = install.game_identity(value['game'], None if repair else manifest['compatibility'])
    # Paths are derived from the selected game, never taken from arbitrary GUI JSON.
    offline_paths = {'game_root': game, **{k:install.safe(game, install.PACKAGE + '/' + v)
        for k, v in manifest['install']['runtime'].items()}}
    if action == 'check':
        try:
            plan = install.plan_install(package, game).summary()
            return {'status': 'Ready to install or update', 'details': plan,
                    'next': 'Install / update mod, then import or convert your Skate 3 files.'}
        except install.InstallError as error:
            return {'status': 'Action needed', 'details': str(error),
                    'next': 'If UE4SS is missing, select its exact official ZIP and use Import UE4SS.'}
    if action in {'install', 'import-loader', 'recover', 'uninstall'}:
        with offline_maintenance(offline_paths):
            if action == 'install': return completed(action, install.install(package, game))
            if action == 'import-loader': return install_loader(package, game, value.get('loader_zip', ''), manifest)
            if action == 'recover': return completed(action, install.recover(game))
            result = completed(action, install.uninstall(game))
            if result['details'].get('enabled_marker_preserved'):
                result['status'] = 'Owned files removed; a modified enable marker was preserved'
                result['next'] = 'Review technical details before launching the game: the altered mod enable marker still exists.'
            return result
    root, paths, own = installed(package, game)
    if action in {'play', 'capture'}:
        if action == 'play': check_installation(root / 'app')
        os.startfile('steam://rungameid/1374490')
        return {'status': 'Launch requested', 'next': ('Enter a solo world, wait for the setup capture message, then close the game and choose Prepare / resume map.'
                 if action == 'capture' else 'Enter your own solo save and equip Skateboard in Mounts.')}
    if action == 'verify':
        return {'status': 'Ready to play', 'details': check_installation(root / 'app')}
    if action == 'stop':
        return {'status': 'Stop requested', 'details': launch_command([root / 'python/python.exe', root / 'app/skate_launcher.py', 'stop'])}
    if action == 'archive-map':
        # The verified preserve-only API owns its mutex and ownership checks.
        # Its generic CLI also checks current game compatibility for exporters,
        # which would incorrectly prevent archiving after a game update.
        from skate_setup import archive_map
        return completed(action, archive_map(paths))
    if action == 'convert' and value.get('confirm_tools') is not True:
        raise ValueError('Confirm that you want to run the separately obtained official tools on your own game files')
    # Keep offline preparation and import away from live game/helper mutation.
    # prepare-buildings holds this same mutex in its child API.
    nested = action == 'prepare-buildings'
    with contextlib.nullcontext() if nested else offline_maintenance(paths):
        if action == 'convert':
            assets = private_conversion(game, value.get('source', ''), value.get('converter_zip', ''), value.get('extractor_zip', ''))
            with action_progress(emit, 'importing-assets', 'Importing and verifying prepared game files'):
                result = launch_command(setup_command(root, 'import-assets', assets))
        elif action == 'import-assets':
            source = Path(value.get('source', '')).resolve()
            if not source.is_dir(): raise ValueError('Choose the converted assets folder')
            with action_progress(emit, 'importing-assets', 'Importing and verifying prepared game files'):
                result = launch_command(setup_command(root, action, source))
        elif action == 'prepare-map':
            args = ['--resume'] if (paths['map_data'] / 'setup-state.json').exists() else []
            emit({'event': 'progress', 'message': 'Checking saved map data and preparing missing grind edges. Existing verified work is reused.'})
            with map_progress(paths['map_data'], emit):
                result = launch_command(setup_command(root, action, *args))
        else:
            label = 'Exporting and verifying building catalogue' if action == 'prepare-buildings' else 'Updating setup settings'
            with action_progress(emit, action, label):
                result = launch_command(setup_command(root, action))
    return completed(action, result)


def main():
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536: raise ValueError('Setup request is too large')
        result = run_request(json.loads(raw.decode('utf-8-sig')))
        emit({'ok': True, 'result': result}); return 0
    except Exception as error:
        emit({'ok': False, 'error': str(error), 'next': 'Your original files and save data are preserved. Correct the reported issue and retry this step.'})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
