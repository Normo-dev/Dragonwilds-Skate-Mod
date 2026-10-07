"""Bounded, read-only cache progress. Completion comes from the real protocol."""
from pathlib import Path
import math
import re

LABELS = {
    'collision_load': 'Loading world collision',
    'preparing_exposed_cells': 'Generating grind edges',
    'loading_cached_edges': 'Loading saved grind edges',
    'joining_grind_paths': 'Joining connected grind paths',
    'loading_cached_grind_data': 'Loading saved grind paths',
    'saving_grind_data': 'Saving grind cache',
    'building_runtime_provider': 'Preparing skating collision',
    'preparing_legacy_grinds': 'Preparing initial grind cache',
    'ready': 'Finishing cache preparation',
}
COUNTERS = re.compile(r'\b(total|cells|rebuilt|reused|segments|elapsed_ms)=(\d+)\b')
KIND = re.compile(r'(?:^|\s)kind=(\S*)')
PHASE = re.compile(r'^DWS_CACHE_PHASE operation=(prepare|init|scene) phase=([a-z_]+) '
                   r'state=(start|end|error) elapsed_ms=(\d+)$')


def format_elapsed(seconds):
    if type(seconds) not in (int, float) or not math.isfinite(seconds):
        seconds = 0
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{hours}h {minutes:02d}m {seconds:02d}s' if hours else f'{minutes}m {seconds:02d}s'


def parse_line(line):
    phase = PHASE.fullmatch(line.strip())
    if phase:
        operation, name, state, elapsed = phase.groups()
        if name not in LABELS:
            return None
        return {'phase': name, 'label': LABELS[name], 'operation': operation,
                'state': state, 'phase_elapsed_ms': int(elapsed)}
    if not line.startswith('WORLD_EXPOSED_PROGRESS '):
        return None
    name = 'preparing_exposed_cells'
    if 'kind=' in line:
        kinds = KIND.findall(line)
        if kinds != ['load']:
            return None
        name = 'loading_cached_edges'
    pairs = COUNTERS.findall(line)
    fields = {key: int(value) for key, value in pairs}
    if (len(pairs) != len(fields) or not {'cells', 'rebuilt', 'reused'} <= fields.keys()
            or any(v > 2**63 - 1 for v in fields.values())):
        return None
    count, total = fields['cells'], fields.get('total')
    if count != fields['rebuilt'] + fields['reused'] or total is not None and count > total:
        return None
    result = {'phase': name, 'label': LABELS[name],
              'state': 'working', 'completed': count, 'reused': fields['reused']}
    if total is not None and total > 0:
        percent=round(100 * count / total, 1) if count==total else min(99.9,round(100 * count / total,1))
        result.update(total=total, percent=percent)
    return result


class ProgressReader:
    def __init__(self, path, start_at_end=True):
        self.path = Path(path)
        self.identity = None
        self.offset = 0
        self.partial = b''
        self.latest = None
        try:
            stat = self.path.stat()
            self.identity = (stat.st_dev, stat.st_ino)
            self.offset = stat.st_size if start_at_end else 0
        except OSError:
            pass

    def poll(self):
        try:
            with self.path.open('rb') as stream:
                import os
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if identity != self.identity or stat.st_size < self.offset:
                    self.identity = identity
                    self.offset = 0
                    self.partial = b''
                    self.latest = None
                skipped = stat.st_size - self.offset > 65536
                if skipped:
                    self.offset = stat.st_size - 65536
                    self.partial = b''
                stream.seek(self.offset)
                raw = stream.read(65536)
                self.offset = stream.tell()
            lines = (self.partial + raw).split(b'\n')
            self.partial = lines.pop()
            if len(self.partial) > 8192:
                self.partial = b''
            if skipped and lines:
                lines = lines[1:]
            for raw_line in lines:
                value = parse_line(raw_line.decode('utf-8', errors='replace').strip())
                if value is not None:
                    self.latest = value
        except FileNotFoundError:
            self.identity = None
            self.offset = 0
            self.partial = b''
            self.latest = None
        except (OSError, ValueError, OverflowError):
            pass
        return dict(self.latest) if self.latest else None


def describe(value):
    if not value:
        return None
    text = value['label']
    if type(value.get('total')) is int and value['total'] > 0:
        text += f" — {value['completed']:,} / {value['total']:,} sections ({value['percent']:g}%)"
    elif type(value.get('completed')) is int:
        text += f" — {value['completed']:,} sections"
    return text
