"""Single-instance, offline launcher for the installed mod's private relay.

Invoked by the mod with --parent-pid so its own helpers stop after game exit.
Nothing here launches, terminates or sends input to Dragonwilds.
"""
from pathlib import Path
import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
import traceback

from runtime_paths import load_paths
from skate_preflight import check_installation
from skate_process import ChildJob, ParentProcess, Singleton

APP = Path(__file__).resolve().parent


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def child():
    if sys.stdin.readline() != 'start\n':
        raise RuntimeError('Relay must be started by the mod launcher')
    import skate_relay
    skate_relay.run()
    return 0


def supervise(app=APP, parent_pid=None):
    with Singleton() as singleton:
        if not singleton.acquired:
            return 0
        paths = load_paths(app)
        mailbox = paths['mailbox']
        mailbox.mkdir(parents=True, exist_ok=True)
        logs = mailbox.parent / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        state = mailbox / 'launcher-status.json'
        stop = mailbox / 'relay-stop.request'
        stop.unlink(missing_ok=True)
        with contextlib.ExitStack() as resources:
            try:
                parent = resources.enter_context(ParentProcess(parent_pid)) if parent_pid else None
                atomic_json(state, {'schema': 1, 'status': 'checking', 'pid': os.getpid()})
                report = check_installation(app)
                if parent and parent.exited():
                    atomic_json(state, {'schema': 1, 'status': 'stopped', 'pid': os.getpid()})
                    return 0
                # Singleton is held and this launcher has no native children yet.
                # Dragonwilds itself may already be open. Cleanup failures must
                # never turn an otherwise valid installation into a launch error.
                try:
                    from skate_cache_retention import launch_maintenance
                    maintenance = launch_maintenance(paths)
                    atomic_json(logs / 'generated-cache-retention.json', maintenance)
                except Exception:
                    pass
                log = resources.enter_context((logs / 'relay.log').open('w', encoding='utf-8'))
                job = resources.enter_context(ChildJob())
                environment = dict(os.environ, S3_MANAGED_RELAY='1',
                    S3_PREPARED_WORLD_MANIFEST=report['map_manifest'], S3_PREPARED_RAIL_FILE=report['rail_file'])
                process = subprocess.Popen([sys.executable, '-u', str(Path(app) / 'skate_launcher.py'), '_child'],
                    cwd=app, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                    text=True, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    job.assign(process)
                    process.stdin.write('start\n')
                    process.stdin.flush()
                    process.stdin.close()
                except BaseException:
                    process.terminate()
                    process.wait(timeout=10)
                    raise
                atomic_json(state, {'schema': 1, 'status': 'running', 'pid': os.getpid(),
                                    'relay_pid': process.pid, 'release': report['release']})
                deadline = None
                while process.poll() is None:
                    if deadline is None and (stop.exists() or (parent and parent.exited())):
                        stop.write_text('stop\n', encoding='ascii')
                        deadline = time.monotonic() + 30
                        atomic_json(state, {'schema': 1, 'status': 'stopping', 'pid': os.getpid()})
                    if deadline is not None and time.monotonic() >= deadline:
                        job.close()
                        process.wait(timeout=10)
                        break
                    time.sleep(.1)
                code = process.returncode
                atomic_json(state, {'schema': 1, 'status': 'stopped' if deadline or code == 0 else 'error',
                                    'exit_code': code, 'pid': os.getpid()})
                return 0 if deadline else code
            except Exception as error:
                with (logs / 'launcher-error.log').open('w', encoding='utf-8') as log:
                    traceback.print_exc(file=log)
                atomic_json(state, {'schema': 1, 'status': 'error', 'error': str(error), 'pid': os.getpid()})
                return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', nargs='?', choices=['run', 'check', 'stop', '_child'], default='run')
    parser.add_argument('--parent-pid', type=int)
    args = parser.parse_args()
    if args.action == '_child':
        return child()
    if args.action == 'check':
        print(json.dumps(check_installation(APP), indent=2))
        return 0
    if args.action == 'stop':
        mailbox = load_paths(APP)['mailbox']
        mailbox.mkdir(parents=True, exist_ok=True)
        (mailbox / 'relay-stop.request').write_text('stop\n', encoding='ascii')
        return 0
    return supervise(parent_pid=args.parent_pid)


if __name__ == '__main__':
    raise SystemExit(main())
