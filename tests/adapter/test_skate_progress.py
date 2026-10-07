from pathlib import Path
import tempfile
import re
import unittest

from skate_progress import ProgressReader, parse_line, format_elapsed, describe


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='progress-', dir=Path(__file__).parent)
        self.path = Path(self.folder.name) / 'progress.log'

    def tearDown(self):
        self.folder.cleanup()

    def line(self, cells=30):
        return f'WORLD_EXPOSED_PROGRESS total=100 cells={cells} rebuilt={cells-10} reused=10 segments=400 elapsed_ms=500\n'

    def test_old_log_is_not_shown_as_current_progress(self):
        self.path.write_text(self.line(99))
        reader = ProgressReader(self.path)
        self.assertIsNone(reader.poll())
        with self.path.open('a') as stream:stream.write('unrelated diagnostic\n')
        self.assertIsNone(reader.poll())
        with self.path.open('a') as stream:stream.write(self.line())
        value = reader.poll()
        self.assertEqual(value['percent'], 30.)
        self.assertEqual(reader.poll(), value)  # caller may publish elapsed heartbeat

    def test_partial_line_waits_then_complete_counts_are_read(self):
        reader = ProgressReader(self.path)
        line = self.line()
        self.path.write_text(line[:45])
        self.assertIsNone(reader.poll())
        with self.path.open('a') as stream:stream.write(line[45:])
        self.assertEqual(reader.poll()['completed'], 30)

    def test_rotation_and_truncation_clear_the_previous_phase(self):
        self.path.write_text(self.line())
        reader = ProgressReader(self.path, start_at_end=False)
        self.assertEqual(reader.poll()['completed'], 30)
        self.path.write_text('new operation\n')
        self.assertIsNone(reader.poll())
        self.path.rename(self.path.with_suffix('.old'))
        self.path.write_text('DWS_CACHE_PHASE operation=init phase=loading_cached_grind_data state=start elapsed_ms=0\n')
        value = reader.poll()
        self.assertEqual(value['label'], 'Loading saved grind paths')
        self.assertNotIn('percent', value)

    def test_known_percentage_is_stage_local_and_never_prematurely_100(self):
        line = 'WORLD_EXPOSED_PROGRESS total=100000 cells=99999 rebuilt=99999 reused=0'
        self.assertEqual(parse_line(line)['percent'], 99.9)
        value = parse_line('DWS_CACHE_PHASE operation=prepare phase=joining_grind_paths state=start elapsed_ms=0')
        self.assertNotIn('percent', value)
        self.assertEqual(describe(value), 'Joining connected grind paths')

    def test_invalid_counts_and_unknown_phase_never_become_a_percentage(self):
        for line in [self.line().replace('rebuilt=20', 'rebuilt=21'),
                     self.line().replace('total=100', 'total=1'),
                     self.line() + ' cells=30',
                     'WORLD_EXPOSED_PROGRESS cells=20',
                     'DWS_CACHE_PHASE operation=prepare phase=untrusted_text state=start elapsed_ms=0']:
            self.assertIsNone(parse_line(line))
        self.assertNotIn('percent', parse_line('WORLD_EXPOSED_PROGRESS total=0 cells=0 reused=0 rebuilt=0'))

    def test_large_unrelated_log_tail_remains_bounded(self):
        reader = ProgressReader(self.path)
        self.path.write_text('ignored\n' * 20000 + self.line())
        self.assertEqual(reader.poll()['completed'], 30)
        self.assertLessEqual(len(reader.partial), 8192)

    def test_elapsed_display(self):
        self.assertEqual(format_elapsed(3148.7), '52m 28s')
        self.assertEqual(format_elapsed(3670), '1h 01m 10s')
        self.assertEqual(format_elapsed(float('nan')), '0m 00s')

    def test_cached_load_kind_preserves_counts_and_old_protocol(self):
        old = parse_line(self.line())
        self.assertEqual(old, {'phase': 'preparing_exposed_cells', 'label': 'Generating grind edges',
                               'state': 'working', 'completed': 30, 'reused': 10,
                               'total': 100, 'percent': 30.})
        for line in (self.line().strip() + ' kind=load',
                     self.line().replace('total=100', 'kind=load total=100')):
            self.assertEqual(parse_line(line), old | {'phase': 'loading_cached_edges',
                                                      'label': 'Loading saved grind edges'})
        complete = parse_line('WORLD_EXPOSED_PROGRESS kind=load total=100 cells=100 rebuilt=0 reused=100')
        self.assertEqual(complete['percent'], 100.)
        self.assertNotIn('ok', complete)  # The result still declares readiness.

    def test_invalid_or_duplicate_load_kind_is_rejected(self):
        for suffix in ('kind=', 'kind=unknown', 'kind=derive', 'kind=Load', 'kind=loadx',
                       'kind=load kind=load', 'kind=load kind=unknown'):
            self.assertIsNone(parse_line(self.line().strip() + ' ' + suffix))

    def test_host_whitelist_matches_the_cached_edges_phase(self):
        bridge = (Path(__file__).parent / 'host-probe/bridge.lua').read_text(encoding='utf-8')
        labels = bridge.split('local progressLabels={', 1)[1].split('}', 1)[0]
        self.assertRegex(labels, re.compile(r"\bloading_cached_edges\s*=\s*['\"]Loading saved grind edges['\"]"))

    def test_deleted_log_does_not_keep_stale_percentage(self):
        self.path.write_text(self.line())
        reader = ProgressReader(self.path, start_at_end=False)
        self.assertEqual(reader.poll()['completed'], 30)
        self.path.unlink()
        self.assertIsNone(reader.poll())
        self.assertEqual(reader.offset, 0)


if __name__ == '__main__':
    unittest.main()
