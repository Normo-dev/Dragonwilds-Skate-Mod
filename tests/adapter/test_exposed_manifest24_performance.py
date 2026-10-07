"""Per-call repeated-payload validation gates on synthetic geometry."""
from pathlib import Path
import copy
import importlib.util
import os
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import skate_exposed_cache as cache
import test_skate_exposed_cache as original

if os.environ.get('SKATE_EXPOSED_VALIDATOR_CANDIDATE'):
    path = Path(os.environ['SKATE_EXPOSED_VALIDATOR_CANDIDATE'])
    spec = importlib.util.spec_from_file_location('exposed_manifest24_candidate', path)
    cache = importlib.util.module_from_spec(spec); spec.loader.exec_module(cache)
    original.cache = cache


class PayloadMemoTests(unittest.TestCase):
    setUp = original.ExposedCacheTests.setUp
    tearDown = original.ExposedCacheTests.tearDown
    payload = original.ExposedCacheTests.payload
    save = original.ExposedCacheTests.save

    def repeated(self, count=100):
        cell = self.value['cells'][0]
        self.value['cells'] = []
        for x in range(count):
            row = copy.deepcopy(cell)
            row.update(key=[x, 0, 0], min=[x*3200, 0, 0], max=[(x+1)*3200, 3200, 3200],
                       dependencies=[{'min': [x*3200-100, -100, -100], 'max': [(x+1)*3200+100, 3300, 3300]}])
            self.value['cells'].append(row)
        self.value['segments'] = count
        self.save()
        return self.folder / cell['path']

    def validate(self, full=False):
        return cache.manifest(self.root, self.file, base='b'*64, scene=None, verify_payloads=full)

    def test_one_leaf_check_hash_and_header_per_unique_payload(self):
        payload = self.repeated()
        resolves, leaves, opens, hashes = [], [], [], []
        resolve, lstat, opening, digest = Path.resolve, Path.lstat, Path.open, cache.digest
        def resolving(path, *args, **kwargs): resolves.append(path); return resolve(path, *args, **kwargs)
        def leaf(path, *args, **kwargs): leaves.append(path); return lstat(path, *args, **kwargs)
        def opened(path, *args, **kwargs): opens.append(path); return opening(path, *args, **kwargs)
        def hashed(path): hashes.append(Path(path)); return digest(path)
        with patch.object(Path, 'resolve', resolving), patch.object(Path, 'lstat', leaf), \
             patch.object(Path, 'open', opened), patch.object(cache, 'digest', hashed):
            self.assertEqual(self.validate(True)['segments'], 100)
        self.assertEqual(leaves.count(payload), 1)
        self.assertEqual(hashes.count(payload), 1)
        self.assertEqual(opens.count(payload), 2)  # Body hash and header/geometry.
        self.assertLessEqual(len(resolves), 4)

    def test_same_filename_conflicting_metadata_rejected_without_body_reads(self):
        self.repeated(2)
        self.value['cells'][1].update(segments=2, bytes=16+2*48)
        self.value['segments'] = 3; self.save()
        with self.assertRaisesRegex(ValueError, 'Conflicting metadata'): self.validate(False)

    def test_every_shared_cell_dependency_and_identity_still_checked(self):
        self.repeated(3)
        pristine = copy.deepcopy(self.value)
        for change in ('bounds', 'halo', 'key', 'path'):
            self.value = copy.deepcopy(pristine)
            last = self.value['cells'][-1]
            if change == 'bounds': last['max'][0] += 1
            if change == 'halo': last['dependencies'][0]['min'][1] = 0
            if change == 'key': last['key'] = self.value['cells'][0]['key']
            if change == 'path': last['path'] = '../outside.edges'
            self.save()
            with self.assertRaises(ValueError): self.validate(False)

    def test_no_trust_survives_another_manifest_call(self):
        payload = self.repeated(2)
        self.validate(True)
        payload.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'missing or changed'): self.validate(True)

    def test_linked_directory_and_leaf_rejected_even_without_body_verification(self):
        payload = self.repeated(2)
        directory = self.folder / 'payloads'
        resolve, lstat = Path.resolve, Path.lstat
        def linked_directory(path, *args, **kwargs):
            return self.root / 'elsewhere' if path == directory else resolve(path, *args, **kwargs)
        with patch.object(Path, 'resolve', linked_directory), self.assertRaisesRegex(ValueError, 'linked'):
            self.validate(False)
        for info in (SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0),
                     SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)):
            def linked_leaf(path, *args, **kwargs):
                return info if path == payload else lstat(path, *args, **kwargs)
            with patch.object(Path, 'lstat', linked_leaf), self.assertRaisesRegex(ValueError, 'linked'):
                self.validate(False)


if __name__ == '__main__': unittest.main()
