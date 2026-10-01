"""Memory classification contracts; no fixture command is executed."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'libexec'))

class DemandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name)
        env = patch.dict(os.environ, MEMCAP_STATE_HOME=str(self.cwd / 'state'))
        env.start()
        self.addCleanup(env.stop)

    def decision(self, command):
        from demand_policy import classify
        return classify(command, self.cwd)

    def test_ordinary_commands_do_not_need_positive_syntax_proof(self):
        commands = [
            'aws --profile dev ssm get-parameter --name "$(cat parameter-name)" --with-decryption',
            'git config --get core.hooksPath',
            'git add a.py && git commit -m "fix: go build is mentioned as data" && git push origin main',
            'set -euo pipefail; gh api repos/example/repo/pulls --jq ".[].number"',
            "python3 -c 'from pathlib import Path; print(Path(\"note\").read_text())'",
            'python3 unknown-helper.py',
            'weasyprint report.html report.pdf',
            'novel-cli fetch-parameters --new-option',
            'rg "go test|npm run build" .',
            'for file in *.md; do cat "$file"; done',
            "cat <<'EOF'\ngo build ./...\nEOF",
            'ssh host "npm run build"',
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command).kind, 'light')

    def test_positive_heavy_evidence_survives_wrappers(self):
        commands = ['go test ./...', 'cargo build --release', 'npm run build',
                    'cd backend && go test ./...', 'env GOOS=darwin go build ./cmd/server',
                    'bash -lc "go test ./..."', 'x=$(go test ./...); echo "$x"',
                    'printf "%s" "$(go build ./...)"',
                    "python3 -c 'import subprocess; subprocess.run([\"go\", \"test\", \"./...\"])'",
                    'python3 -m pytest tests/', 'go -C backend test ./...',
                    'find . -name go.mod -exec go test ./... \\;',
                    'docker build .', 'xcrun simctl boot ABCD',
                    'npx playwright test', 'for p in a b; do go test "$p"; done']
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command).kind, 'heavy')

    def test_local_script_contents_and_changes_drive_decision(self):
        script = self.cwd / 'helper.sh'
        script.write_text('#!/bin/sh\naws ssm get-parameter --name fixture\n')
        first = self.decision('bash helper.sh')
        self.assertEqual(first.kind, 'light')
        script.write_text('#!/bin/sh\ngo build ./...\n')
        second = self.decision('bash helper.sh')
        self.assertEqual(second.kind, 'heavy')
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_package_script_name_is_not_the_workload(self):
        (self.cwd / 'package.json').write_text(json.dumps({'scripts': {
            'fetch-config': 'aws ssm get-parameter --name fixture',
            'prepare-report': 'vite build', 'build': 'echo already-built'}}))
        self.assertEqual(self.decision('npm run fetch-config').kind, 'light')
        self.assertEqual(self.decision('npm run prepare-report').kind, 'heavy')
        self.assertEqual(self.decision('npm run build').kind, 'light')

    def test_quoted_heredoc_python_is_analyzed_as_python(self):
        self.assertEqual(self.decision("python3 - <<'PY'\nimport subprocess\nsubprocess.run(['go', 'test', './...'])\nPY").kind, 'heavy')
        self.assertEqual(self.decision("python3 - <<'PY'\nprint('go test ./...')\nPY").kind, 'light')
        self.assertEqual(self.decision("cd backend && python3 - <<'PY'\nimport subprocess\nsubprocess.run(['go', 'test', './...'])\nPY").kind, 'heavy')

    def test_slow_dependency_probe_cannot_hide_a_later_explicit_workload(self):
        from demand_policy import Classifier
        worker = Classifier(self.cwd)
        worker.deadline = 0
        self.assertEqual(worker.shell('git commit -m fixture; go test ./...'), 'known-workload')

    def test_native_demand_does_not_consult_admission_even_under_red_pressure(self):
        from scheduler_policy import hook_response
        with patch('scheduler.Scheduler.admissible', side_effect=AssertionError('native consulted admission')):
            for command in ['git status', 'aws ssm get-parameter --name fixture', 'unknown-helper --read']:
                payload = dict(hook_event_name='PreToolUse', tool_name='Bash', cwd=str(self.cwd),
                               tool_input={'command': command, 'timeout': 1000})
                with patch('native_observer.available', return_value=False):
                    self.assertEqual(hook_response(payload, '/installed/memcap', 'claude'), {})

    def test_heap_ceiling_and_quoted_build_text_are_not_allocation_evidence(self):
        for command in ['node --max-old-space-size=4096 unknown.js',
                        'echo "go test ./..."', 'cat <<EOF\ngo test ./...\nEOF']:
            self.assertEqual(self.decision(command).kind, 'light')

    def test_uninvoked_python_workload_and_remote_run_method_are_not_heavy(self):
        import shlex
        bodies = [
            'import subprocess\ndef build():\n subprocess.run(["go", "build"])\nprint("status")',
            'remote.run("go build ./...")',
            'if False:\n import torch\nprint("status")',
        ]
        for body in bodies:
            self.assertEqual(self.decision('python3 -c ' + shlex.quote(body)).kind, 'light')
        self.assertEqual(self.decision('python3 -c ' + shlex.quote(bodies[0] + '\nbuild()')).kind, 'heavy')
        self.assertEqual(self.decision("python3 -c 'from subprocess import run as launch; launch([\"go\", \"build\"])'").kind, 'heavy')

    def test_unused_shell_function_does_not_queue_the_read_path(self):
        body = 'build() { go test ./...; }; cat README.md'
        self.assertEqual(self.decision(body).kind, 'light')
        self.assertEqual(self.decision(body + '; build').kind, 'heavy')
        body = 'build() { result=$(go test ./...); }; cat README.md'
        self.assertEqual(self.decision(body).kind, 'light')
        self.assertEqual(self.decision(body + '; build').kind, 'heavy')

    def test_observed_high_usage_promotes_exact_fingerprint_only(self):
        import native_observer as native
        import time
        original = self.decision('novel-helper --read')
        native.write_json(native.root() / 'profiles.json', {original.fingerprint: {'peak_kb': 800000, 'at': time.time()}})
        self.assertEqual(self.decision('novel-helper --read').reason, 'observed-high-memory')
        self.assertEqual(self.decision('novel-helper --other').kind, 'light')

    def test_git_hooks_are_inspected_without_running_them(self):
        from demand_policy import Classifier
        hooks = self.cwd / '.git/hooks'
        hooks.mkdir(parents=True)
        hook = hooks / 'pre-commit'
        hook.write_text('#!/bin/sh\ngo test ./...\n')
        hook.chmod(0o700)
        with patch.object(Classifier, 'git_hooks', return_value=hooks):
            self.assertEqual(self.decision('git commit -m fixture').kind, 'heavy')
            hook.write_text('#!/bin/sh\nprintf approved\n')
            self.assertEqual(self.decision('git commit -m fixture').kind, 'light')
            hook.write_text('#!/bin/sh\ngo test ./...\n')
            hook.chmod(0o600)
            self.assertEqual(self.decision('git commit -m fixture').kind, 'light')

    def test_native_hook_keeps_permissions_deadlines_and_worker_settings(self):
        from scheduler_policy import hook_response
        for agent, tool in [('claude', 'Bash'), ('codex', 'exec_command')]:
            payload = dict(hook_event_name='PreToolUse', tool_name=tool, cwd=str(self.cwd),
                           permission_mode='default', tool_input={'command': 'git commit -m fixture',
                           'timeout': 12000, 'run_in_background': False})
            self.assertEqual(hook_response(payload, '/installed/memcap', agent), {})

    def test_observation_never_replays_login_or_shell_startup_files(self):
        from scheduler_policy import hook_response
        from unittest.mock import patch
        with patch('native_observer.available', return_value=True):
            for settings in ({'login': True}, {'shell': '/bin/zsh', 'login': False}):
                payload = dict(hook_event_name='PreToolUse', tool_name='exec_command', cwd=str(self.cwd),
                    permission_mode='bypassPermissions', tool_input={'cmd': 'unknown-helper', **settings})
                self.assertEqual(hook_response(payload, '/installed/memcap', 'codex'), {})
            with patch.dict(os.environ, BASH_ENV='/fixture/startup.sh'):
                payload = dict(hook_event_name='PreToolUse', tool_name='Bash', cwd=str(self.cwd),
                               tool_input={'command': 'unknown-helper'})
                self.assertEqual(hook_response(payload, '/installed/memcap', 'claude'), {})

    def test_labeled_replay_and_negative_controls(self):
        from demand_replay import replay
        corpus = ROOT / 'tests/fixtures/demand-replay.json'
        result = replay(corpus, self.cwd, repetitions=1)
        self.assertEqual((result['false_heavy'], result['false_light']), (0, 0))
        # Deliberately broken classifiers must fail both sides of the contract.
        self.assertGreater(replay(corpus, self.cwd, 1, lambda _: 'heavy')['false_heavy'], 0)
        self.assertGreater(replay(corpus, self.cwd, 1, lambda _: 'light')['false_light'], 0)

    def test_replay_never_executes_unknown_commands(self):
        from demand_replay import replay
        corpus = self.cwd / 'replay.json'
        marker = self.cwd / 'must-not-exist'
        corpus.write_text(json.dumps([dict(command=f'touch {marker}', expected='light')]))
        self.assertEqual(replay(corpus, self.cwd, 1)['false_heavy'], 0)
        self.assertFalse(marker.exists())

    def test_observed_native_command_named_run_is_not_reported_as_managed(self):
        import contextlib
        import io
        import scheduler
        payload = dict(hook_event_name='PreToolUse', tool_name='Bash', cwd=str(self.cwd),
                       tool_input={'command': 'unfamiliar-helper run action'})
        with patch.object(sys, 'argv', ['scheduler.py', 'hook', 'claude']), \
             patch('scheduler.json.load', return_value=payload), \
             patch('native_observer.available', return_value=True), \
             patch('analytics_events.emit') as emit, contextlib.redirect_stdout(io.StringIO()):
            scheduler.main()
        route = next(call.kwargs['route'] for call in emit.call_args_list if call.args[0] == 'route')
        self.assertEqual(route, 'native')

if __name__ == '__main__':
    unittest.main()
