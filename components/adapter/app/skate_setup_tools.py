"""Download only the pinned official conversion tools, when conversion requests it.

Importing this module performs no network or filesystem work. The caller passes
the selected game folder, never ISO contents. Archives are verified before they
are returned; this module never extracts or executes a program.
"""
from pathlib import Path
import hashlib
import http.client
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile


TOOLS = {
    'converter': {
        'sha256': 'f7c02cfb4dd5650be29762947a0b17d3e6f9b9f1259025ee89a99bd031e1ebca',
        'member': '2010-Rust-Rewrite-Mashup/skate/iw4l-skate-convert.exe',
        'url': 'https://github.com/chasmlol/2010-rust-rewrite-mashup/releases/tag/v0.4.0',
        'download': 'https://github.com/chasmlol/2010-rust-rewrite-mashup/releases/download/v0.4.0/2010-Rust-Rewrite-Mashup-windows-x64.zip',
        'bytes': 70231384,
    },
    'extractor': {
        'sha256': '008b1b3a63f1ecb00d1ed51ab154053fd0f7c41c68e973455bbe77ab989a7cc5',
        'member': 'xdvdfs.exe',
        'url': 'https://github.com/antangelo/xdvdfs/releases/tag/v0.8.3',
        'download': 'https://github.com/antangelo/xdvdfs/releases/download/v0.8.3/xdvdfs-windows-1cc850bf1b3487fad7ec7c9eed01d83e8fc75ba4.zip',
        'bytes': 1582072,
    },
}
_HOSTS = {'github.com', 'release-assets.githubusercontent.com',
          'objects.githubusercontent.com', 'github-releases.githubusercontent.com'}
_LABELS = {'converter': 'game-file converter', 'extractor': 'ISO extractor'}


def _official_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in _HOSTS or
            parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('The tool download redirected outside the official HTTPS release hosts')


class _OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(request):
    return urllib.request.build_opener(_OfficialRedirect()).open(request, timeout=30)


def _safe_cache_path(game, filename=None):
    game = Path(game).resolve()
    if not game.is_dir():
        raise ValueError('Choose the existing Dragonwilds game folder before preparing your ISO')
    path = game / 'DragonwildsSkateData' / 'setup-tools'
    if filename is not None:
        path = path / filename
    if path.resolve() != path or path.is_symlink():
        raise ValueError('The setup-tool cache contains a redirected path')
    return path


def _validate(path, spec):
    if not path.is_file() or path.stat().st_size != spec['bytes']:
        raise ValueError('The downloaded tool has the wrong size; retry Prepare my ISO')
    with path.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    if checksum != spec['sha256']:
        raise ValueError('The tool download failed its checksum check; retry Prepare my ISO')
    with zipfile.ZipFile(path) as archive:
        matches = [entry for entry in archive.infolist() if entry.filename == spec['member']]
        if len(matches) != 1:
            raise ValueError('The official tool archive does not contain its required program exactly once')
        entry = matches[0]
        kind = (entry.external_attr >> 16) & 0o170000
        if entry.is_dir() or kind not in (0, 0o100000) or not 0 < entry.file_size <= 256 * 1024 * 1024:
            raise ValueError('The official tool archive does not contain a regular program file')


def get_tool_archive(game_root, name, *, progress=None):
    """Return a verified cached ZIP; call only for an explicit conversion action.

    ``progress(message)`` receives short display text. A corrupt cache is replaced
    only after a complete verified download; failed downloads never replace it.
    The selected ISO is not a parameter and is never sent over the network.
    """
    if name not in TOOLS:
        raise ValueError('Unknown conversion tool')
    spec, label = TOOLS[name], _LABELS[name]
    notify = progress if progress is not None else lambda message: None
    filename = name + '-' + spec['sha256'] + '.zip'
    target = _safe_cache_path(game_root, filename)
    if target.exists():
        try:
            _validate(target, spec)
        except (OSError, ValueError, zipfile.BadZipFile):
            pass
        else:
            notify('Using the verified ' + label + ' already downloaded.')
            return target
    cache = _safe_cache_path(game_root)
    cache.mkdir(parents=True, exist_ok=True)
    temporary_name = '.' + filename + '.' + uuid.uuid4().hex + '.part'
    temporary = _safe_cache_path(game_root, temporary_name)
    _official_url(spec['download'])
    request = urllib.request.Request(spec['download'], headers={
        'User-Agent': 'DragonwildsSkate-Setup/1', 'Accept': 'application/octet-stream',
        'Accept-Encoding': 'identity'})
    size = (f"{spec['bytes'] / 1_000_000:.1f} MB" if spec['bytes'] >= 1_000_000
            else f"{spec['bytes'] / 1_000:.0f} KB")
    notify('Downloading the official ' + label + ' (' + size + ').')
    try:
        try:
            with _open(request) as response, temporary.open('xb') as output:
                _official_url(response.geturl())
                declared = response.headers.get('Content-Length')
                if declared is not None and (not declared.isdigit() or int(declared) != spec['bytes']):
                    raise ValueError('The official download size changed; use the pinned ZIP in Advanced files or retry later')
                received, announced, started = 0, 0., time.monotonic()
                while True:
                    if time.monotonic() - started > 30 * 60:
                        raise ValueError('The tool download took too long; check your connection and retry Prepare my ISO')
                    block = response.read(256 * 1024)
                    if not block:
                        break
                    received += len(block)
                    if received > spec['bytes']:
                        raise ValueError('The tool download is larger than its pinned official release')
                    output.write(block)
                    now = time.monotonic()
                    if now - announced >= 1.:
                        notify('Downloading the ' + label + ': ' + str(received * 100 // spec['bytes']) + '%.')
                        announced = now
                output.flush(); os.fsync(output.fileno())
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as error:
            raise ValueError('Could not download the ' + label + '. Check your connection and retry Prepare my ISO; '
                             'you can also select the pinned official ZIP in Advanced files.') from error
        notify('Verifying the official ' + label + ' download.')
        _validate(temporary, spec)
        # Recheck containment immediately before publishing into the private
        # cache. Unique temporary names permit independent interrupted retries.
        target = _safe_cache_path(game_root, filename)
        if _safe_cache_path(game_root, temporary_name) != temporary:
            raise ValueError('The setup-tool cache moved during download')
        os.replace(temporary, target)
        _validate(target, spec)
        notify('The verified ' + label + ' is ready.')
        return target
    finally:
        if temporary.exists() and temporary.resolve() == temporary:
            temporary.unlink()
