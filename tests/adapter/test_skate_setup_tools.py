"""Mocked downloads only: no external files are fetched, extracted, or executed."""
from pathlib import Path
from unittest.mock import Mock, patch
import hashlib
import io
import tempfile
import unittest
import urllib.error
import urllib.request
import warnings
import zipfile
import skate_setup_tools as tools


def archive_bytes(member='folder/converter.exe', *, duplicate=False, symlink=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        entry = zipfile.ZipInfo(member)
        entry.external_attr = (0o120777 if symlink else 0o100644) << 16
        archive.writestr(entry, b'inert test program; never execute')
        if duplicate:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                archive.writestr(member, b'duplicate')
    return stream.getvalue()


class Response(io.BytesIO):
    def __init__(self, data, *, url='https://release-assets.githubusercontent.com/test', length=None, fault=False):
        super().__init__(data)
        self.url, self.fault = url, fault
        self.headers = {} if length is None else {'Content-Length': str(length)}
    def geturl(self): return self.url
    def read(self, size=-1):
        if self.fault: raise urllib.error.URLError('mocked connection loss')
        return super().read(size)


class ToolDownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent, prefix='tool-cache-test-')
        self.addCleanup(temporary.cleanup)
        self.game = Path(temporary.name).resolve()
        self.raw = archive_bytes()
        self.spec = {'sha256': hashlib.sha256(self.raw).hexdigest(), 'member': 'folder/converter.exe',
            'url': 'https://github.com/test/repo/releases/tag/v1',
            'download': 'https://github.com/test/repo/releases/download/v1/tool.zip', 'bytes': len(self.raw)}
        self.patch = patch.dict(tools.TOOLS, {'converter': self.spec})
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.target = self.game / 'DragonwildsSkateData/setup-tools' / ('converter-' + self.spec['sha256'] + '.zip')

    def fetch(self, response, messages=None):
        with patch.object(tools, '_open', return_value=response) as opened:
            path = tools.get_tool_archive(self.game, 'converter', progress=messages.append if messages is not None else None)
        return path, opened

    def assert_no_partial(self):
        self.assertEqual(list((self.game / 'DragonwildsSkateData/setup-tools').glob('*.part')), [])

    def test_verified_download_is_atomic_cached_and_contains_no_upload(self):
        messages = []
        path, opened = self.fetch(Response(self.raw, length=len(self.raw)), messages)
        self.assertEqual(path, self.target)
        self.assertEqual(path.read_bytes(), self.raw)
        request = opened.call_args.args[0]
        self.assertIsNone(request.data)
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.full_url, self.spec['download'])
        self.assertNotIn(str(self.game), str(request.headers))
        self.assertTrue(any('Verifying' in text for text in messages))
        with patch.object(tools, '_open', side_effect=AssertionError('Cache reuse must not use the network')):
            self.assertEqual(tools.get_tool_archive(self.game, 'converter'), path)
        self.assert_no_partial()
        self.assertFalse(list(self.game.rglob('*.exe')))  # ZIP stays a ZIP.

    def test_bad_hash_never_replaces_old_cache_and_good_retry_repairs_it(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'previous corrupted cache')
        changed = bytearray(self.raw); changed[35] ^= 1
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.fetch(Response(bytes(changed)))
        self.assertEqual(self.target.read_bytes(), b'previous corrupted cache')
        self.assert_no_partial()
        self.fetch(Response(self.raw))
        self.assertEqual(self.target.read_bytes(), self.raw)

    def test_truncated_oversized_and_wrong_header_downloads_are_not_published(self):
        for response in (Response(self.raw[:-1]), Response(self.raw + b'x'), Response(self.raw, length=1)):
            with self.subTest(response=response), self.assertRaises(ValueError): self.fetch(response)
            self.assertFalse(self.target.exists())
            self.assert_no_partial()

    def test_only_official_https_redirects_are_followed(self):
        request = urllib.request.Request(self.spec['download'])
        handler = tools._OfficialRedirect()
        for url in ('http://github.com/test.zip', 'https://example.com/tool.zip',
                    'https://github.com.evil.example/tool.zip', 'https://user@github.com/tool.zip'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, 'Found', {}, url)
        allowed = 'https://release-assets.githubusercontent.com/test?signature=example'
        self.assertEqual(handler.redirect_request(request, None, 302, 'Found', {}, allowed).full_url, allowed)
        with self.assertRaisesRegex(ValueError, 'official HTTPS'):
            self.fetch(Response(self.raw, url='https://example.com/tool.zip'))
        self.assertFalse(self.target.exists()); self.assert_no_partial()

    def test_required_zip_member_must_be_unique_and_regular(self):
        for raw in (archive_bytes('../elsewhere.exe'), archive_bytes(duplicate=True), archive_bytes(symlink=True)):
            spec = {**self.spec, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            with patch.dict(tools.TOOLS, {'converter': spec}), self.assertRaises(ValueError): self.fetch(Response(raw))
            self.assertFalse(list(self.game.rglob('*.zip')))
            self.assert_no_partial()

    def test_connection_failure_explains_retry_and_unknown_tool_does_nothing(self):
        with self.assertRaisesRegex(ValueError, 'Check your connection'):
            self.fetch(Response(self.raw, fault=True))
        self.assertFalse(self.target.exists()); self.assert_no_partial()
        with patch.object(tools, '_open', side_effect=AssertionError('Unknown tool must not use network')):
            with self.assertRaisesRegex(ValueError, 'Unknown'):
                tools.get_tool_archive(self.game, 'not-a-tool')


if __name__ == '__main__':
    unittest.main()
