"""Isolated failure/recovery checks for private asset import; no game paths."""
from pathlib import Path
import json
import os
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import skate_asset_import as importer


ROOT = Path(__file__).resolve().parent


class AssetImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT, prefix='asset-import-check-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'prepared assets 路径 & $(literal)'
        self.target = self.root / 'private data' / 'assets'
        self.stage = self.target.with_name('assets.import-stage')
        self.source.mkdir()
        self.write('private/game.json', json.dumps({'version': 1, 'character_scene': 'private/skater.glb',
            'initial_animation': 'IDLE', 'action_graph': 'private/action.stategraph',
            'motion_graph': 'private/motion.stategraph'}).encode())
        self.write('private/skater.glb', struct.pack('<III', 0x46546C67, 2, 12))
        self.write('private/action.stategraph', b'action')
        self.write('private/motion.stategraph', b'motion')
        self.write('private/stock/physics-skeletons.json', b'{}')
        self.write('private/stock/skater-collections.json', b'{}')
        self.before = importer._inventory(self.source)

    def write(self, relative, data):
        file = self.source / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        return file

    def interrupt(self):
        original = importer._copy_verified
        calls = []
        def copy(source, pending, record):
            calls.append(record['path'])
            if len(calls) == 2:
                pending.write_bytes(b'partial')
                raise OSError('injected interruption')
            return original(source, pending, record)
        with patch.object(importer, '_copy_verified', side_effect=copy):
            with self.assertRaisesRegex(OSError, 'injected interruption'):
                importer.import_assets(self.source, self.target)
        self.assertFalse(self.target.exists())
        return calls

    def directory_link(self, link, target):
        link.parent.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                self.skipTest('Directory junction creation unavailable')
        else:
            link.symlink_to(target, target_is_directory=True)
        # Remove only the link itself before TemporaryDirectory cleanup.
        self.addCleanup(lambda: os.rmdir(link) if os.name == 'nt' and link.exists() else link.unlink(missing_ok=True))

    def test_success_idempotency_and_source_untouched(self):
        result = importer.import_assets(self.source, self.target)
        self.assertEqual(result['copied_files'], len(self.before))
        self.assertFalse(result['resumed'])
        self.assertEqual(importer._inventory(self.target), self.before)
        self.assertEqual(importer._inventory(self.source), self.before)
        self.assertFalse((self.stage / 'payload').exists())
        self.assertTrue(importer.import_assets(self.source, self.target)['already_prepared'])
        self.assertTrue(importer.import_assets(self.target, self.target)['already_prepared'])

    def test_interrupted_copy_resumes_without_rewriting_verified_file(self):
        calls = self.interrupt()
        verified = self.stage / 'payload' / calls[0]
        stamp = verified.stat().st_mtime_ns
        result = importer.import_assets(self.source, self.target)
        self.assertTrue(result['resumed'])
        self.assertEqual(result['skipped_files'], 1)
        self.assertEqual((self.target / calls[0]).stat().st_mtime_ns, stamp)
        self.assertEqual(importer._inventory(self.target), self.before)
        self.assertEqual(importer._inventory(self.source), self.before)

    def test_changed_source_refuses_existing_stage_without_writes(self):
        self.interrupt()
        snapshot = importer._inventory(self.stage)
        self.write('private/action.stategraph', b'changed source')
        with self.assertRaisesRegex(ValueError, 'different source data'):
            importer.import_assets(self.source, self.target)
        self.assertEqual(importer._inventory(self.stage), snapshot)
        self.assertFalse(self.target.exists())

    def test_source_changes_during_copy_never_publishes(self):
        original = importer._copy_verified
        changed = [False]
        def copy(source, pending, record):
            original(source, pending, record)
            if not changed[0]:
                changed[0] = True
                self.write('added-during-import.txt', b'new file')
        with patch.object(importer, '_copy_verified', side_effect=copy):
            with self.assertRaisesRegex(ValueError, 'inventory changed'):
                importer.import_assets(self.source, self.target)
        self.assertFalse(self.target.exists())

    def test_foreign_target_and_unknown_stage_preserved(self):
        self.target.mkdir(parents=True)
        (self.target / 'personal.txt').write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'Destination already contains'):
            importer.import_assets(self.source, self.target)
        self.assertEqual((self.target / 'personal.txt').read_bytes(), b'keep')
        self.assertFalse(self.stage.exists())
        other = self.root / 'second' / 'assets'
        stage = other.with_name('assets.import-stage')
        stage.mkdir(parents=True)
        (stage / 'foreign.txt').write_bytes(b'also keep')
        with self.assertRaisesRegex(ValueError, 'unknown asset import stage'):
            importer.import_assets(self.source, other)
        self.assertEqual((stage / 'foreign.txt').read_bytes(), b'also keep')

    def test_changed_completed_stage_is_preserved(self):
        calls = self.interrupt()
        staged = self.stage / 'payload' / calls[0]
        staged.write_bytes(b'user changed staged bytes')
        with self.assertRaisesRegex(ValueError, 'Completed staged asset changed'):
            importer.import_assets(self.source, self.target)
        self.assertEqual(staged.read_bytes(), b'user changed staged bytes')
        self.assertFalse(self.target.exists())

    def test_unknown_stage_payload_preserved(self):
        self.interrupt()
        extra = self.stage / 'payload' / 'foreign.txt'
        extra.write_bytes(b'keep this')
        with self.assertRaisesRegex(ValueError, 'unknown payload entry'):
            importer.import_assets(self.source, self.target)
        self.assertEqual(extra.read_bytes(), b'keep this')

    def test_empty_existing_target_is_preserved(self):
        self.target.mkdir(parents=True)
        importer.import_assets(self.source, self.target)
        backup = self.stage / 'original-empty-target'
        self.assertTrue(backup.is_dir())
        self.assertEqual(list(backup.iterdir()), [])
        self.assertEqual(importer._inventory(self.target), self.before)

    def test_publication_interruption_is_idempotent(self):
        original = importer.os.rename
        def rename(source, destination):
            original(source, destination)
            if Path(source) == self.stage / 'payload':
                raise OSError('injected after publish')
        with patch.object(importer.os, 'rename', side_effect=rename):
            with self.assertRaisesRegex(OSError, 'after publish'):
                importer.import_assets(self.source, self.target)
        self.assertTrue(importer.import_assets(self.source, self.target)['already_prepared'])

    def test_disk_space_failure_leaves_resumable_owned_stage(self):
        with patch.object(importer.shutil, 'disk_usage', return_value=type('Space', (), {'free': 0})()):
            with self.assertRaisesRegex(ValueError, 'disk space'):
                importer.import_assets(self.source, self.target)
        self.assertTrue((self.stage / 'owner.json').is_file())
        self.assertFalse(self.target.exists())
        self.assertTrue(importer.import_assets(self.source, self.target)['resumed'])

    def test_linked_source_and_stage_are_rejected_without_foreign_writes(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'keep.txt').write_bytes(b'keep')
        linked = self.source / 'linked'
        self.directory_link(linked, outside)
        with self.assertRaisesRegex(ValueError, 'linked path'):
            importer.import_assets(self.source, self.target)
        self.assertFalse(self.stage.exists())
        self.assertEqual((outside / 'keep.txt').read_bytes(), b'keep')
        alternate = self.root / 'alternate' / 'assets'
        self.directory_link(alternate.with_name('assets.import-stage'), outside)
        clean = self.root / 'clean'
        clean.mkdir()
        # Use a source with valid contents, independent of the linked source.
        for record in self.before:
            destination = clean / record['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((self.source / record['path']).read_bytes())
        with self.assertRaisesRegex(ValueError, 'linked path'):
            importer.import_assets(clean, alternate)
        self.assertEqual(list(outside.iterdir()), [outside / 'keep.txt'])

    def test_link_inserted_in_owned_stage_is_rejected(self):
        self.interrupt()
        outside = self.root / 'outside'
        outside.mkdir()
        self.directory_link(self.stage / 'payload' / 'linked', outside)
        with self.assertRaisesRegex(ValueError, 'linked path'):
            importer.import_assets(self.source, self.target)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
