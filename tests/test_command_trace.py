"""Private command evidence uses only temporary state and fake command strings."""
import fcntl
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
import command_trace as trace
from scheduler_policy import classify_shell


class CommandTraceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, MEMCAP_STATE_HOME=self.tmp.name,
                         MEMCAP_CONFIG_HOME=self.tmp.name + '/config', MC_DRY_RUN='1')
        env.start()
        self.addCleanup(env.stop)

    def action(self, action):
        out = io.StringIO()
        with patch.object(sys, 'argv', ['memcap trace', *action.split()]), redirect_stdout(out), patch.object(trace, 'snapshot_pending'):
            trace.main()
        return out.getvalue()

    def rows(self):
        return [json.loads(line) for name in trace.FILES if (trace.root()/name).exists()
                for line in (trace.root()/name).read_text().splitlines()]

    def test_disabled_does_not_create_state(self):
        trace.hook(dict(hook_event_name='PreToolUse', tool_name='Bash', tool_input={'command': 'cat fixture'}))
        self.assertFalse(trace.root().exists())

    def test_exact_command_routes_queue_decisions_and_completion_stay_local(self):
        self.action('on')
        command = 'VALUE="fixture secret"; aws ssm get-parameter --name /fixture'
        trace.hook(dict(hook_event_name='PreToolUse', tool_name='Bash', tool_input={'command': command}), 'managed')
        trace.job(trace.root().parent/'queue', 'queued', job='a'*32, argv=['bash', '-c', command])
        trace.record('waiting', job='a'*32, admission={'reason': 'headroom'}, wait_seconds=60)
        trace.record('completed', job='a'*32, exit_code=7)
        rows = self.rows()
        self.assertEqual(len(rows), 4, 'enabled tracing must persist command and job evidence')
        self.assertEqual(rows[0]['command'], command)
        self.assertEqual(rows[1]['argv'], ['bash', '-c', command])
        self.assertEqual(rows[2]['admission']['reason'], 'headroom')
        self.assertEqual(rows[3]['exit_code'], 7)
        self.assertEqual(trace.root().stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in trace.root().iterdir()))
        self.assertFalse((trace.root().parent/'analytics').exists())
        self.assertFalse((trace.root().parent/'reports').exists())
        self.assertEqual(len(self.action('show').splitlines()), 4)
        selected = [json.loads(line) for line in self.action('show aaaaaaaa').splitlines()]
        self.assertEqual(len(selected), 3)
        self.assertEqual(selected[0]['argv'], ['bash', '-c', command])

    def test_expiry_clear_and_synthetic_job_isolation(self):
        self.action('on')
        trace.job(Path(self.tmp.name)/'fixture-queue', 'queued', command='must not log')
        self.assertEqual(self.rows(), [])
        trace.record('command', command='one')
        with patch.object(trace.time, 'time', return_value=trace.deadline(trace.root()) + 1):
            trace.record('command', command='expired')
        # The expired writer also retires records older than the 24h retention.
        self.assertEqual(self.rows(), [])
        self.action('clear')
        trace.record('command', command='after clear')
        self.assertEqual(self.rows(), [])
        self.assertFalse(json.loads(self.action('status'))['enabled'])

    def test_rotation_is_bounded_and_busy_log_does_not_block_work(self):
        self.action('on')
        with patch.object(trace, 'SEGMENT_BYTES', 256):
            for n in range(20):
                trace.record('command', command='x'*100, index=n)
        self.assertLessEqual(len(self.rows()), 6)
        self.assertEqual(self.rows()[-1]['index'], 19)
        with open(trace.root()/'trace.lock', 'r') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            trace.record('command', command='busy')
        self.assertNotEqual(self.rows()[-1]['command'], 'busy')

    def test_trace_controls_are_native_and_no_output_payload_is_recorded(self):
        self.action('on')
        for action in ('on', 'off', 'status', 'show', 'show aaaaaaaa', 'clear', 'unknown'):
            self.assertEqual(classify_shell('memcap trace ' + action)[0], 'light')
        trace.hook(dict(hook_event_name='PostToolUse', tool_name='Bash',
                        tool_input={'command': 'cat fixture'}, tool_response='private output'))
        self.assertEqual(self.rows(), [])

    def test_raw_command_fields_cannot_enter_analytics_transport(self):
        from analytics_events import make_event
        row = make_event('queued', {'command': 'private-command-sentinel',
                                   'argv': ['private-command-sentinel'], 'cwd': '/private-sentinel'},
                         b'k'*32, 'a'*32, 1, build='b'*64, policy='c'*64, boot='d'*32)
        self.assertNotIn('sentinel', json.dumps(row))

    def test_snapshot_checks_supervisor_identity_without_changing_queue(self):
        self.action('on')
        directory = trace.root().parent/'queue'
        directory.mkdir()
        path = directory/'jobs.json'
        path.write_text(json.dumps({'jobs': [dict(id='a'*32, owner=123, owner_start='original', status='waiting')]}))
        before = path.read_bytes()
        for start, uid, expected in [('reused', os.getuid(), 0), ('original', os.getuid()+1, 0),
                                     ('original', os.getuid(), 1)]:
            with patch('boot_timeout.table_now', return_value={'123': dict(start=start, uid=uid, command='fixture command')}):
                trace.snapshot_pending()
            self.assertEqual(len(self.rows()), expected)
        self.assertEqual(path.read_bytes(), before)

    def test_dispatcher_and_hook_capture_commands_in_private_state(self):
        self.action('on')
        root = Path(__file__).resolve().parents[1]
        payload = dict(hook_event_name='PreToolUse', permission_mode='bypassPermissions',
                       session_id='fixture', cwd=self.tmp.name, tool_name='Bash',
                       tool_input={'command': 'aws ssm get-parameter --name /fixture'})
        result = subprocess.run([sys.executable, str(root/'libexec/scheduler.py'), 'hook', 'claude'],
                                input=json.dumps(payload), capture_output=True, text=True,
                                env={**os.environ, 'HOME': self.tmp.name}, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rows()[-1]['command'], payload['tool_input']['command'])
        self.assertEqual(self.rows()[-1]['route'], 'native')
        status = subprocess.run([str(root/'bin/memcap'), 'trace', 'status'],
                                capture_output=True, text=True,
                                env={**os.environ, 'HOME': self.tmp.name, 'MEMCAP_ROOT': str(root)}, timeout=10)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertTrue(json.loads(status.stdout)['enabled'])


if __name__ == '__main__':
    unittest.main()
