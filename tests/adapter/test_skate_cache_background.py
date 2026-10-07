import unittest
from pathlib import Path

from skate_cache_background import BackgroundCache


class Child:
    def __init__(self, payload):
        self.payload = payload
        self.done = False
        self.closed = False
        self.value = {'ok': True, 'retained': True}

    def poll(self):
        return 0 if self.done else None

    def result(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value

    def close(self):
        self.closed = True


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.children = []
        self.commands = []
        self.leased = False
        self.fail_spawn = False
        self.fail_release = False
        self.quiescent = True
        self.paths = {'map_data': Path(__file__).parent, 'worker': Path(__file__)}
        self.bg = BackgroundCache(self.paths, self.request, None, spawn=self.spawn,
            clock=lambda: self.now, managed=True)
        self.descriptor = {'mode': 'whole_world', 'world_session': 'save', 'revision': 1}
        self.cached = {'world_manifest': 'base', 'scene_overlay': 'overlay', 'report':
            {'world': 'map', 'world_session': 'save', 'source_fingerprint': 'b', 'scene_fingerprint': 's'}}
        self.result = {'ok': True, 'collision_revision': 1, 'world': {'scene_fingerprint': 's',
            'scene_checkpoint': {'format': 'exposed_edges_v1'}}}

    def request(self, value):
        self.commands.append(value['op'])
        status = {'available': True, 'worker_id': 'worker', 'quiescent': self.quiescent,
            'lease_active': self.leased, 'retirement_allowed': self.leased}
        if value['op'] == 'maintenance_begin':
            self.assertFalse(self.leased)
            self.leased = True
            return {'status': 'maintenance_acquired', 'lease_token': 'token',
                'maintenance': {**status, 'lease_active': True, 'retirement_allowed': True}}
        if value['op'] == 'maintenance_end':
            self.assertTrue(all(c.closed for c in self.children))
            if self.fail_release:
                raise RuntimeError('Native pipe failed')
            self.leased = False
            return {'status': 'maintenance_released', 'maintenance': {**status, 'lease_active': False}}
        return {'maintenance': status}

    def spawn(self, script, payload, log):
        if self.fail_spawn:
            raise OSError('Cannot create child')
        child = Child(payload)
        self.children.append(child)
        return child

    def checkpoint(self):
        self.bg.accepted(self.descriptor, self.cached, self.result)
        self.bg.pump(can_collect=False)
        self.children[-1].done = True

    def test_lease_spans_entire_collector_lifetime(self):
        self.checkpoint()
        self.bg.pump(can_collect=True)
        self.assertEqual(self.children[-1].payload['op'], 'collect')
        self.assertTrue(self.bg.blocked)
        self.assertTrue(self.leased)
        self.bg.pump(can_collect=True)
        self.assertNotIn('maintenance_end', self.commands)
        self.children[-1].value = {'status': 'complete', 'bytes': 123}
        self.children[-1].done = True
        self.bg.pump(can_collect=True)
        self.assertFalse(self.leased)
        self.assertFalse(self.bg.blocked)

    def test_catalog_failure_never_authorizes_retirement(self):
        self.checkpoint()
        self.children[-1].value = {'ok': True, 'retained': False}
        self.bg.pump(can_collect=True)
        self.assertEqual(self.commands, [])
        self.assertFalse(self.bg.retained)

    def test_invalidated_checkpoint_cannot_protect_new_resident_scene(self):
        self.checkpoint()
        self.bg.invalidate()
        self.bg.pump(can_collect=True)
        self.assertFalse(self.bg.retained)
        self.assertEqual(self.commands, [])

    def test_python_and_native_writer_barriers(self):
        self.checkpoint()
        self.bg.pump(can_collect=False)
        self.assertEqual(self.commands, [])
        self.quiescent = False
        self.bg.pump(can_collect=True)
        self.assertNotIn('maintenance_begin', self.commands)
        self.quiescent = True
        self.now = 2.
        self.bg.pump(can_collect=True)
        self.assertTrue(self.leased)
        self.bg.close()

    def test_spawn_failure_releases_acquired_lease(self):
        self.checkpoint()
        self.fail_spawn = True
        self.bg.pump(can_collect=True)
        self.assertFalse(self.leased)
        self.assertFalse(self.bg.blocked)

    def test_timeout_kills_collector_before_releasing(self):
        self.checkpoint()
        self.bg.pump(can_collect=True)
        self.now = 76.
        self.bg.pump(can_collect=True)
        self.assertFalse(self.leased)
        self.assertTrue(self.children[-1].closed)
        self.assertGreater(self.bg.next_attempt, self.now)

    def test_shutdown_waits_for_collector_exit(self):
        self.checkpoint()
        self.bg.pump(can_collect=True)
        self.bg.close()
        self.assertFalse(self.leased)
        self.assertTrue(self.children[-1].closed)

    def test_failed_release_stays_blocked_and_is_fatal(self):
        self.checkpoint()
        self.bg.pump(can_collect=True)
        self.children[-1].done = True
        self.fail_release = True
        with self.assertRaisesRegex(RuntimeError, 'pipe'):
            self.bg.pump(can_collect=True)
        self.assertTrue(self.bg.blocked)
        self.assertTrue(self.leased)

    def test_build_update_cancels_sweep_then_releases_lease(self):
        self.checkpoint()
        self.bg.pump(can_collect=True)
        self.bg.cancel_retirement()
        self.assertTrue(self.children[-1].closed)
        self.assertFalse(self.leased)
        self.assertFalse(self.bg.blocked)
        self.assertGreater(self.bg.next_attempt, self.now)

    def test_legacy_and_unmanaged_scenes_never_enter_collector(self):
        self.result['world']['scene_checkpoint']['format'] = 'accepted_incremental_v1'
        self.bg.accepted(self.descriptor, self.cached, self.result)
        self.bg.pump(can_collect=True)
        self.assertEqual(self.children, [])
        self.bg.managed = False
        self.result['world']['scene_checkpoint']['format'] = 'exposed_edges_v1'
        self.bg.accepted(self.descriptor, self.cached, self.result)
        self.bg.pump(can_collect=True)
        self.assertEqual(self.commands, [])


if __name__ == '__main__':
    unittest.main()
