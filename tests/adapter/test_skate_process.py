"""Exercise real Windows process lifetimes using isolated, inert child programs."""
from pathlib import Path
import ctypes
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from skate_process import ChildJob, ExtendedLimits, ParentProcess, Singleton


class ProcessLifetimes(unittest.TestCase):
    def test_singleton_is_exclusive_then_released(self):
        name = 'Local\\DragonwildsSkate.test.singleton.' + uuid.uuid4().hex
        with Singleton(name) as first:
            self.assertTrue(first.acquired)
            with Singleton(name) as second:
                self.assertFalse(second.acquired)
        with Singleton(name) as after:
            self.assertTrue(after.acquired)

    def test_job_owns_only_started_child_tree(self):
        self.assertEqual(ctypes.sizeof(ExtendedLimits), 144)
        with tempfile.TemporaryDirectory(prefix='skate process test ') as temporary:
            ready = Path(temporary) / 'grandchild.pid'
            script = """import pathlib, subprocess, sys, time
assert sys.stdin.readline() == 'start\\n'
p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], creationflags=subprocess.CREATE_NO_WINDOW)
pathlib.Path(sys.argv[1]).write_text(str(p.pid))
time.sleep(60)
"""
            with ParentProcess(os.getpid()) as own_process, ChildJob() as job:
                process = subprocess.Popen([sys.executable, '-c', script, str(ready)],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    job.assign(process)
                    process.stdin.write('start\n')
                    process.stdin.close()
                    deadline = time.monotonic() + 10
                    while not ready.exists() and time.monotonic() < deadline:
                        self.assertIsNone(process.poll())
                        time.sleep(.03)
                    self.assertTrue(ready.exists())
                    with ParentProcess(int(ready.read_text())) as grandchild:
                        self.assertFalse(grandchild.exited())
                        job.close()
                        process.wait(timeout=10)
                        deadline = time.monotonic() + 5
                        while not grandchild.exited() and time.monotonic() < deadline:
                            time.sleep(.03)
                        self.assertTrue(grandchild.exited())
                    self.assertFalse(own_process.exited())
                finally:
                    job.close()
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
