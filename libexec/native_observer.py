"""Observe native work without admission, reservations or signal authority.

The launch shim replaces itself with the original shell. The existing analytics
collector samples identities and physical footprint; no per-command monitor is
left running. Observed peaks are lower bounds and can only promote later work.
"""
import argparse
import ctypes
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid

THRESHOLD_KB = 512 * 1024
MAX_ACTIVE = 256
MAX_PROFILES = 2048


def root():
    return Path(os.environ.get('MEMCAP_STATE_HOME', str(Path.home() / '.local/state'))) / 'memcap/native'


def read_json(path, limit=2 * 1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > limit:
            raise ValueError('unsafe native observation file')
        return json.loads(os.read(fd, limit + 1))
    finally:
        os.close(fd)


def private(path):
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('unsafe native observation directory')


def write_json(path, value):
    private(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='.native-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, separators=(',', ':'))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Usage(ctypes.Structure):
    # macOS SDK sys/resource.h rusage_info_v2. Bytes, not ps RSS.
    _fields_ = [('uuid', ctypes.c_ubyte * 16)] + [(name, ctypes.c_uint64) for name in (
        'user_time', 'system_time', 'pkg_idle_wkups', 'interrupt_wkups', 'pageins',
        'wired_size', 'resident_size', 'phys_footprint', 'proc_start_abstime',
        'proc_exit_abstime', 'child_user_time', 'child_system_time',
        'child_pkg_idle_wkups', 'child_interrupt_wkups', 'child_pageins',
        'child_elapsed_abstime', 'diskio_bytesread', 'diskio_byteswritten')]


@functools.lru_cache(maxsize=1)
def library():
    if sys.platform != 'darwin':
        return None
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    lib.proc_pid_rusage.restype = ctypes.c_int
    return lib


def usage(pid):
    lib = library()
    if lib is None:
        return None
    result = Usage()
    if lib.proc_pid_rusage(int(pid), 2, ctypes.byref(result)) != 0:
        return None
    return dict(identity=int(result.proc_start_abstime), footprint_kb=int(result.phys_footprint) // 1024)


def available():
    try:
        from analytics_events import root as analytics_root
        directory = analytics_root()
        heartbeat = read_json(directory / 'heartbeat.json', 65536)
        return (directory / 'enabled').is_file() and 0 <= time.time() - heartbeat['at'] < 15
    except (OSError, ValueError, KeyError, TypeError):
        return False


def learned(fingerprint):
    try:
        value = read_json(root() / 'profiles.json').get(fingerprint, {})
        peak = value.get('peak_kb')
        return type(peak) is int and peak >= THRESHOLD_KB and 0 <= time.time() - value.get('at', 0) < 30 * 86400
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def register(fingerprint, session=''):
    try:
        if not available():
            return
        measured = usage(os.getpid())
        if not measured:
            return
        directory = root() / 'active'
        private(root())
        private(directory)
        if len(list(directory.glob('*.json'))) >= MAX_ACTIVE:
            return
        write_json(directory / (uuid.uuid4().hex + '.json'), dict(pid=os.getpid(),
            identity=measured['identity'], fingerprint=fingerprint, created=time.time(),
            session=hashlib.sha256(session.encode()).hexdigest() if session else ''))
    except (OSError, ValueError, TypeError):
        pass


def process_table():
    result = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,uid='], capture_output=True,
                            text=True, timeout=1)
    if result.returncode:
        raise ValueError('native observation process table unavailable')
    return {int(p): dict(ppid=int(parent), uid=int(uid)) for p, parent, uid in
            (line.split() for line in result.stdout.splitlines())}


class Observer:
    def __init__(self, directory=None, probe=usage, table=process_table):
        self.directory = Path(directory) if directory else root()
        self.probe, self.table = probe, table
        self.states = {}
        self.last = 0

    def tick(self, now=None):
        now = time.time() if now is None else now
        if now - self.last < 2:
            return []
        self.last = now
        files = list((self.directory / 'active').glob('*.json'))[:MAX_ACTIVE]
        if not files:
            self.states.clear()
            return []
        self.states = {name: state for name, state in self.states.items() if name in {p.name for p in files}}
        entries = []
        for path in files:
            try:
                entry = read_json(path, 4096)
                if (type(entry.get('pid')) is int and entry['pid'] > 0
                        and type(entry.get('identity')) is int and entry['identity'] > 0
                        and isinstance(entry.get('fingerprint'), str)
                        and re.fullmatch('[a-f0-9]{64}', entry['fingerprint'])
                        and type(entry.get('created')) in (int, float) and 0 <= entry['created'] <= now):
                    entries.append((path, entry))
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        if not entries:
            return []
        try:
            table = self.table()
            children = {}
            for pid, row in table.items():
                if row['uid'] == os.getuid():
                    children.setdefault(row['ppid'], []).append(pid)
            pending = [entry['pid'] for _, entry in entries]
            pending += [pid for state in self.states.values() for pid in state['members']]
            targets = set()
            while pending and len(targets) <= 8192:
                pid = pending.pop()
                if pid in targets or table.get(pid, {}).get('uid') != os.getuid():
                    continue
                targets.add(pid)
                pending.extend(children.get(pid, []))
            # Bounded shared observation. Never spawn a process-table probe per
            # native task, or let profiling a large tree consume the collector.
            if len(targets) > 8192:
                return []
            measurements, deadline = {}, time.monotonic() + .1
            for pid in targets:
                if time.monotonic() > deadline:
                    return []
                measurements[pid] = self.probe(pid)
            after = self.table()
        except (OSError, ValueError, subprocess.SubprocessError):
            return []
        events, changed = [], False
        try:
            profiles = read_json(self.directory / 'profiles.json')
            if not isinstance(profiles, dict):
                profiles = {}
        except (OSError, ValueError):
            profiles = {}
        profiles = {key: value for key, value in profiles.items()
                    if isinstance(key, str) and re.fullmatch('[a-f0-9]{64}', key)
                    and isinstance(value, dict) and type(value.get('peak_kb')) is int
                    and value['peak_kb'] >= 0 and type(value.get('at')) in (int, float)
                    and 0 <= value['at'] <= now}
        for path, entry in entries:
            try:
                pid, identity, fingerprint = entry['pid'], entry['identity'], entry['fingerprint']
                if type(pid) is not int or type(identity) is not int or not re.fullmatch('[a-f0-9]{64}', fingerprint):
                    continue
                state = self.states.setdefault(path.name, dict(members={}, peak=0, samples=0))
                row = table.get(pid)
                current = measurements.get(pid) if row and row['uid'] == os.getuid() else None
                unresolved = bool(row and row['uid'] == os.getuid() and not current)
                live_root = current and current['identity'] == identity
                candidates = {pid} if live_root else set()
                if live_root:
                    for _ in range(128):
                        more = {p for p, r in table.items() if r['uid'] == os.getuid() and r['ppid'] in candidates}
                        if more <= candidates:
                            break
                        candidates |= more
                    if len(candidates) > 512:
                        continue
                total, members, missed = 0, {}, {}
                for child in candidates | set(state['members']):
                    if table.get(child, {}).get('uid') != os.getuid():
                        continue
                    value = measurements.get(child)
                    if not value:
                        unresolved = True
                        if child in state['members']:
                            missed[child] = state['members'][child]
                        continue
                    previous = state['members'].get(child)
                    if previous is not None and previous != value['identity']:
                        continue
                    if child == pid and value['identity'] != identity:
                        continue
                    members[child] = value['identity']
                    total += value['footprint_kb']
                # A root identity change invalidates discovery of new descendants.
                check = self.probe(pid) if live_root else None
                if live_root and (not check or check['identity'] != identity):
                    continue
                # Recheck discovery ancestry after sampling. The table has no
                # identity stamps, so a reused PID must not attach a new tree.
                if any(after.get(p) != table.get(p) for p in candidates):
                    continue
                if any(not (value := self.probe(p)) or value['identity'] != stamp for p, stamp in members.items()):
                    continue
                state['members'] = {**members, **missed}
                if members:
                    state['samples'] += 1
                    state['peak'] = max(state['peak'], total)
                    if total >= THRESHOLD_KB:
                        prior = profiles.get(fingerprint, {})
                        profiles[fingerprint] = dict(peak_kb=max(total, prior.get('peak_kb', 0)), at=now)
                        changed = True
                if (not members and not unresolved) or now - entry['created'] > 86400:
                    observation = dict(workload=fingerprint, session=entry.get('session', ''),
                        runtime_ms=max(0, now - entry['created']) * 1000,
                        count=state['samples'], measurement_complete=0,
                        outcome='unknown')
                    if state['samples']:
                        observation['peak_kb'] = state['peak']
                    events.append(observation)
                    # Only an observation marker. Never a scheduler lease or job.
                    path.unlink(missing_ok=True)
                    self.states.pop(path.name, None)
            except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
                continue
        if changed:
            profiles = dict(sorted(profiles.items(), key=lambda kv: kv[1].get('at', 0), reverse=True)[:MAX_PROFILES])
            try:
                write_json(self.directory / 'profiles.json', profiles)
            except (OSError, ValueError):
                pass
        return events


def main():
    parser = argparse.ArgumentParser(prog='memcap _native')
    parser.add_argument('--fingerprint', required=True)
    parser.add_argument('--session-key', default='')
    parser.add_argument('--cwd')
    parser.add_argument('--shell', default='/bin/bash')
    parser.add_argument('--login', action='store_true')
    parser.add_argument('--shell-command', required=True)
    args = parser.parse_args()
    if not re.fullmatch('[a-f0-9]{64}', args.fingerprint):
        parser.error('invalid observation fingerprint')
    if args.cwd:
        os.chdir(args.cwd)
    from demand_policy import classify
    decision = classify(args.shell_command, os.getcwd())
    if decision.kind == 'heavy':
        executable = str(Path(__file__).resolve().parents[1] / 'bin/memcap')
        argv = [executable, 'run', '--wait', '86400', '--session-key', args.session_key, '--shell', args.shell]
        if args.login:
            argv.append('--login')
        argv += ['--shell-command', args.shell_command]
        os.execvpe(argv[0], argv, os.environ)
    register(decision.fingerprint, args.session_key)
    argv = [args.shell, '-lc' if args.login else '-c', args.shell_command]
    os.execvpe(argv[0], argv, os.environ)


if __name__ == '__main__':
    main()
