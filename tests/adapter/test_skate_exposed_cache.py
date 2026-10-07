"""Derived cache identity, completion and corruption gates; synthetic geometry."""
from pathlib import Path
import copy
import hashlib
import json
import struct
import tempfile
import unittest
from unittest.mock import patch
import skate_exposed_cache as cache


def make_rail_cache(manifest_path):
    """Write a synthetic DWR1 beside a synthetic DWE; no game data or physics."""
    manifest_path = Path(manifest_path).resolve()
    value = json.loads(manifest_path.read_text(encoding='utf-8'))
    dwe_sha = cache.digest(manifest_path)
    count = int(value['segments'] > 0)
    stats = json.dumps({'provider_segments': count, 'stitched_joints': 0}, separators=(',', ':')).encode()
    payload = stats
    if count:
        payload += struct.pack('<Q6f', 2, 0, -0.0, 1, 1, 0, 1)
    raw = cache.DWR_HEADER.pack(b'DWR1', 1, bytes.fromhex(value['algorithm_sha256']),
                                bytes.fromhex(dwe_sha), count, count * 2, len(stats), hashlib.sha256(payload).digest()) + payload
    path = manifest_path.parent / 'native-rails' / (dwe_sha + '.dwr1')
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(raw)
    return {'schema': 1, 'ready': True, 'format': cache.DWR_FORMAT, 'path': str(path), 'bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest(), 'manifest_sha256': dwe_sha,
            'algorithm_sha256': value['algorithm_sha256'], 'rails': count, 'points': count * 2,
            'segments': count, 'cache_hit': False}


class ExposedCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix='edge-cache-test-')
        self.root = Path(self.temp.name).resolve()
        self.folder = self.root / 'exposed-edges-v1' / ('a' * 64) / ('b' * 64)
        (self.folder / 'payloads').mkdir(parents=True)
        self.file = self.folder / 'base.json'
        self.value = {'magic': 'DWE1', 'schema': 1, 'complete': True, 'base_fingerprint': 'b' * 64,
                      'scene_fingerprint': None, 'algorithm_sha256': 'a' * 64,
                      'parameters': {'cell_size_cm': 3200, 'halo_cm': 100, 'truck_distance_cm': 28, 'tolerance_cm': 1e-5},
                      'cells': [], 'segments': 1}
        self.payload(struct.pack('<4sIQ6d', b'DWC1', 1, 1, 0, 0, 100, 100, 0, 100))

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, raw):
        hashed = hashlib.sha256(raw).hexdigest()
        path = self.folder / 'payloads' / (hashed + '.edges')
        path.write_bytes(raw)
        self.value['cells'] = [{'key': [0, 0, 0], 'min': [0, 0, 0], 'max': [3200, 3200, 3200],
                               'dependencies': [{'min': [-100, -100, -100], 'max': [3300, 3300, 3300]}],
                               'path': 'payloads/' + path.name, 'bytes': len(raw), 'sha256': hashed, 'segments': 1}]
        self.save()
        return path

    def save(self):
        self.file.write_text(json.dumps(self.value), encoding='utf-8')
        self.rail = make_rail_cache(self.file)

    def declaration(self):
        return {'schema': 1, 'ready': True, 'manifest_file': str(self.file), 'manifest_sha256': cache.digest(self.file),
                'base_fingerprint': 'b' * 64, 'scene_fingerprint': None, 'algorithm_sha256': 'a' * 64,
                'cells': 1, 'segments': 1, 'rail_cache': copy.deepcopy(self.rail)}

    def check(self):
        return cache.prepared_record(self.root, self.declaration(), base='b' * 64)

    def test_full_preparation_and_lightweight_startup_use_same_pinned_identity(self):
        record = self.check()
        self.assertEqual(cache.check_record(self.root, record, base='b' * 64)['segments'], 1)
        payload = self.folder / self.value['cells'][0]['path']
        payload.write_bytes(b'changed')
        # Payload validation is the native loader's job at launch, not double I/O.
        self.assertEqual(cache.check_record(self.root, record, base='b' * 64)['segments'], 1)
        with self.assertRaisesRegex(ValueError, 'missing or changed'):
            cache.check_record(self.root, record, base='b' * 64, verify_payloads=True)

    def test_incomplete_wrong_scene_base_algorithm_and_duplicate_cells_rejected(self):
        original = copy.deepcopy(self.value)
        for change in [{'complete': False}, {'schema': True}, {'base_fingerprint': 'c' * 64},
                       {'scene_fingerprint': 'c' * 64}, {'algorithm_sha256': 'c' * 64},
                       {'cells': original['cells'] * 2}, {'parameters': {'halo_cm': float('nan')}}]:
            with self.subTest(change=change):
                self.value = {**copy.deepcopy(original), **change}
                self.save()
                with self.assertRaises(ValueError):
                    self.check()

    def test_payload_checksum_length_header_and_nonfinite_geometry_rejected(self):
        for raw in [b'DWC1', struct.pack('<4sIQ6d', b'BAD1', 1, 1, 0, 0, 0, 1, 0, 0),
                    struct.pack('<4sIQ6d', b'DWC1', 1, 2, 0, 0, 0, 1, 0, 0),
                    struct.pack('<4sIQ6d', b'DWC1', 1, 1, 0, 0, 0, float('nan'), 0, 0),
                    struct.pack('<4sIQ6d', b'DWC1', 1, 1, 1, 0, 0, 1, 0, 0)]:
            with self.subTest(raw=raw[:16]):
                self.payload(raw)
                with self.assertRaises(ValueError):
                    self.check()

    def test_paths_and_changed_manifest_never_accepted(self):
        record = self.check()
        self.file.write_text(self.file.read_text() + ' ')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            cache.check_record(self.root, record, base='b' * 64)
        self.value['cells'][0]['path'] = '../outside.edges'
        self.save()
        with self.assertRaisesRegex(ValueError, 'escapes'):
            self.check()
        outside = self.root / 'outside.json'
        outside.write_text(json.dumps(self.value))
        declaration = {**self.declaration(), 'manifest_file': str(outside)}
        with self.assertRaisesRegex(ValueError, 'escapes'):
            cache.prepared_record(self.root, declaration, base='b' * 64)

    def test_wrong_response_counts_or_not_ready_rejected(self):
        for change in [{'ready': False}, {'ready': 1}, {'cells': 2}, {'segments': True}, {'manifest_sha256': 'd' * 64}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                cache.prepared_record(self.root, {**self.declaration(), **change}, base='b' * 64)

    def test_native_rail_identity_is_required_normalized_and_transient_flag_removed(self):
        record = self.check()
        self.assertFalse(Path(record['rail_cache']['path']).is_absolute())
        self.assertNotIn('cache_hit', record['rail_cache'])
        expected = 'exposed-edges-v1/' + 'a' * 64 + '/' + 'b' * 64 + '/native-rails/' + record['sha256'] + '.dwr1'
        self.assertEqual(record['rail_cache']['path'], expected)
        declaration = self.declaration(); del declaration['rail_cache']
        with self.assertRaisesRegex(ValueError, 'Native rail cache is missing'):
            cache.prepared_record(self.root, declaration, base='b' * 64)
        changed = copy.deepcopy(record); del changed['rail_cache']
        with self.assertRaisesRegex(ValueError, 'Native rail cache is missing'):
            cache.check_record(self.root, changed, base='b' * 64)
        for change in [{'manifest_sha256': 'c' * 64}, {'algorithm_sha256': 'c' * 64}, {'schema': True},
                       {'ready': False}, {'segments': 2}, {'points': 100}, {'rails': True}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                declaration = self.declaration(); declaration['rail_cache'].update(change)
                cache.prepared_record(self.root, declaration, base='b' * 64)

    def test_native_rail_paths_missing_file_and_header_changes_are_caught_at_startup(self):
        record = self.check()
        path = Path(self.rail['path'])
        original = path.read_bytes()
        for change in [{'path': '../outside.dwr1'}, {'path': record['path']}, {'path': str(path)}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                changed = copy.deepcopy(record); changed['rail_cache'].update(change)
                cache.check_record(self.root, changed, base='b' * 64)
        declaration = self.declaration(); declaration['rail_cache']['path'] = str(self.root / 'elsewhere.dwr1')
        with self.assertRaisesRegex(ValueError, 'exact native cache location'):
            cache.prepared_record(self.root, declaration, base='b' * 64)
        for offset in (0, 4, 8, 40, 72, 80, 88):
            changed = bytearray(original); changed[offset] ^= 1; path.write_bytes(changed)
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, 'header'):
                cache.check_record(self.root, record, base='b' * 64)
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'missing or changed'):
            cache.check_record(self.root, record, base='b' * 64)

    def test_full_native_rail_payload_verification_and_bounded_startup(self):
        record = self.check()
        path = Path(self.rail['path'])
        original = path.read_bytes()
        changed = bytearray(original); changed[-1] ^= 1; path.write_bytes(changed)
        with patch.object(cache, 'digest', wraps=cache.digest) as digests:
            cache.check_record(self.root, record, base='b' * 64)
            self.assertNotIn(path, [Path(call.args[0]) for call in digests.call_args_list])
        with self.assertRaisesRegex(ValueError, 'checksum'):
            cache.check_record(self.root, record, base='b' * 64, verify_payloads=True)
        path.write_bytes(original)
        cache.check_record(self.root, record, base='b' * 64, verify_payloads=True)
        stats_len = cache.DWR_HEADER.unpack(original[:128])[6]
        start = 128 + stats_len + 8
        invalids = []
        nonfinite = bytearray(original); nonfinite[start:start + 4] = struct.pack('<f', float('nan')); invalids.append(nonfinite)
        zero = bytearray(original); zero[start + 12:start + 24] = original[start:start + 12]; invalids.append(zero)
        length = bytearray(original); length[start - 8:start] = struct.pack('<Q', 100); invalids.append(length)
        for raw in invalids:
            raw[96:128] = hashlib.sha256(raw[128:]).digest()
            path.write_bytes(raw)
            changed_record = copy.deepcopy(record)
            changed_record['rail_cache']['sha256'] = hashlib.sha256(raw).hexdigest()
            with self.assertRaisesRegex(ValueError, 'Invalid native rail'):
                cache.check_record(self.root, changed_record, base='b' * 64, verify_payloads=True)


if __name__ == '__main__':
    unittest.main()
