"""Wrapped dev-server lifetime and simulator status classification.

Synthetic commands, fake executables and private temporary queue state only.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'libexec'))
from demand_policy import classify
from scheduler_policy import hook_response, persistent_shell

LIVE = ('memcap run --wait 86400 -- ../../node_modules/.bin/expo start '
        '--dev-client --port 8082 > log &')


def healthy():
    return {"cap_kb": 16 * 1048576, "tracked_kb": 0, "available_kb": 16 * 1048576,
            "pressure": 1, "fault": False, "footprints": {}, "tracked_pids": []}


class Incidents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, 'HOME': str(self.root), 'MC_DRY_RUN': '1',
                    'MEMCAP_CONFIG_HOME': str(self.root / 'config'),
                    'MEMCAP_STATE_HOME': str(self.root / 'state')}
        self.env.pop('MEMCAP_QUEUE_LEASE', None)
        env = patch.dict(os.environ, self.env, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def hook_args(self, command):
        payload = dict(hook_event_name='PreToolUse', tool_name='Bash', cwd=str(self.root),
                       session_id='fixture', tool_input={'command': command})
        result = hook_response(payload, str(ROOT / 'bin/memcap'), 'claude')
        return shlex.split(result['hookSpecificOutput']['updatedInput']['command'])

    def test_wrapped_dev_server_is_persistent_but_wrapped_build_is_finite(self):
        for command in [LIVE,
                        'memcap run --wait 86400 -- ../../node_modules/.bin/expo start --port 8082 > log 2>&1 &',
                        'memcap run --wait 86400 -- pnpm exec expo start > log',
                        "memcap run --wait=60 --shell-command 'expo start --port 8082' > log &",
                        'cd app && memcap run --cwd . -- env PORT=1 expo start > log',
                        '../../node_modules/.bin/expo start --dev-client > log &']:
            with self.subTest(command=command):
                self.assertEqual(persistent_shell(command), 'shell:' + command)
        for command in ['memcap run --wait 86400 -- go test ./... > log &',
                        'memcap run -- go test ./... > log',
                        "memcap run --shell-command 'expo start; go test ./...' > log",
                        "memcap run -- go test --shell-command 'expo start' > log",
                        'memcap run --memory 4 -- expo start > log',
                        'memcap run --resource fixed -- expo start > log',
                        "memcap run --shell /bin/zsh --shell-command 'expo start' > log",
                        'memcap run -- PORT=1 expo start > log',
                        'memcap run -- expo start --help > log &',
                        'memcap run -- expo start & go test ./...',
                        'expo start & go test ./...',
                        'memcap queue -- expo start > log']:
            with self.subTest(command=command):
                self.assertEqual(persistent_shell(command), '', command)

    def test_hook_registers_wrapped_server_as_resource_and_build_as_job(self):
        args = self.hook_args(LIVE)
        self.assertIn('--resource', args)
        self.assertEqual(args[args.index('--resource') + 1], 'shell:' + LIVE)
        self.assertEqual(args[-2:], ['--shell-command', LIVE])
        finite = 'memcap run --wait 86400 -- go test ./... > log &'
        args = self.hook_args(finite)
        self.assertNotIn('--resource', args)
        self.assertEqual(args[-2:], ['--shell-command', finite])

    def test_inner_runner_joins_outer_resource_without_second_reservation(self):
        """The real nested path: one registry entry, and it is the resource."""
        app = self.root / 'app' / 'packages' / 'mobile'
        (app.parent.parent / 'node_modules/.bin').mkdir(parents=True)
        app.mkdir(parents=True)
        started, stop = self.root / 'started', self.root / 'stop'
        expo = app.parent.parent / 'node_modules/.bin/expo'
        expo.write_text('#!/bin/sh\n'
                        f'echo "$PPID $*" > {shlex.quote(str(started))}\n'
                        'i=0\n'
                        f'while [ ! -e {shlex.quote(str(stop))} ] && [ "$i" -lt 200 ]; do\n'
                        '  sleep 0.05; i=$((i+1))\ndone\n')
        expo.chmod(0o700)
        shim = self.root / 'bin' / 'memcap'
        shim.parent.mkdir()
        shim.write_text('#!/bin/sh\nexec "$MEMCAP_TEST_PYTHON" '
                        f'{shlex.quote(str(ROOT / "libexec/scheduler.py"))} "$@"\n')
        shim.chmod(0o700)
        args = self.hook_args(LIVE)
        resource = args[args.index('--resource') + 1]
        queue = self.root / 'state' / 'memcap' / 'queue'
        driver = ("import sys,json;sys.path.insert(0,sys.argv[1]);"
                  "from scheduler import Scheduler;"
                  "s=Scheduler(sys.argv[2],sampler=lambda:json.loads(sys.argv[3]),poll=0.02);"
                  "sys.exit(s.run(['/bin/bash','-c',sys.argv[4]],cwd=sys.argv[5],resource=sys.argv[6]))")
        env = {**self.env, 'PATH': f"{shim.parent}:{os.environ.get('PATH', '/usr/bin:/bin')}",
               'MEMCAP_TEST_PYTHON': sys.executable}
        proc = subprocess.Popen([sys.executable, '-c', driver, str(ROOT / 'libexec'), str(queue),
                                 json.dumps(healthy()), LIVE, str(app), resource],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 20
            while not started.exists() and time.monotonic() < deadline and proc.poll() is None:
                time.sleep(0.05)
            self.assertTrue(started.exists(), proc.stderr.read() if proc.poll() is not None else 'timeout')
            # Only the nested-lease path applies the worker limit to the inner
            # argv; a native (unregistered) exec would leave it unchanged.
            self.assertIn('--max-workers=', started.read_text())
            import scheduler
            jobs = scheduler.Scheduler(queue, sampler=healthy).status()
            self.assertEqual(len(jobs), 1, jobs)
            self.assertTrue(jobs[0]['resource'])
            self.assertEqual(jobs[0]['status'], 'running')
        finally:
            stop.touch()
            out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, err)


class SimulatorStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, MEMCAP_STATE_HOME=str(self.root / 'state'))
        env.start()
        self.addCleanup(env.stop)

    def test_bootstatus_waits_natively_unless_it_boots(self):
        udid = '3A3F994D-8ABE-4566-AF82-50D56F83A50C'
        for command in [f'xcrun simctl bootstatus {udid}', f'xcrun simctl bootstatus {udid} -d',
                        f'xcrun simctl bootstatus {udid} -c', 'xcrun simctl bootstatus booted',
                        f'/usr/bin/xcrun simctl bootstatus {udid} -dc']:
            with self.subTest(command=command):
                self.assertNotEqual(classify(command, self.root).reason, 'simulator-start')
                self.assertEqual(classify(command, self.root).kind, 'light')
        for command in [f'xcrun simctl bootstatus {udid} -b', f'xcrun simctl bootstatus -b {udid}',
                        f'xcrun simctl bootstatus {udid} -bd', f'xcrun simctl bootstatus {udid} -db',
                        f'xcrun simctl boot {udid}',
                        f'xcrun simctl bootstatus {udid}; xcrun simctl boot {udid}']:
            with self.subTest(command=command):
                decision = classify(command, self.root)
                self.assertEqual((decision.kind, decision.reason), ('heavy', 'simulator-start'))


if __name__ == '__main__':
    unittest.main()
