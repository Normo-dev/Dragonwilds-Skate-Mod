"""Read-only setup progress. Only the operation's result declares success."""
from contextlib import contextmanager
from pathlib import Path
import json
import threading
import time

from skate_progress import ProgressReader, describe, format_elapsed, parse_line


SETUP_PHASES = {
    'collision-settings': 'Reading collision settings',
    'collision-policy': 'Checking collision rules',
    'terrain': 'Exporting terrain collision',
    'objects-and-buildings': 'Exporting world objects and buildings',
    'complete-world-index': 'Building the world collision index',
    'ready': 'Verifying prepared map data',
}


def latest_progress(path):
    """Compatibility helper; active monitors use a new-operation reader."""
    try:
        with Path(path).open('rb') as stream:
            stream.seek(0, 2)
            offset = max(0, stream.tell() - 65536)
            stream.seek(offset)
            raw = stream.read(65536)
        lines = raw.split(b'\n')[:-1]
        if offset and lines:
            lines = lines[1:]
        for raw_line in reversed(lines):
            value = parse_line(raw_line.decode('utf-8', errors='replace').strip())
            if value is not None:
                return describe(value)
    except (OSError, ValueError):
        pass
    return None


class SetupProgress:
    """Periodic structured snapshots, including during indeterminate work."""
    def __init__(self, emit, phase, label, *, source=None, clock=time.monotonic):
        self.emit, self.source, self.clock = emit, source, clock
        self.started = self.phase_started = clock()
        self.value = {'phase': phase, 'label': label, 'state': 'working'}
        self.lock = threading.Lock()
        self.previous = None

    def tick(self):
        with self.lock:
            current = self.source() if self.source else None
            if current is not None:
                old_key = (self.value.get('phase'), self.value.get('operation'))
                new_key = (current.get('phase'), current.get('operation'))
                if new_key != old_key:
                    self.phase_started = self.clock()
                self.value = dict(current)  # Clears old counts at a phase/reset.
            now = self.clock()
            value = dict(self.value)
            value.update(event='progress', elapsed_seconds=max(0, now-self.started),
                         elapsed=format_elapsed(now-self.started),
                         phase_elapsed=format_elapsed(now-self.phase_started))
            value['message'] = describe(self.value)
            signature = (value['phase'], value.get('state'), value['message'])
            value['changed'] = signature != self.previous
            self.previous = signature
            self.emit(value)
            return value


@contextmanager
def action_progress(emit, phase, label, *, interval=2.0, source=None, clock=time.monotonic):
    progress = SetupProgress(emit, phase, label, source=source, clock=clock)
    stop = threading.Event()
    progress.tick()

    def watch():
        while not stop.wait(interval):
            progress.tick()

    thread = threading.Thread(target=watch, name='setup-progress', daemon=True)
    thread.start()
    try:
        yield progress
    finally:
        stop.set()
        thread.join()


class MapProgressSource:
    def __init__(self, map_data):
        self.root = Path(map_data)
        self.reader = ProgressReader(self.root / 'setup-logs/grind-rails.log', start_at_end=True)
        self.marker = self.root / 'setup-state.json'
        self.marker_stamp = self.stamp()
        self.stage = None
        self.had_native = False

    def stamp(self):
        try:
            stat = self.marker.stat()
            return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def __call__(self):
        stamp = self.stamp()
        if stamp != self.marker_stamp:
            self.marker_stamp = stamp
            try:
                with self.marker.open('rb') as stream:
                    raw = stream.read(1048577)
                state = json.loads(raw) if len(raw) <= 1048576 else {}
                stage = state.get('stage') if isinstance(state, dict) else None
                if stage in SETUP_PHASES:
                    self.stage = {'phase': stage, 'label': SETUP_PHASES[stage], 'state': 'working'}
            except (OSError, ValueError):
                pass
        native = self.reader.poll()
        if native is not None and native.get('operation') in (None, 'prepare'):
            self.had_native = True
            return native
        if self.had_native:
            self.had_native = False
            return {'phase': 'checking-map', 'label': 'Checking saved map preparation', 'state': 'working'}
        return self.stage


@contextmanager
def map_progress(map_data, emit, interval=2.0):
    with action_progress(emit, 'checking-map', 'Checking saved map preparation',
                         interval=interval, source=MapProgressSource(map_data)) as progress:
        yield progress
