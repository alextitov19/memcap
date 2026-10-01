"""Temporary owner-enabled command evidence. Local files only; no telemetry transport."""
import argparse
from collections import deque
from contextlib import contextmanager
import fcntl
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import time

SEGMENT_BYTES = 8 * 1024 * 1024
RECORD_BYTES = 256 * 1024
RETENTION_SECONDS = 86400
FILES = ('commands.2.jsonl', 'commands.1.jsonl', 'commands.jsonl')


def root():
    return Path(os.environ.get('MEMCAP_STATE_HOME', str(Path.home() / '.local/state'))) / 'memcap/command-trace'


def private_directory(path, create=False):
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError('command trace directory must be private and owned by this user')


def open_private(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise OSError('command trace file must be private and owned by this user')
    return fd


@contextmanager
def locked(path):
    fd = open_private(path / 'trace.lock', os.O_CREAT | os.O_RDWR)
    try:
        # A diagnostic must never stall admission behind its own log writer.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def deadline(path):
    try:
        with os.fdopen(open_private(path / 'enabled.json', os.O_RDONLY)) as stream:
            value = json.loads(stream.read(4096)).get('until', 0)
        return value if type(value) in (int, float) else 0
    except (OSError, ValueError, AttributeError):
        return 0


def prune(path, now):
    for name in FILES:
        file = path / name
        try:
            if now - file.lstat().st_mtime > RETENTION_SECONDS:
                file.unlink()
        except FileNotFoundError:
            pass


def record(event, **fields):
    """Best effort. Never changes permissions, admission, leases or command status."""
    try:
        path = root()
        private_directory(path)
        now = time.time()
        if deadline(path) <= now:
            return
        row = dict(event=event, at=now, **fields)
        payload = (json.dumps(row, ensure_ascii=True) + '\n').encode()
        if len(payload) > RECORD_BYTES:
            # Keep a valid, explicit partial record rather than silently losing it.
            row = dict(event=event, at=now, truncated=True,
                       command=str(fields.get('command', fields.get('argv', '')))[:16000],
                       job=fields.get('job', ''))
            payload = (json.dumps(row, ensure_ascii=True) + '\n').encode()
        with locked(path):
            if deadline(path) <= time.time():
                return
            prune(path, now)
            current = path / FILES[-1]
            if current.exists() and current.lstat().st_size + len(payload) > SEGMENT_BYTES:
                (path / FILES[0]).unlink(missing_ok=True)
                if (path / FILES[1]).exists():
                    os.replace(path / FILES[1], path / FILES[0])
                os.replace(current, path / FILES[1])
            with os.fdopen(open_private(current, os.O_CREAT | os.O_WRONLY | os.O_APPEND), 'ab') as stream:
                stream.write(payload)
    except (OSError, ValueError, TypeError):
        pass


def hook(payload, route=None):
    if not isinstance(payload, dict):
        return
    if payload.get('hook_event_name') != 'PreToolUse':
        return
    tool = payload.get('tool_name')
    if tool not in {'Bash', 'exec_command', 'shell_command'}:
        return
    args = payload.get('tool_input', {})
    command = args.get('command', args.get('cmd')) if isinstance(args, dict) else None
    if isinstance(command, str):
        record('route' if route else 'command', command=command, tool=tool,
               route=route, cwd=payload.get('cwd'), session=payload.get('session_id'),
               subagent=payload.get('agent_id'), operation=payload.get('tool_use_id'))


def job(directory, event, **fields):
    # Synthetic scheduler fixtures must never write into an enabled live trace.
    if Path(directory) == root().parent / 'queue':
        record(event, **fields)


def snapshot_pending():
    """Capture existing supervisors read-only; never adopt or modify their jobs."""
    try:
        from boot_timeout import table_now
        jobs = json.loads((root().parent / 'queue/jobs.json').read_text())['jobs']
        table = table_now()
        for entry in jobs:
            row = table.get(str(entry.get('owner')), {})
            if (row.get('uid') == os.getuid() and row.get('start') == entry.get('owner_start')
                    and row.get('command')):
                record('existing-supervisor', job=entry['id'], command=row['command'],
                       cwd=entry.get('cwd'), status=entry.get('status'),
                       admission=entry.get('admission'), enqueued=entry.get('enqueued'))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
        pass


def main():
    parser = argparse.ArgumentParser(prog='memcap trace')
    parser.add_argument('action', choices=('on', 'off', 'status', 'show', 'clear'))
    parser.add_argument('job', nargs='?')
    args = parser.parse_args()
    if args.job and (args.action != 'show' or not re.fullmatch(r'[a-f0-9]{8,32}', args.job)):
        parser.error('a job ID is accepted only with show and must contain 8 to 32 hex characters')
    path = root()
    if not path.exists() and args.action != 'on':
        print('memcap: command tracing is off; no local trace')
        return
    private_directory(path, create=args.action == 'on')
    with locked(path):
        prune(path, time.time())
        if args.action == 'on':
            with os.fdopen(open_private(path / 'enabled.json', os.O_CREAT | os.O_WRONLY | os.O_TRUNC), 'w') as stream:
                json.dump(dict(until=time.time() + RETENTION_SECONDS), stream)
            print(f'memcap: local command tracing enabled for 24 hours: {path}')
        elif args.action in {'off', 'clear'}:
            (path / 'enabled.json').unlink(missing_ok=True)
            if args.action == 'clear':
                for name in FILES:
                    (path / name).unlink(missing_ok=True)
            print('memcap: command tracing disabled' + (' and local records deleted' if args.action == 'clear' else ''))
        elif args.action == 'show':
            lines = deque(maxlen=200)
            for name in FILES:
                try:
                    with os.fdopen(open_private(path / name, os.O_RDONLY)) as stream:
                        for line in stream:
                            if args.job:
                                try:
                                    if not str(json.loads(line).get('job', '')).startswith(args.job):
                                        continue
                                except (ValueError, AttributeError):
                                    continue
                            lines.append(line)
                except FileNotFoundError:
                    pass
            print(''.join(lines), end='')
        else:
            print(json.dumps(dict(enabled=deadline(path) > time.time(), expires_at=deadline(path),
                                  directory=str(path), max_bytes=3 * SEGMENT_BYTES)))
    if args.action == 'on':
        snapshot_pending()


if __name__ == '__main__':
    main()
