"""Progress is observational; counts and completion never drive setup work."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from skate_setup_progress import SetupProgress, MapProgressSource, action_progress, latest_progress


class SetupProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / 'setup-logs/grind-rails.log'
        self.log.parent.mkdir()

    def append(self, text):
        with self.log.open('a', encoding='utf-8') as stream:
            stream.write(text)

    def test_unchanged_heartbeat_advances_elapsed_without_repeated_log(self):
        now = [10.0]; events = []
        display = SetupProgress(events.append, 'terrain', 'Exporting terrain', clock=lambda: now[0])
        display.tick(); now[0] += 3663; value = display.tick()
        self.assertEqual(value['elapsed'], '1h 01m 03s')
        self.assertFalse(value['changed'])
        self.assertNotIn('percent', value)
        self.assertNotIn('total', value)
        self.assertEqual(len(events), 2)

    def test_new_action_ignores_old_complete_log_and_marker(self):
        self.append('WORLD_EXPOSED_PROGRESS total=100 cells=100 rebuilt=20 reused=80\n')
        (self.root / 'setup-state.json').write_text(json.dumps({'stage': 'ready'}))
        source = MapProgressSource(self.root)
        self.assertIsNone(source())
        events = []
        display = SetupProgress(events.append, 'checking', 'Checking saved work', source=source)
        self.assertNotIn('percent', display.tick())
        self.append('DWS_CACHE_PHASE operation=prepare phase=collision_load state=start elapsed_ms=0\n')
        self.assertEqual(display.tick()['phase'], 'collision_load')

    def test_known_counts_then_new_phase_clears_percentage(self):
        source = MapProgressSource(self.root)
        display = SetupProgress(lambda _: None, 'checking', 'Checking', source=source)
        self.append('WORLD_EXPOSED_PROGRESS total=100 cells=40 rebuilt=10 reused=30\n')
        value = display.tick()
        self.assertEqual((value['completed'], value['total'], value['percent']), (40,100,40.0))
        self.append('DWS_CACHE_PHASE operation=prepare phase=joining_grind_paths state=start elapsed_ms=0\n')
        value = display.tick()
        self.assertEqual(value['phase'], 'joining_grind_paths')
        self.assertNotIn('total', value)
        self.assertNotIn('completed', value)

    def test_cached_load_kind_uses_saved_edges_label_with_shared_reader(self):
        source = MapProgressSource(self.root)
        display = SetupProgress(lambda _: None, 'checking', 'Checking', source=source)
        self.append('WORLD_EXPOSED_PROGRESS kind=load total=100 cells=40 rebuilt=0 reused=40\n')
        value = display.tick()
        self.assertEqual((value['phase'], value['label']),
                         ('loading_cached_edges', 'Loading saved grind edges'))
        self.assertEqual((value['completed'], value['total'], value['percent']), (40, 100, 40.))
        self.assertEqual(latest_progress(self.log), 'Loading saved grind edges — 40 / 100 sections (40%)')

    def test_rotation_truncation_and_incomplete_line_cannot_retain_old_count(self):
        source = MapProgressSource(self.root)
        self.append('WORLD_EXPOSED_PROGRESS total=100 cells=80 rebuilt=80 reused=0\n')
        self.assertEqual(source()['completed'], 80)
        self.log.rename(self.log.with_suffix('.old'))
        self.log.write_text('DWS_CACHE_PHASE operation=prepare phase=collision_load')
        self.assertNotIn('completed', source())
        self.append(' state=start elapsed_ms=0\n')
        self.assertEqual(source()['phase'], 'collision_load')
        self.log.write_text('')
        self.assertNotIn('completed', source())
        self.append('WORLD_EXPOSED_PROGRESS total=100 cells=1 rebuilt=1 reused=0\n')
        self.assertEqual(source()['completed'], 1)

    def test_marker_changes_report_export_stage_without_invented_percentage(self):
        source = MapProgressSource(self.root)
        for stage in ('terrain', 'objects-and-buildings', 'complete-world-index'):
            (self.root / 'setup-state.json').write_text(json.dumps({'stage': stage}))
            value = source()
            self.assertEqual(value['phase'], stage)
            self.assertNotIn('percent', value)

    def test_phase_end_keeps_phase_elapsed_and_next_phase_resets_it(self):
        now = [10.0]
        source = MapProgressSource(self.root)
        display = SetupProgress(lambda _: None, 'checking', 'Checking', source=source, clock=lambda: now[0])
        self.append('DWS_CACHE_PHASE operation=prepare phase=collision_load state=start elapsed_ms=0\n')
        display.tick()
        now[0] += 12
        self.append('DWS_CACHE_PHASE operation=prepare phase=collision_load state=end elapsed_ms=12000\n')
        self.assertEqual(display.tick()['phase_elapsed'], '0m 12s')
        self.append('DWS_CACHE_PHASE operation=prepare phase=joining_grind_paths state=start elapsed_ms=12000\n')
        self.assertEqual(display.tick()['phase_elapsed'], '0m 00s')

    def test_setup_does_not_use_scene_or_init_phase_as_its_own_progress(self):
        source = MapProgressSource(self.root)
        (self.root / 'setup-state.json').write_text(json.dumps({'stage': 'terrain'}))
        for operation in ('scene', 'init'):
            self.append(f'DWS_CACHE_PHASE operation={operation} phase=ready state=end elapsed_ms=0\n')
            value = source()
            self.assertEqual(value['phase'], 'terrain')
            self.assertNotIn('percent', value)

    def test_malformed_counts_and_partial_marker_are_ignored(self):
        source = MapProgressSource(self.root)
        self.append('WORLD_EXPOSED_PROGRESS total=2 cells=3 rebuilt=3 reused=0\n')
        (self.root / 'setup-state.json').write_text('{')
        self.assertIsNone(source())
        self.assertIsNone(latest_progress(self.log))

    def test_worker_emits_unchanged_heartbeat_and_stops_before_result(self):
        events = []; heartbeat = threading.Event()
        def emit(value):
            events.append(value)
            if len(events) >= 2: heartbeat.set()
        with action_progress(emit, 'catalogue', 'Reading building catalogue', interval=0.01):
            self.assertTrue(heartbeat.wait(1.0))
        events.append({'ok': True})
        self.assertFalse(any(t.name == 'setup-progress' for t in threading.enumerate()))
        self.assertEqual(events[-1], {'ok': True})
        self.assertFalse(events[1]['changed'])

    def test_real_error_propagates_and_native_ready_never_declares_success(self):
        events = []
        source = MapProgressSource(self.root)
        self.append('DWS_CACHE_PHASE operation=prepare phase=ready state=end elapsed_ms=42\n')
        with self.assertRaisesRegex(ValueError, 'real operation failed'):
            with action_progress(events.append, 'checking', 'Checking', source=source):
                raise ValueError('real operation failed')
        self.assertTrue(events)
        self.assertEqual(events[0]['phase'], 'ready')
        self.assertTrue(all('ok' not in value for value in events))
        self.assertFalse(any(t.name == 'setup-progress' for t in threading.enumerate()))


if __name__ == '__main__':
    unittest.main()
