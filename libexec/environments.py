"""Disposable Compose environments: reserve before startup, release after work.

Only uniquely named environments created by this runner are eligible. Existing
stacks are never adopted from cwd/name guesses. Python never stops containers:
the shell enforcement choke point authorizes and performs that operation.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r'memcap-[a-f0-9]{32}')


def state_root():
    return Path(os.environ.get('MEMCAP_STATE_HOME', str(Path.home() / '.local/state'))) / 'memcap'


@contextmanager
def registry(state):
    directory = state / 'environments'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise ValueError('environment registry must be private and owned')
    fd = os.open(directory / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = directory / 'resources.json'
        if path.is_symlink():
            raise ValueError('unsafe environment registry')
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError('invalid environment registry')
        yield data
        from integrate import atomic_write
        atomic_write(path, (json.dumps(data) + '\n').encode(), 0o600)
    finally:
        os.close(fd)


def docker_json(args, engine=None):
    prefix = ['docker', '--host', engine] if engine else ['docker']
    result = subprocess.run([*prefix, *args], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError('Docker inspection unavailable; retain environment')
    return json.loads(result.stdout)


def local_engine():
    if os.environ.get('DOCKER_HOST') or os.environ.get('DOCKER_CONTEXT'):
        raise ValueError('explicit Docker endpoints are not supported for disposable environments')
    contexts = docker_json(['context', 'inspect'])
    host = contexts[0]['Endpoints']['docker']['Host']
    if not host.startswith('unix:///') or any(c in host for c in '\n\r\0'):
        raise ValueError('disposable environments require a local Docker socket')
    return host


def containers(token, engine=None):
    if not TOKEN.fullmatch(token):
        raise ValueError('invalid environment identity')
    prefix = ['docker', '--host', engine] if engine else ['docker']
    result = subprocess.run([*prefix, 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + token],
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError('container identity unavailable')
    ids = result.stdout.split()
    if any(not re.fullmatch('[a-f0-9]{12,64}', value) for value in ids) or len(ids) > 64:
        raise ValueError('invalid container identities')
    return docker_json(['inspect', *ids], engine) if ids else []


def identities(rows, token):
    result = {}
    for row in rows:
        labels = row.get('Config', {}).get('Labels') or {}
        ident = row.get('Id', '')
        if (not re.fullmatch('[a-f0-9]{64}', ident) or labels.get('com.docker.compose.project') != token
                or not row.get('Created') or row.get('HostConfig', {}).get('AutoRemove')):
            raise ValueError('unverified disposable container')
        result[ident] = row['Created']
    return result


def alive(identity, table):
    pid, start = identity.get('pid'), identity.get('start')
    return bool(pid and start and str(pid) in table and table[str(pid)]['start'] == start
                and table[str(pid)]['uid'] == os.getuid())


def eligible(row, table, now, caller=None):
    if row.get('pinned') or any(alive(c, table) for c in row.get('claims', [])):
        return False
    if row.get('phase') == 'releasing' and caller == row.get('owner', {}).get('pid') and alive(row['owner'], table):
        return True
    group_busy = bool(row.get('group')) and any(r.get('group') == row['group'] and r.get('uid') == os.getuid() for r in table.values())
    finished = row.get('phase') == 'releasing'
    return (not alive(row.get('owner', {}), table) and (finished or not alive(row.get('agent', {}), table)) and not group_busy
            and bool(row.get('agent', {}).get('start')) and row.get('orphan_since') is not None
            and 120 <= now - row['orphan_since'] and 0 <= now - row.get('observed', 0) <= 90)


def authorize(token, caller=None):
    from scheduler import processes
    state = state_root()
    if (state / 'paused').exists():
        return []
    table, engine = processes(), local_engine()
    with registry(state) as data:
        row = data.get(token)
        if not row or row.get('engine') != engine or not eligible(row, table, time.time(), caller):
            return []
        observed = containers(token, engine)
        actual = identities(observed, token)
        # During a failed startup the exclusive random Compose project is still
        # ours; only the live releasing worker may enroll newly created members.
        if caller == row.get('owner', {}).get('pid') and row.get('phase') == 'releasing':
            row['containers'] = actual
        if actual != row.get('containers'):
            return []
        selected = sorted(r['Id'] for r in observed if r.get('State', {}).get('Running'))
        if selected and os.environ.get('MC_DRY_RUN') != '1':
            # Claiming a stack after disposal has begun cannot save a container
            # whose stop was already authorized. New claims must wait/refuse.
            row['stop_requested'] = True
        return selected


def scan():
    from scheduler import processes
    state, now = state_root(), time.time()
    if (state / 'paused').exists() or not (state / 'environments/resources.json').exists():
        return []
    table = processes()
    ready = []
    with registry(state) as data:
        for token, row in list(data.items()):
            if row.get('phase') == 'stopped':
                continue
            group_busy = bool(row.get('group')) and any(r.get('group') == row['group'] and r.get('uid') == os.getuid() for r in table.values())
            if alive(row.get('owner', {}), table) or (row.get('phase') != 'releasing' and alive(row.get('agent', {}), table)) or group_busy:
                row.pop('orphan_since', None)
            else:
                if now - row.get('observed', 0) > 90:
                    row.pop('orphan_since', None)
                row.setdefault('orphan_since', now)
            row['observed'] = now
            if eligible(row, table, now):
                ready.append(token)
    return ready


def adequate_budget(data, lease, required):
    return any(j.get('id') == lease and j.get('status') == 'running'
               and j.get('memory_kb', 0) >= required for j in data.get('jobs', []))


def worker(compose, command, token, runner=None, required_memory=None):
    from scheduler import processes, Scheduler
    from orphan_recovery import agent_identity
    state, table = state_root(), processes()
    own = table.get(str(os.getpid()))
    if not own or not TOKEN.fullmatch(token):
        raise ValueError('environment worker requires an admitted managed job')
    queue = Scheduler(state / 'queue')
    with queue.locked() as data:
        if not queue.nested(data, table):
            raise ValueError('environment worker requires a verified live managed lease')
        if required_memory is not None and not adequate_budget(data, os.environ.get('MEMCAP_QUEUE_LEASE'), required_memory):
            raise ValueError('parent admission is smaller than the combined environment budget; submit environment run as a standalone command')
    if os.environ.get('MC_DRY_RUN') == '1' and runner is None:
        raise ValueError('dry run: disposable environment startup withheld')
    run = subprocess.run if runner is None else runner
    engine = local_engine()
    if containers(token, engine):
        raise ValueError('environment project already exists')
    origin = agent_identity()
    env = {**os.environ, 'COMPOSE_PROJECT_NAME': token, 'MEMCAP_ENVIRONMENT': token, 'DOCKER_HOST': engine}
    prefix = ['docker', '--host', engine, 'compose', '-p', token, '-f', compose]
    with registry(state) as data:
        # Completed records are history, not live ownership. Keep a bounded
        # tail while never discarding active/pinned/claimed environments.
        old = [k for k, row in data.items() if row.get('phase') == 'stopped' and not row.get('pinned') and not row.get('claims')]
        for key in old[:-64]:
            del data[key]
        if len(data) >= 256:
            raise ValueError('environment registry full; inspect retained resources')
        data[token] = dict(owner=dict(pid=os.getpid(), start=own['start']), group=own['group'],
                           agent=dict(pid=origin.get('agent_owner'), start=origin.get('agent_start')),
                           engine=engine, containers={}, phase='starting', created=time.time(), claims=[])
    try:
        # Images must be prepared separately. This admission covers stack startup
        # plus tests, not an unbounded implicit image build or pull.
        result = run([*prefix, 'up', '--no-start', '--no-build', '--pull', 'never'], env=env)
        with registry(state) as data:
            data[token]['containers'] = identities(containers(token, engine), token)
            data[token]['phase'] = 'prepared'
        if result.returncode:
            return result.returncode
        # Persist immutable IDs before anything consumes stack memory, so a
        # crash during startup still leaves recoverable ownership evidence.
        result = run([*prefix, 'start', '--wait', '--wait-timeout', '180'], env=env)
        if result.returncode:
            return result.returncode
        with registry(state) as data:
            if identities(containers(token, engine), token) != data[token]['containers']:
                raise ValueError('environment membership changed during startup')
            data[token]['phase'] = 'active'
        return run(command, env=env).returncode
    finally:
        with registry(state) as data:
            data[token]['phase'] = 'releasing'
        result = run(['/bin/bash', str(ROOT / 'bin/memcap'), '_environment-stop', token])
        if result.returncode:
            with registry(state) as data:
                stopped = data[token].get('phase') == 'stopped'
            if not stopped:
                print('memcap: environment cleanup retained; inspect memcap environment list. No volumes were deleted.', file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='memcap environment')
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('run', help='reserve the entire disposable Compose stack and workload before starting either')
    p.add_argument('--memory', type=float, required=True, help='GiB for stack plus workload together')
    p.add_argument('--compose', type=Path, required=True)
    p.add_argument('--wait', type=float, default=86400)
    p.add_argument('--session-key', default='', help=argparse.SUPPRESS)
    p.add_argument('command', nargs=argparse.REMAINDER)
    p = sub.add_parser('_worker')
    p.add_argument('compose')
    p.add_argument('token')
    p.add_argument('memory_gb', type=float)
    p.add_argument('command', nargs=argparse.REMAINDER)
    sub.add_parser('list')
    sub.add_parser('_scan')
    p = sub.add_parser('_authorize')
    p.add_argument('token')
    p.add_argument('--caller', type=int)
    p = sub.add_parser('_finish')
    p.add_argument('token')
    p = sub.add_parser('_endpoint')
    p.add_argument('token')
    for action in ('pin', 'unpin', 'claim', 'release'):
        p = sub.add_parser(action)
        p.add_argument('token')
    args = parser.parse_args(argv)
    if args.action == 'run':
        import math
        if not math.isfinite(args.memory) or args.memory <= 0 or not math.isfinite(args.wait) or args.wait < 0:
            parser.error('memory must be positive; wait must be nonnegative')
        compose = args.compose.resolve(strict=True)
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        if not command:
            parser.error('workload required after --')
        token = 'memcap-' + uuid.uuid4().hex
        # Enter the normal runner before inspecting/starting Docker. Explicit
        # memory keeps strict and adaptive admission from shrinking this budget.
        os.execv('/bin/bash', ['/bin/bash', str(ROOT / 'bin/memcap'), 'run', '--memory', str(args.memory), '--wait', str(args.wait), '--session-key', args.session_key, '--',
                   sys.executable, str(Path(__file__).resolve()), '_worker', str(compose), token, str(args.memory), *command])
    if args.action == '_worker':
        import math
        if not math.isfinite(args.memory_gb) or args.memory_gb <= 0:
            parser.error('positive environment memory required')
        return worker(args.compose, args.command, args.token, required_memory=int(args.memory_gb * 1048576))
    if args.action == '_authorize':
        print(' '.join(authorize(args.token, args.caller)))
    elif args.action == '_scan':
        print('\n'.join(scan()))
    elif args.action == '_finish':
        with registry(state_root()) as data:
            row = data[args.token]
            if local_engine() != row['engine']:
                raise ValueError('Docker endpoint changed; retaining resource')
            observed = containers(args.token, row['engine'])
            if identities(observed, args.token) != row['containers'] or any(c.get('State', {}).get('Running') for c in observed):
                raise ValueError('environment still running or identity changed')
            row['phase'] = 'stopped'
    elif args.action == '_endpoint':
        with registry(state_root()) as data:
            endpoint = data[args.token]['engine']
            if endpoint != local_engine():
                raise ValueError('Docker endpoint changed')
            print(endpoint)
    elif args.action == 'list':
        path = state_root() / 'environments/resources.json'
        print(path.read_text() if path.exists() else '{}')
    else:
        from orphan_recovery import agent_identity
        identity = agent_identity()
        claim = dict(pid=identity.get('agent_owner'), start=identity.get('agent_start'))
        if not claim['start']:
            raise ValueError('cannot identify claiming agent')
        with registry(state_root()) as data:
            row = data[args.token]
            if args.action in {'pin', 'claim'} and (row.get('stop_requested') or row.get('phase') == 'stopped'):
                raise ValueError('environment disposal already began; do not use or claim this stack')
            if args.action in {'pin', 'unpin'}:
                if row.get('agent') != claim:
                    raise ValueError('only originating agent may change pin')
                row['pinned'] = args.action == 'pin'
            elif args.action == 'claim':
                row['claims'] = [c for c in row['claims'] if c != claim] + [claim]
            else:
                row['claims'] = [c for c in row['claims'] if c != claim]
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f'memcap environment: {exc}', file=sys.stderr)
        sys.exit(75)
