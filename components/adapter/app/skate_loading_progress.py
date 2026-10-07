"""Read-only progress for one generation/revision and serialized native replies."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import math
import time

from skate_progress import LABELS, ProgressReader


class LoadingProgress:
    def __init__(self, log_path, *, clock=time.monotonic):
        self.log_path = log_path
        self.clock = clock
        self.context = None
        self.reader = None
        self.last_poll = -math.inf

    def begin(self, generation, revision, *, prewarm=False):
        if not isinstance(generation, str) or not generation or len(generation) > 160:
            raise ValueError('Invalid progress generation')
        if type(revision) is not int or not 0 <= revision <= 2**53 - 1:
            raise ValueError('Invalid progress revision')
        self.context = {'generation': generation, 'revision': revision, 'started': self.clock(),
                        'operation': 'cache', 'phase': 'checking_saved_world' if prewarm else 'preparing_scene',
                        'label': 'Checking saved world collision' if prewarm else 'Preparing object collision',
                        'state': 'working'}
        self.reader = None
        self.last_poll = -math.inf

    def native(self, command):
        if self.context is None:
            return
        operation = {'init_world': 'init', 'init': 'init', 'collision': 'init',
                     'prepare_world': 'prepare', 'world_scene': 'scene', 'queue_collision': 'scene'}.get(command)
        if operation is None:
            return
        self.context.update(operation=operation, phase='collision_load',
                            label=LABELS['collision_load'], state='working')
        for key in ('completed', 'total', 'percent'):
            self.context.pop(key, None)
        # Position the tail before writing the new native request; never reuse
        # the preceding operation's count when its log does not advance.
        self.reader = ProgressReader(self.log_path, start_at_end=True)
        self.last_poll = -math.inf

    def finish(self):
        self.context = None
        self.reader = None
        self.last_poll = -math.inf

    def snapshot(self):
        value = self.context
        if value is None:
            return None
        now = self.clock()
        if self.reader is not None and now - self.last_poll >= .5:
            self.last_poll = now
            identity, offset = self.reader.identity, self.reader.offset
            record = self.reader.poll()
            if self.reader.identity != identity or self.reader.offset < offset:
                value.update(phase='collision_load', label=LABELS['collision_load'])
                for key in ('completed', 'total', 'percent'):
                    value.pop(key, None)
            if record and record.get('operation', value['operation']) == value['operation']:
                phase = record.get('phase')
                if phase in LABELS:
                    # Copy only validated scalar fields; log text/paths are
                    # never sent to the host or interpreted as instructions.
                    value.update(phase=phase, label=LABELS[phase], state='working')
                    for key in ('completed', 'total', 'percent'):
                        value.pop(key, None)
                    completed, total = record.get('completed'), record.get('total')
                    if type(completed) is int and 0 <= completed <= 2**53 - 1:
                        value['completed'] = completed
                        if type(total) is int and 0 < total <= 2**53 - 1 and completed <= total:
                            value['total'] = total
                            value['percent'] = 100 if completed == total else min(99.9, round(100 * completed / total, 1))
        return {key: val for key, val in value.items() if key != 'started'} | {
            'elapsed_seconds': max(0, int(now - value['started']))}


class NativeReplyReader:
    """Only long synchronous loading replies use a reader thread.

    The caller alone writes native commands and runs progress callbacks. No
    callback may send another command. Pose/tick and all ordinary replies are
    read directly, preserving their existing request path.
    """
    SLOW = frozenset(('init_world', 'init', 'prepare_world', 'collision'))

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='native-loading-reply')
        self.pending = None

    def read(self, stream, operation, progress):
        if self.pending is not None:
            raise RuntimeError('Native reply already pending')
        if operation not in self.SLOW:
            return stream.readline()
        future = self.executor.submit(stream.readline)
        self.pending = future
        try:
            while True:
                try:
                    return future.result(timeout=.25)
                except TimeoutError:
                    if future.done():
                        return future.result()  # Propagate a reader's own exception.
                    progress()
        finally:
            if future.done():
                self.pending = None

    def close(self):
        # The owner first exits/terminates the process, unblocking any pipe
        # reader left by a stop/error. It must not enqueue a second command.
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.pending = None
