"""Queue patience is separate from polling, hook timeouts, and owner cancellation."""
import io
import shlex
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from scheduler import Scheduler
from scheduler_policy import hook_response


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(sandbox.cleanup)
        environment = patch.dict('os.environ', {
            'MEMCAP_CONFIG_HOME': str(Path(sandbox.name) / 'config'),
            'MEMCAP_STATE_HOME': str(Path(sandbox.name) / 'state'),
            'MC_DRY_RUN': '1',
        })
        environment.start()
        self.addCleanup(environment.stop)

    def hook(self, command, agent='claude', **fields):
        return hook_response(dict(hook_event_name='PreToolUse', tool_name='Bash',
                                  permission_mode='bypassPermissions',
                                  session_id='fixture', cwd='/tmp',
                                  tool_input=dict(command=command, **fields)), '/opt/memcap', agent)

    def test_managed_claude_job_extends_outer_deadline_and_backgrounds(self):
        result = self.hook('make build', timeout=600000)['hookSpecificOutput']
        updated = result['updatedInput']
        self.assertEqual(updated['timeout'], 86400000)
        self.assertTrue(updated['run_in_background'])
        args = shlex.split(updated['command'])
        self.assertEqual(args[args.index('--wait') + 1], '86400')

    def test_codex_yield_is_not_a_queue_deadline(self):
        updated = self.hook('make build', 'codex', yield_time_ms=1000)['hookSpecificOutput']['updatedInput']
        self.assertEqual(updated['yield_time_ms'], 1000)
        self.assertNotIn('timeout', updated)
        args = shlex.split(updated['command'])
        self.assertEqual(args[args.index('--wait') + 1], '86400')

    def test_native_control_keeps_its_original_timeout(self):
        self.assertEqual(self.hook('aws ssm get-parameter --name /fixture', timeout=600000), {})

    def test_existing_wrapper_gets_outer_patience_without_overriding_explicit_queue_deadline(self):
        updated = self.hook('memcap run --wait 30 -- make build', timeout=600000)['hookSpecificOutput']['updatedInput']
        self.assertEqual(updated['timeout'], 86400000)
        self.assertTrue(updated['run_in_background'])
        args = shlex.split(updated['command'])
        self.assertEqual(args[args.index('--wait') + 1], '30')
        payload = dict(hook_event_name='PreToolUse', tool_name='Bash', session_id='fixture', tool_input=updated)
        self.assertEqual(hook_response(payload, '/opt/memcap', 'claude'), {})

    def test_default_queue_patience_is_one_day(self):
        # Advance only the scheduler clock: no real 24-hour wait or host probes.
        clock = [100.0]
        calls = []
        def sleep(_seconds):
            clock[0] += 200
            time.sleep(.005)
        def sample():
            calls.append(1)
            return dict(cap_kb=16 * 1048576, tracked_kb=0, available_kb=16 * 1048576,
                        pressure=1, fault=len(calls) < 11, footprints={}, tracked_pids=[])
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'ran'
            q = Scheduler(Path(tmp) / 'queue', sampler=sample, poll=.01)
            fake = SimpleNamespace(monotonic=lambda: clock[0], time=time.time, sleep=sleep)
            with patch('scheduler.time', fake), patch('orphan_recovery.agent_identity', return_value={}), redirect_stderr(io.StringIO()):
                result = q.run([sys.executable, '-c', f"open({str(marker)!r}, 'w').write('once')"])
            self.assertEqual(result, 0, 'default patience must survive a 2,000-second admission wait')
            self.assertEqual(marker.read_text(), 'once')

    def test_profile_ceiling_preserves_other_environment_and_rejects_malformed_data(self):
        import integrate
        original = {'env': {'KEEP': 'value', 'BASH_DEFAULT_TIMEOUT_MS': '120000'}}
        updated = integrate.managed_timeouts(original)
        self.assertEqual(updated['env']['BASH_MAX_TIMEOUT_MS'], '86400000')
        self.assertEqual(updated['env']['KEEP'], 'value')
        self.assertEqual(updated['env']['BASH_DEFAULT_TIMEOUT_MS'], '120000')
        self.assertNotIn('BASH_MAX_TIMEOUT_MS', original['env'])
        self.assertEqual(integrate.managed_timeouts(updated), updated)
        with self.assertRaises(integrate.IntegrationError):
            integrate.managed_timeouts({'env': []})

    def test_guidance_patch_refreshes_an_existing_session(self):
        from agent_diagnostics import session_guidance
        from session_identity import key
        payload = dict(hook_event_name='PreToolUse', session_id='fixture')
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            directory = state / 'job-feedback'
            directory.mkdir()
            receipt = directory / ('guidance-' + key(payload).replace('/', '-') + '.receipt')
            receipt.write_text('same-version\n')
            self.assertIn('24 hours', session_guidance(payload, state, 'same-version'))
            self.assertEqual(session_guidance(payload, state, 'same-version'), '')


if __name__ == '__main__':
    unittest.main()
