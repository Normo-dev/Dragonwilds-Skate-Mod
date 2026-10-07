"""Keep large checkpoint validation and owned-cache retirement off the frame loop.

Only the relay thread calls the native protocol. A maintenance lease covers the
entire collector lifetime; cache dispatch stays blocked until its release is
acknowledged. Children get a job before their stdin handshake permits any I/O.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time


class Child:
    def __init__(self, script, payload, log):
        from skate_process import ChildJob
        self.job = ChildJob()
        self.output = tempfile.TemporaryFile(mode='w+b')
        self.process = None
        self.assigned = False
        self.started = time.monotonic()
        try:
            self.process = subprocess.Popen([sys.executable, '-B', str(script), '_child'],
                stdin=subprocess.PIPE, stdout=self.output, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            self.job.assign(self.process)
            self.assigned = True
            data = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode() + b'\n'
            self.process.stdin.write(data)
            self.process.stdin.close()
        except Exception:
            self.close()
            raise

    def poll(self):
        return self.process.poll()

    def result(self):
        self.output.seek(0)
        raw = self.output.read(65537)
        if len(raw) > 65536 or self.process.returncode != 0:
            raise ValueError('Background cache helper failed or returned oversized output')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('Background cache helper returned invalid output')
        return result

    def close(self):
        # Job close kills this child before the native lease can be released.
        self.job.close()
        try:
            if self.process is not None:
                if not self.assigned:
                    # Job assignment can fail before the child is protected.
                    # It is still waiting for our handshake; stop only this
                    # newly created process, never an unrelated helper.
                    try:self.process.stdin.close()
                    except (OSError,ValueError):pass
                    if self.process.poll() is None:self.process.terminate()
                self.process.wait(timeout=5)
        finally:
            self.output.close()


def checkpoint_payload(paths, descriptor, cached, response):
    world = response.get('world', {})
    return {'op': 'promote' if response.get('collision_revision') == descriptor['revision'] else 'remember',
        'root': str(Path(paths['map_data']).resolve()), 'worker': str(Path(paths['worker']).resolve()),
        'parent_pid': os.getpid(),
        'descriptor': {key: descriptor[key] for key in ('mode', 'world_session', 'revision')},
        'cached': {'world_manifest': cached['world_manifest'], 'scene_overlay': cached['scene_overlay'],
            'report': {key: cached['report'][key] for key in
                ('world', 'world_session', 'source_fingerprint', 'scene_fingerprint')}},
        'result': {'ok': response.get('ok'), 'collision_revision': response.get('collision_revision'),
            'world': {key: world.get(key) for key in ('scene_fingerprint', 'scene_checkpoint')}}}


class BackgroundCache:
    def __init__(self, paths, request, log, *, spawn=Child, clock=time.monotonic, managed=None):
        self.paths, self.request, self.log = paths, request, log
        self.spawn, self.clock = spawn, clock
        self.managed = os.environ.get('S3_MANAGED_RELAY') == '1' if managed is None else managed
        self.child = None
        self.kind = None
        self.lease = None
        self.queued = None
        self.serial = 0
        self.child_serial = None
        self.retained = False
        self.due = False
        self.next_attempt = 0.
        self.started = 0.

    @property
    def blocked(self):
        """All Python/native cache dispatch waits for either child to finish."""
        return self.child is not None or self.queued is not None or self.lease is not None

    def invalidate(self):
        self.serial += 1
        self.retained = False
        self.due = False
        self.queued = None

    def accepted(self, descriptor, cached, response):
        self.invalidate()
        if not self.managed or descriptor.get('mode') != 'whole_world':
            return
        world = response.get('world')
        checkpoint = world.get('scene_checkpoint') if isinstance(world, dict) else None
        if not isinstance(checkpoint, dict) or checkpoint.get('format') != 'exposed_edges_v1':
            return
        self.queued = checkpoint_payload(self.paths, descriptor, cached, response)

    def _start(self, kind, payload):
        script = Path(__file__).resolve().parent / ('skate_checkpoint_worker.py' if kind == 'checkpoint' else 'skate_cache_retention.py')
        self.child = self.spawn(script, payload, self.log)
        self.kind = kind
        self.child_serial = self.serial
        self.started = self.clock()

    def _release(self):
        if self.lease is None:
            return
        identity, token = self.lease
        result = self.request({'op': 'maintenance_end', 'worker_id': identity, 'lease_token': token})
        status = result.get('maintenance', {})
        if (result.get('status') != 'maintenance_released' or status.get('worker_id') != identity
                or status.get('lease_active') is not False):
            raise RuntimeError('Cache maintenance lease release was not acknowledged')
        self.lease = None

    def pump(self, *, can_collect):
        if self.child is not None:
            expired = self.clock() - self.started > (180 if self.kind == 'checkpoint' else 75)
            if self.child.poll() is None and not expired:
                return
            kind, serial = self.kind, self.child_serial
            try:
                result = self.child.result() if not expired else {'status': 'timed_out'}
            except Exception as error:
                result = {'status': 'failed', 'error': str(error)}
            finally:
                # Never drop a lease while a collector might still be running.
                self.child.close()
                self.child = None
                self.kind = None
                self._release()
            print('CACHE BACKGROUND', kind, json.dumps(result, separators=(',', ':')), flush=True)
            if kind == 'checkpoint' and serial == self.serial:
                self.retained = result.get('ok') is True and result.get('retained') is True
                self.due = self.retained
            elif kind == 'collect':
                self.due = result.get('status') in ('deferred', 'timed_out')
                self.next_attempt = self.clock() + 60.
        if self.queued is not None:
            payload, self.queued = self.queued, None
            try:
                self._start('checkpoint', payload)
            except Exception as error:
                self.retained = False
                print('CACHE CHECKPOINT START SKIPPED', str(error), flush=True)
            return
        if not (self.managed and can_collect and self.retained and self.due and self.clock() >= self.next_attempt):
            return
        result = self.request({'op': 'maintenance_status'})
        status = result.get('maintenance', {})
        if status.get('available') is not True or status.get('quiescent') is not True or status.get('lease_active') is not False:
            self.next_attempt = self.clock() + 1.
            return
        identity = status.get('worker_id')
        acquired = self.request({'op': 'maintenance_begin', 'worker_id': identity})
        token = acquired.get('lease_token')
        state = acquired.get('maintenance', {})
        self.lease = (identity, token)
        if (not isinstance(token, str) or acquired.get('status') != 'maintenance_acquired'
                or state.get('worker_id') != identity or state.get('retirement_allowed') is not True):
            raise RuntimeError('Native cache maintenance barrier was not established')
        try:
            self._start('collect', {'op': 'collect', 'root': str(Path(self.paths['map_data']).resolve()),
                'parent_pid': os.getpid(), 'budget_seconds': 60})
        except Exception as error:
            self._release()
            self.due = False
            print('CACHE MAINTENANCE START SKIPPED', str(error), flush=True)

    def close(self):
        if self.child is not None:
            self.child.close()
            self.child = None
        self._release()

    def cancel_retirement(self):
        """New observed geometry has priority over optional space reclamation."""
        if self.kind != 'collect':
            return
        self.child.close()
        self.child = None
        self.kind = None
        self._release()
        self.due = True
        self.next_attempt = self.clock() + 10.
