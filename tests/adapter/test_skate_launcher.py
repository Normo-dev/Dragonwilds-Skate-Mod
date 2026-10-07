"""Real supervisor/child startup, deduplication and stop; no game or live IPC.

Only release preflight is replaced here; its independent tests exercise actual
file/version checks. Child code is inert and uses an isolated temporary mailbox.
"""
from pathlib import Path
import json
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

import skate_launcher
from skate_process import ParentProcess, Singleton


class LauncherLifecycle(unittest.TestCase):
    def test_relocated_child_stops_and_reaps_descendants(self):
        with tempfile.TemporaryDirectory(prefix='skate launcher relocated ') as temporary:
            app = Path(temporary) / 'app'
            app.mkdir()
            for name in ['skate_launcher.py', 'skate_preflight.py', 'runtime_paths.py', 'skate_process.py',
                         'skate_cache_retention.py', 'skate_exposed_cache.py']:
                shutil.copyfile(Path(__file__).with_name(name), app / name)
            box = Path(temporary) / 'private/box'
            (app / 'runtime.json').write_text(json.dumps({'schema': 1, 'paths': {'mailbox': str(box)}}))
            (app / 'skate_relay.py').write_text('''from runtime_paths import load_paths
from pathlib import Path
import os, subprocess, sys, time
def run():
    assert os.environ.get('S3_MANAGED_RELAY') == '1'
    box = load_paths()['mailbox']
    p = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], creationflags=subprocess.CREATE_NO_WINDOW)
    (box/'fixture-grandchild.pid').write_text(str(p.pid))
    while not (box/'relay-stop.request').exists(): time.sleep(.025)
''')
            name = 'Local\\DragonwildsSkate.test.launcher.' + uuid.uuid4().hex
            result = []
            maintenance_calls = []
            def maintenance(paths):
                self.assertFalse((box / 'fixture-grandchild.pid').exists())
                maintenance_calls.append(paths)
                return {'status': 'fixture', 'removed': 0}
            with patch.object(skate_launcher, 'Singleton', lambda: Singleton(name)), \
                 patch('skate_cache_retention.launch_maintenance', side_effect=maintenance), \
                 patch.object(skate_launcher, 'check_installation', lambda _: {'release': 'test-fixture',
                     'map_manifest': str(Path(temporary) / 'inert-world.json'),
                     'rail_file': str(Path(temporary) / 'inert-world.lips')}):
                thread = threading.Thread(target=lambda: result.append(skate_launcher.supervise(app)), daemon=True)
                thread.start()
                try:
                    ready = box / 'fixture-grandchild.pid'
                    deadline = time.monotonic() + 15
                    while not ready.exists() and time.monotonic() < deadline:
                        if not thread.is_alive():
                            self.fail((box / 'launcher-status.json').read_text())
                        time.sleep(.025)
                    self.assertTrue(ready.exists())
                    with ParentProcess(int(ready.read_text())) as grandchild:
                        self.assertFalse(grandchild.exited())
                        self.assertEqual(skate_launcher.supervise(app), 0)
                        (box / 'relay-stop.request').write_text('stop\n')
                        thread.join(15)
                        self.assertFalse(thread.is_alive())
                        self.assertEqual(result, [0])
                        deadline = time.monotonic() + 5
                        while not grandchild.exited() and time.monotonic() < deadline:
                            time.sleep(.025)
                        self.assertTrue(grandchild.exited())
                        self.assertEqual(len(maintenance_calls), 1)
                        self.assertEqual(json.loads((box / 'launcher-status.json').read_text())['status'], 'stopped')
                finally:
                    box.mkdir(parents=True, exist_ok=True)
                    (box / 'relay-stop.request').write_text('stop\n')
                    thread.join(35)


if __name__ == '__main__':
    unittest.main()
