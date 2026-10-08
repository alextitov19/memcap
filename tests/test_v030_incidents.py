"""October 8 incident reproductions. Synthetic commands and isolated state only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'libexec'))
from demand_policy import classify
from scheduler_policy import hook_response, persistent_shell, worker_argv
from agent_diagnostics import guidance


class Incidents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, 'HOME': str(self.root), 'MC_DRY_RUN': '1',
                    'MEMCAP_CONFIG_HOME': str(self.root/'config'),
                    'MEMCAP_STATE_HOME': str(self.root/'state')}
        env = patch.dict(os.environ, self.env)
        env.start()
        self.addCleanup(env.stop)

    def test_xcode_version_is_native_but_build_remains_managed(self):
        for command in ['xcodebuild -version', 'xcrun xcodebuild -version',
                        'cat build.sh; git log -1; xcodebuild -version | head -1']:
            with self.subTest(command=command):
                self.assertEqual(classify(command, self.root).kind, 'light')
        for command in ['xcodebuild -scheme App build',
                        'xcodebuild -version; xcodebuild build',
                        'xcodebuild -version $(go build ./...)']:
            self.assertEqual(classify(command, self.root).kind, 'heavy')

    def test_shell_executable_lookup_does_not_launch_the_named_workload(self):
        for command in ['command -v maestro', 'command -V xcodebuild',
                        'command -p -v go', 'command -pv maestro',
                        'rg --files . | head -35; command -v idb; command -v maestro']:
            self.assertEqual(classify(command, self.root).kind, 'light', command)
        for command in ['command maestro test flow.yaml', 'command -p go test ./...',
                        'command -v $(go build ./...)', 'command -v maestro; maestro test flow.yaml']:
            self.assertEqual(classify(command, self.root).kind, 'heavy', command)

    def test_real_expo_start_is_managed_but_status_is_native(self):
        for command in ['expo start --clear', 'pnpm exec expo start --dev-client --lan',
                        'npx expo start --max-workers 1', 'expo export --platform web',
                        'expo start -- --help']:
            self.assertEqual(classify(command, self.root).kind, 'heavy', command)
        for command in ['expo --version', 'expo start --help', 'expo config --json',
                        "echo 'expo start --clear'"]:
            self.assertEqual(classify(command, self.root).kind, 'light', command)

    def test_expo_server_is_persistent_but_mixed_work_stays_finite(self):
        for command in ['expo start --clear', 'pnpm exec expo start --port 8081',
                        'npx expo start --max-workers 1',
                        'EXPO_NO_DOTENV=1 EXPO_PUBLIC_API_MODE=mock pnpm exec expo start']:
            self.assertTrue(persistent_shell(command), command)
        for command in ['expo export', 'expo start --help',
                        'expo start; go test ./...', 'expo start && unknown-helper',
                        'EXPO_PUBLIC_API_URL=$(helper) expo start']:
            self.assertFalse(persistent_shell(command), command)

    def test_expo_cli_worker_limit_keeps_smaller_owner_setting(self):
        self.assertEqual(worker_argv(['pnpm', 'exec', 'expo', 'start', '--clear'], self.root, 2),
                         ['pnpm', 'exec', 'expo', 'start', '--clear', '--max-workers=2'])
        self.assertEqual(worker_argv(['expo', 'start', '--max-workers', '1'], self.root, 4),
                         ['expo', 'start', '--max-workers=1'])
        self.assertEqual(worker_argv(['expo', 'start', '--', '--help'], self.root, 2),
                         ['expo', 'start', '--max-workers=2', '--', '--help'])
        for args in [['expo', 'config', '--json'], ['expo', 'start', '--help']]:
            self.assertEqual(worker_argv(args, self.root, 2), args)

    def test_explicit_expo_runner_keeps_resource_out_of_finite_waits(self):
        import scheduler
        with patch.object(sys, 'argv', ['memcap', 'run', '--cwd', str(self.root),
                                       '--', 'pnpm', 'exec', 'expo', 'start']), \
             patch.object(scheduler.Scheduler, 'run', return_value=0) as run:
            self.assertEqual(scheduler.main(), 0)
        # The actual runner must pass a persistent key to admission, rather than
        # leave this registered server in every finite completion/Stop wait.
        self.assertEqual(run.call_args.args[2], 'shell:pnpm exec expo start')

    def test_named_start_script_running_tests_stays_finite(self):
        import scheduler
        (self.root/'package.json').write_text(json.dumps({'scripts': {'start': 'go test ./...'}}))
        with patch.object(sys, 'argv', ['memcap', 'run', '--cwd', str(self.root), '--', 'npm', 'start']), \
             patch.object(scheduler.Scheduler, 'run', return_value=0) as run:
            self.assertEqual(scheduler.main(), 0)
        self.assertEqual(run.call_args.args[2], '')

    def test_direct_emulator_is_persistent_without_exempting_mixed_work(self):
        import scheduler
        command = 'emulator -avd Fixture_Phone -no-audio'
        self.assertTrue(persistent_shell(command))
        with patch.object(sys, 'argv', ['memcap', 'run', '--cwd', str(self.root),
                                       '--', 'emulator', '-avd', 'Fixture_Phone', '-no-audio']), \
             patch.object(scheduler.Scheduler, 'run', return_value=0) as run:
            self.assertEqual(scheduler.main(), 0)
        self.assertEqual(run.call_args.args[2], 'shell:' + command)
        for command in ['emulator -list-avds', 'emulator -avd Fixture_Phone -help',
                        'emulator -avd Fixture_Phone -unknown-mode',
                        'emulator -avd Fixture_Phone; go test ./...',
                        'echo emulator -avd Fixture_Phone']:
            self.assertFalse(persistent_shell(command), command)

    def test_codex_session_directory_does_not_override_tool_directory(self):
        # Codex's Bash-compatible hook may omit workdir although exec_command
        # executes in a subdirectory. The synthetic runner prints its real cwd.
        actual = self.root/'sub directory'
        actual.mkdir()
        runner = self.root/'memcap'
        runner.write_text('#!/bin/sh\nwhile [ "$#" -gt 0 ]; do\n'
                          ' if [ "$1" = --cwd ]; then cd "$2" || exit; shift; fi\n'
                          ' shift\ndone\npwd\n')
        runner.chmod(0o700)
        payload = dict(hook_event_name='PreToolUse', tool_name='Bash', cwd=str(self.root),
                       permission_mode='bypassPermissions', session_id='fixture',
                       tool_input={'command': 'go test ./...', 'login': False})
        result = hook_response(payload, str(runner), 'codex')
        command = result['hookSpecificOutput']['updatedInput']['command']
        output = subprocess.check_output(['/bin/bash', '-c', command], cwd=actual, env=self.env, text=True)
        self.assertEqual(Path(output.strip()).resolve(), actual.resolve())
        for agent in ['codex', 'claude']:
            payload['tool_input']['workdir'] = str(self.root)
            result = hook_response(payload, str(runner), agent)
            command = result['hookSpecificOutput']['updatedInput']['command']
            output = subprocess.check_output(['/bin/bash', '-c', command], cwd=actual, env=self.env, text=True)
            self.assertEqual(Path(output.strip()).resolve(), self.root.resolve())

    def test_heavy_completion_is_not_an_inspection_or_polling_incident(self):
        for brief in [False, True]:
            for code in [0, 1]:
                payload = dict(hook_event_name='PostToolUse', tool_name='Bash',
                               tool_input={'command': 'memcap run -- go test ./...'},
                               tool_response={'exit_code': code, 'output':
                                   'memcap: queued abcdef12 (heavy lane): all finite-job slots occupied.\n'
                                   'memcap: admitted abcdef12; command started.\nTest result\n'})
                text = guidance(payload, self.root, brief=brief)
                self.assertNotIn('MUST report delayed inspection', text)
                self.assertNotIn('You MUST report a delayed inspection', text)
                self.assertIn('final exit status', text)
                self.assertIn('Only if', text)

    def test_pending_lane_notice_needs_no_new_memory_probe(self):
        payload = dict(hook_event_name='PostToolUse', tool_name='TaskOutput',
                       tool_response={'output': 'memcap: queued abcdef12 (heavy lane): all finite-job slots occupied.'})
        with patch('agent_diagnostics.measure', side_effect=AssertionError('unnecessary probe')):
            text = guidance(payload, self.root)
        self.assertIn('pending', text)
        self.assertIn('Only if', text)

    def test_isolated_claude_inspection_keeps_host_guard_visible_command(self):
        payload = dict(hook_event_name='PreToolUse', tool_name='Bash',
                       cwd=str(self.root), session_id='fixture',
                       tool_input={'command': 'lsof -iTCP -sTCP:LISTEN -P'})
        self.assertEqual(classify(payload['tool_input']['command'], self.root).confidence, 'unknown')
        with patch('native_observer.available', return_value=True):
            # Ordinary non-isolated calls still get passive learning.
            self.assertIn('_native', str(hook_response(payload, '/fixture/memcap', 'claude')))
            for scope in [{'agent_id': 'fixture-child'},
                          {'cwd': str(self.root/'.claude/worktrees/fixture')}]:
                scoped = {**payload, **scope}
                self.assertEqual(hook_response(scoped, '/fixture/memcap', 'claude'), {})
                scoped['tool_input'] = {'command': 'go test ./...'}
                result = hook_response(scoped, '/fixture/memcap', 'claude')
                self.assertIn('/fixture/memcap run', str(result))


if __name__ == '__main__':
    unittest.main()
