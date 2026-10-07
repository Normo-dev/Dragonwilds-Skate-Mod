"""Incremental graph cache/checkpoint/retention fixtures without game inputs."""
from pathlib import Path
import copy
import hashlib
import io
import json
import struct
import unittest
from unittest.mock import patch

import skate_exposed_cache as cache
import skate_cache_retention as retention
import skate_checkpoint_worker as checkpoint_worker
from test_skate_cache_retention import RetentionTests


def make_graph(manifest, *, parent=None, nonce=None, graph_pin='d' * 64):
    manifest = Path(manifest).resolve()
    value = json.loads(manifest.read_text())
    snapshot = cache.digest(manifest)
    root = manifest.parents[3]
    folder = root / 'incremental-grind-v1' / graph_pin
    folder.mkdir(parents=True, exist_ok=True)
    name = snapshot + (('-' + nonce) if nonce else '') + '.dgi1'
    path = folder / name
    if parent is None:
        # A minimal native-valid graph. DWE synthetic tests allow duplicate raw
        # edges; this single canonical edge retains a one-metre spline.
        key = struct.pack('<6I', 0, 0x3f800000, 0, 0x3f800000, 0x3f800000, 0)
        body = struct.pack('<11Qd', value['segments'], 1, 1, 1, 1, 2, 1, 0, 0, 0, 1, 0.)
        identity = value['cells'][0]['sha256'].encode()
        body += struct.pack('<3iQ', 0, 0, 0, len(identity)) + identity
        body += struct.pack('<QQI', value['segments'], 1, 0)
        body += b'\1' + key + struct.pack('<IIB', 1, 0, 36)
        body += struct.pack('<IdQQIQ', 0, 1., 0, 1, 0, 1) + key
        body += key + struct.pack('<QQ6f', 1, 2, 0, 1, -0., 1, 1, -0.)
        magic = b'DGI1'
    else:
        parent = Path(parent)
        parent_header = cache.GRAPH_HEADER.unpack(parent.read_bytes()[:128])
        ancestor = parent.name.encode()
        # Zero changed cells proves a differently pinned scene without
        # inventing geometry or output; native decoding remains authoritative.
        body = parent_header[3] + struct.pack('<Q', len(ancestor)) + ancestor + b'\0' + struct.pack('<Q', 0)
        magic = b'DGD1'
    raw = cache.GRAPH_HEADER.pack(magic, 1, bytes.fromhex(graph_pin), bytes.fromhex(snapshot),
                                  hashlib.sha256(body).digest(), len(body), b'\0' * 16) + body
    path.write_bytes(raw)
    return {'schema': 1, 'ready': True, 'format': cache.GRAPH_FORMAT, 'path': str(path), 'bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest(), 'manifest_sha256': snapshot,
            'algorithm_sha256': graph_pin, 'extraction_algorithm_sha256': value['algorithm_sha256'],
            'rails': 1, 'points': 2, 'segments': 1, 'cache_hit': False}


class GrindCacheTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RetentionTests(); self.fixture.setUp()
        self.root = self.fixture.root

    def tearDown(self):
        self.fixture.tearDown()

    def row(self, number, *, parent=None, publish=True, nonce=None):
        row = self.fixture.accept(number, publish=False)
        declaration = row['response']['world']['scene_checkpoint']
        graph = make_graph(row['marker'], parent=parent, nonce=nonce)
        canonical = Path(graph['path']).parent / (graph['manifest_sha256'] + '.dgi1')
        if nonce and not canonical.exists():
            canonical.write_bytes(Path(graph['path']).read_bytes())
        declaration['graph_cache'] = graph
        declaration['rail_cache'] = graph
        row['graph'] = Path(graph['path'])
        if publish:
            self.assertTrue(row['store'].remember(row['descriptor'], row['cached'], row['response']))
        return row

    def test_normalized_graph_record_and_lightweight_launch_walk_all_ancestors(self):
        first = self.row(1)
        second = self.row(2, parent=first['graph'], nonce='1' * 64)
        saved = json.loads(second['pointer'].read_text())
        rail = saved['accepted_checkpoint']['rail_cache']
        self.assertEqual(rail['format'], cache.GRAPH_FORMAT)
        self.assertEqual([r['format'] for r in rail['lineage']], ['DGD1', 'DGI1'])
        with patch.object(cache, 'digest', wraps=cache.digest) as hashes:
            self.assertEqual(second['store'].select(second['descriptor'], second['cached']), Path(second['cached']['scene_overlay']))
        self.assertNotIn(first['graph'], [Path(call.args[0]) for call in hashes.call_args_list])
        exposed = cache.manifest(self.root, second['marker'], base='b' * 64, scene=second['cached']['report']['scene_fingerprint'])
        cache.graph_cache_record(self.root, rail, exposed, verify_payloads=True)

    def test_offline_preparation_prefers_graph_and_setup_preflight_reuses_it(self):
        row = self.row(1, publish=False)
        value = json.loads(row['marker'].read_text())
        checkpoint = row['response']['world']['scene_checkpoint']
        declaration = {'schema': 1, 'ready': True, 'manifest_file': str(row['marker']),
                       'manifest_sha256': cache.digest(row['marker']), 'algorithm_sha256': value['algorithm_sha256'],
                       'base_fingerprint': value['base_fingerprint'], 'scene_fingerprint': value['scene_fingerprint'],
                       'cells': len(value['cells']), 'segments': value['segments'], 'graph_cache': checkpoint['graph_cache']}
        # Offline setup may additionally expose a legacy DWR for older callers.
        declaration['rail_cache'] = {'format': cache.DWR_FORMAT, 'ready': False}
        prepared = cache.prepared_record(self.root, declaration, base=value['base_fingerprint'], scene=value['scene_fingerprint'])
        self.assertEqual(prepared['rail_cache']['format'], cache.GRAPH_FORMAT)
        self.assertEqual(cache.check_record(self.root, prepared, base=value['base_fingerprint'], scene=value['scene_fingerprint']),
                         {key: value for key, value in prepared.items() if key != 'schema'})

    def test_wrong_location_graph_algorithm_snapshot_and_parent_traversal_rejected(self):
        row = self.row(1, publish=False)
        exposed = cache.manifest(self.root, row['marker'], base='b' * 64, scene=row['cached']['report']['scene_fingerprint'])
        original = row['response']['world']['scene_checkpoint']['graph_cache']
        for changed in [{'extraction_algorithm_sha256': 'e' * 64}, {'algorithm_sha256': 'e' * 64},
                        {'manifest_sha256': 'e' * 64}, {'path': str(row['marker'])}, {'points': 999}, {'schema': True}]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                cache.graph_cache_record(self.root, {**original, **changed}, exposed, native_path=True)
        second = self.row(2, parent=row['graph'], publish=False, nonce='2' * 64)
        raw = bytearray(second['graph'].read_bytes())
        start = 128 + 32 + 8
        raw[start:start + 3] = b'../'
        second['graph'].write_bytes(raw)
        with self.assertRaisesRegex(ValueError, 'parent path'):
            cache.graph_lineage(self.root, second['graph'])

    def test_missing_corrupt_ancestor_and_cycles_rejected_and_warm_pointer_preserved(self):
        first = self.row(1); second = self.row(2, parent=first['graph'], nonce='3' * 64)
        original = first['graph'].read_bytes()
        raw = bytearray(original); raw[-1] ^= 1; first['graph'].write_bytes(raw)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            cache.graph_lineage(self.root, second['graph'], verify_payloads=True)
        first['graph'].write_bytes(original)
        # Point the old base at the descendant while keeping each link's exact
        # snapshot/name pins. Repeated immutable paths reveal the cycle.
        marker = first['marker']; make_graph(marker, parent=second['graph'])
        with self.assertRaisesRegex(ValueError, 'cycle'):
            cache.graph_lineage(self.root, second['graph'])
        self.assertTrue(second['pointer'].is_file())
        first['graph'].unlink()
        self.assertIsNone(second['store'].select(second['descriptor'], second['cached']))
        self.assertTrue(second['pointer'].is_file())

    def test_checkpoint_worker_accepts_native_graph_aliases_and_retains_lineage(self):
        first = self.row(1, publish=False)
        request = {'op': 'remember', 'root': str(self.root), 'worker': str(self.fixture.worker), 'parent_pid': 123,
                   'descriptor': {k: first['descriptor'][k] for k in ('mode', 'world_session', 'revision')},
                   'cached': first['cached'], 'result': first['response']}
        output = io.StringIO()
        with patch('skate_process.Singleton') as singleton, patch('skate_process.ParentProcess') as parent:
            singleton.return_value.__enter__.return_value.acquired = False
            parent.return_value.__enter__.return_value.exited.return_value = False
            checkpoint_worker.checkpoint_child(io.StringIO(json.dumps(request) + '\n'), output, io.StringIO())
        self.assertEqual(json.loads(output.getvalue()), {'ok': True, 'retained': True, 'error': None})
        record = next(iter(retention._load(self.root)['records'].values()))
        self.assertEqual(record['rails']['kind'], 'graph')
        self.assertEqual(len(record['graph_lineage']), 1)
        bad = copy.deepcopy(request); bad['result']['world']['scene_checkpoint']['rail_cache']['path'] = str(self.fixture.base)
        with self.assertRaises(ValueError):
            checkpoint_worker.read_request(io.StringIO(json.dumps(bad) + '\n'))

    def test_retained_descendant_keeps_retired_base_graph_and_unknown_delta_ancestors(self):
        first = self.row(1)
        second = self.row(2, parent=first['graph'], nonce='4' * 64)
        current = self.row(3, parent=second['graph'], nonce='5' * 64)
        self.assertEqual(retention.collect(self.root)['status'], 'complete')
        self.assertFalse(first['marker'].exists())
        self.assertTrue(first['graph'].exists()); self.assertTrue(second['graph'].exists()); self.assertTrue(current['graph'].exists())
        # Unaccepted native work is a protection root, even after its source
        # scene falls out of the two accepted rollback slots.
        unknown = self.row(4, parent=current['graph'], nonce='6' * 64, publish=False)
        self.row(5); self.row(6)
        self.assertEqual(retention.collect(self.root)['status'], 'complete')
        self.assertTrue(current['graph'].exists()); self.assertTrue(unknown['graph'].exists())

    def test_independent_retired_graph_is_deleted_only_after_body_pin_matches(self):
        first = self.row(1); self.row(2); self.row(3)
        result = retention.collect(self.root)
        self.assertEqual(result['status'], 'complete'); self.assertFalse(first['graph'].exists())
        old = self.row(7); self.row(8); self.row(9)
        raw = bytearray(old['graph'].read_bytes()); raw[-1] ^= 1; old['graph'].write_bytes(raw)
        result = retention.collect(self.root)
        self.assertEqual(result['removed'], 0)
        self.assertTrue(old['marker'].exists()); self.assertTrue(old['graph'].exists())

    def test_revisited_scene_keeps_canonical_warm_target_with_distinct_owner_generation(self):
        first = self.row(1)
        second = self.row(2)
        current = self.row(1, parent=second['graph'], nonce='7' * 64)
        self.assertNotEqual(first['graph'], current['graph'])
        self.assertEqual(retention.collect(self.root)['status'], 'complete')
        self.assertTrue(first['graph'].exists(), 'Native warm initialization requires the canonical first generation')
        self.assertTrue(current['graph'].exists())
        self.assertEqual(current['store'].select(current['descriptor'], current['cached']), Path(current['cached']['scene_overlay']))


if __name__ == '__main__':
    unittest.main()
