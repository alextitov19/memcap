"""Offline compatibility contracts. Never launch any classified workload or engine."""
import hashlib
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


class ToolCompatibility(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, 'HOME': str(self.root),
                    'DOCKER_CONFIG': str(self.root / '.docker'),
                    'MEMCAP_ROOT': str(ROOT), 'MC_DRY_RUN': '1',
                    'MEMCAP_CONFIG_HOME': str(self.root / 'config'),
                    'MEMCAP_STATE_HOME': str(self.root / 'state'),
                    'MC_DOCKER_STORE': str(self.root / 'desktop.json')}
        for key in ('MC_DOCKER_RUNTIME', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'MC_DOCKER_CEILING_MIB'):
            self.env.pop(key, None)
        environment = patch.dict(os.environ, self.env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def shell(self, body, data=None):
        result = subprocess.run(['/bin/bash', '-c', body], env=self.env,
                                input=data, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def selected(self, name, endpoint):
        directory = self.root / '.docker'
        directory.mkdir(exist_ok=True)
        (directory / 'config.json').write_text(json.dumps({'currentContext': name}))
        meta = directory / 'contexts/meta' / hashlib.sha256(name.encode()).hexdigest()
        meta.mkdir(parents=True, exist_ok=True)
        (meta / 'meta.json').write_text(json.dumps({'Name': name, 'Endpoints': {'docker': {'Host': endpoint}}}))

    def decision(self, command):
        return classify(command, self.root, self.env)

    def test_mise_exec_preserves_underlying_demand(self):
        for command in ('mise exec -- go test ./...', 'mise x node@22 -- npm run build',
                        'mise --cd backend exec -- go build ./...', 'mise install node@22'):
            self.assertEqual(self.decision(command).kind, 'heavy', command)
        for command in ('mise current', 'mise exec -- xh GET https://example.com',
                        'mise x node@22 -- node --version', 'mise exec --help',
                        'mise exec -- echo "go test ./..."'):
            self.assertEqual(self.decision(command).kind, 'light', command)

    def test_mise_tasks_are_inspected_and_fingerprinted_not_executed(self):
        task = self.root / 'mise.toml'
        task.write_text('[tasks.build]\nrun = "go test ./..."\n[tasks.status]\nrun = "git status"\n')
        first = self.decision('mise run build')
        self.assertEqual(first.kind, 'heavy')
        self.assertEqual(self.decision('mise run status').kind, 'light')
        task.write_text('[tasks.build]\nrun = "echo ready"\n')
        second = self.decision('mise run build')
        self.assertEqual(second.kind, 'light')
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_just_recipes_inspect_work_not_recipe_names(self):
        (self.root / 'justfile').write_text('build:\n    echo ready\ncheck:\n    go test ./...\nstatus:\n    git status\n')
        self.assertEqual(self.decision('just check').kind, 'heavy')
        for command in ('just build', 'just status', 'just --list', 'just --show check', 'just --dry-run check'):
            self.assertEqual(self.decision(command).kind, 'light', command)

    def test_hyperfine_inspects_commands_not_labels(self):
        self.assertEqual(self.decision("hyperfine --warmup 1 'go build ./...'").kind, 'heavy')
        self.assertEqual(self.decision("hyperfine --prepare 'npm run build' 'cat README.md'").kind, 'heavy')
        self.assertEqual(self.decision("hyperfine --command-name 'go build' 'cat README.md'").kind, 'light')

    def test_raw_test_runner_and_local_orb_commands_preserve_demand(self):
        for command in ('gotestsum --raw-command -- go test -json ./...',
                        'orb -m ubuntu -- go test ./...', 'orb go build ./...'):
            self.assertEqual(self.decision(command).kind, 'heavy', command)
        for command in ('gotestsum --raw-command -- cat saved-tests.json',
                        'orb -m ubuntu -- cat /etc/os-release', 'orb list', 'orb config get memory_mib'):
            self.assertEqual(self.decision(command).kind, 'light', command)

    def test_dependency_cycles_and_missing_scripts_remain_bounded(self):
        (self.root / 'mise.toml').write_text('[tasks.a]\ndepends = ["b"]\n[tasks.b]\ndepends = ["a"]\n')
        self.assertEqual(self.decision('mise run a').kind, 'light')
        (self.root / 'justfile').write_text('a: b\n    echo a\nb: a\n    echo b\n')
        self.assertEqual(self.decision('just a').kind, 'light')
        self.assertEqual(self.decision('mise run unknown').kind, 'light')

    def test_task_inspection_never_executes_commands(self):
        marker = self.root / 'must-not-exist'
        (self.root / 'justfile').write_text(f'check:\n    touch {marker}\n    go test ./...\n')
        self.assertEqual(self.decision('just check').kind, 'heavy')
        self.assertFalse(marker.exists())

    def test_help_flags_are_not_confused_with_child_arguments(self):
        for command in ('trivy image --help', 'gotestsum --format testname --help', 'k6 run --help',
                        'pod install --help', 'sqlc generate --help', 'eas build --local --help'):
            self.assertEqual(self.decision(command).kind, 'light', command)
        (self.root / 'justfile').write_text('check args:\n    go test ./...\n')
        self.assertEqual(self.decision('just check -- --help').kind, 'heavy')
        self.assertEqual(self.decision('gotestsum -- ./... --raw-command').kind, 'heavy')

    def test_task_lookup_honors_expired_inspection_budget(self):
        from demand_policy import Classifier
        worker = Classifier(self.root, self.env)
        worker.deadline = 0
        with patch('pathlib.Path.is_file', side_effect=AssertionError('lookup after deadline')):
            self.assertIsNone(worker.argv(['mise', 'run', 'missing'], self.root))
        self.assertTrue(worker.uncertain)

    def test_hook_keeps_light_tools_native_and_manages_wrapped_build(self):
        from scheduler_policy import hook_response
        with patch.dict(os.environ, self.env, clear=True), patch('native_observer.available', return_value=False):
            for command in ('mise current', 'docker --context orbstack ps', 'gotestsum --help'):
                payload = dict(hook_event_name='PreToolUse', tool_name='Bash', cwd=str(self.root),
                               tool_input={'command': command, 'timeout': 1000})
                self.assertEqual(hook_response(payload, '/installed/memcap', 'claude'), {})
            payload['tool_input']['command'] = 'mise exec -- go test ./...'
            response = hook_response(payload, '/installed/memcap', 'claude')
            self.assertIn('updatedInput', response['hookSpecificOutput'])

    def test_engine_switch_retains_old_disposable_environment(self):
        import environments
        token = 'memcap-' + 'a' * 32
        state = self.root / 'environments'
        with patch.dict(os.environ, self.env, clear=True), patch('environments.state_root', return_value=state), \
                patch('environments.local_engine', return_value='unix:///orbstack.sock'), \
                patch('scheduler.processes', return_value={}), \
                patch('environments.containers', side_effect=AssertionError('must not query wrong engine')):
            with environments.registry(state) as data:
                data[token] = dict(engine='unix:///desktop.sock', phase='releasing',
                                   owner=dict(pid=123, start='old'), agent=dict(pid=124, start='old'))
            self.assertEqual(environments.authorize(token), [])

    def test_new_workload_tools_and_their_inspection_modes(self):
        for command in ('gotestsum -- ./...', 'gotestsum', 'staticcheck ./...', 'deadcode ./...',
                        'air', 'trivy fs .', 'trivy image example:local', 'k6 run load.js',
                        'whisper-cli -m large.bin -f sample.wav', 'ffmpeg -i in.mov out.mp4',
                        'fastlane ios build', 'pod install', 'sqlc generate', 'vhs demo.tape',
                        'eas build --local --platform ios'):
            self.assertEqual(self.decision(command).kind, 'heavy', command)
        for command in ('gotestsum --help', 'gotestsum tool slowest', 'air init', 'trivy --version',
                        'k6 version', 'whisper-cli --help', 'ffmpeg -version', 'ffprobe in.mov',
                        'fastlane --help', 'pod --version', 'sqlc version', 'eas build --platform ios',
                        'xh GET https://example.com', 'bat README.md', 'eza -la', 'sg --pattern foo .',
                        'psql --version', 'goose -dir migrations postgres example status',
                        'gitleaks version', 'hadolint Dockerfile', 'mole --help'):
            self.assertEqual(self.decision(command).kind, 'light', command)

    def test_docker_global_options_do_not_hide_builds(self):
        for command in ('docker --context orbstack build .', 'docker -H unix:///tmp/docker.sock compose up',
                        'docker --context=orbstack buildx build .'):
            self.assertEqual(self.decision(command).kind, 'heavy', command)
        for command in ('docker --context orbstack ps', 'docker --context orbstack compose config',
                        'docker --context build ps', 'docker context use orbstack'):
            self.assertEqual(self.decision(command).kind, 'light', command)

    def test_selected_orbstack_wins_over_installed_desktop(self):
        self.selected('orbstack', f'unix://{self.root}/.orbstack/run/docker.sock')
        (self.root / 'desktop.json').write_text('{"MemoryMiB":10240}')
        out = self.shell('source "$MEMCAP_ROOT/libexec/docker.sh"; mc_docker_runtime')
        self.assertEqual(out, 'orbstack')

    def test_non_desktop_context_does_not_read_desktop_ceiling(self):
        self.selected('orbstack', f'unix://{self.root}/.orbstack/run/docker.sock')
        (self.root / 'desktop.json').write_text('{"MemoryMiB":10240}')
        out = self.shell('source "$MEMCAP_ROOT/libexec/common.sh"; source "$MEMCAP_ROOT/libexec/docker.sh"; mc_docker_ceiling_gb; printf "rc=%s" "$?"')
        self.assertEqual(out, 'rc=1')

    def test_context_env_precedes_host_and_host_precedes_saved_context(self):
        self.selected('desktop-linux', f'unix://{self.root}/.docker/run/docker.sock')
        self.selected('orbstack', f'unix://{self.root}/.orbstack/run/docker.sock')
        self.env['DOCKER_HOST'] = 'ssh://example.test'
        body = 'source "$MEMCAP_ROOT/libexec/docker.sh"; mc_docker_runtime'
        self.assertEqual(self.shell(body), 'remote')
        self.env['DOCKER_CONTEXT'] = 'desktop-linux'
        self.assertEqual(self.shell(body), 'desktop')

    def test_unknown_context_never_falls_back_to_desktop_mutation(self):
        self.selected('custom', 'unix:///tmp/custom-engine.sock')
        (self.root / 'desktop.json').write_text('{"MemoryMiB":10240}')
        out = self.shell('source "$MEMCAP_ROOT/libexec/docker.sh"; mc_docker_apply; printf "rc=%s" "$?"')
        self.assertNotIn('would set Docker', out)
        self.assertTrue(out.endswith('rc=1'), out)

    def test_malformed_and_missing_context_metadata_is_unknown(self):
        config = self.root / '.docker'
        config.mkdir()
        body = 'source "$MEMCAP_ROOT/libexec/docker.sh"; mc_docker_runtime'
        (config / 'config.json').write_text('{broken')
        self.assertEqual(self.shell(body), 'unknown')
        (config / 'config.json').write_text('{"currentContext":"missing"}')
        self.assertEqual(self.shell(body), 'unknown')
        for invalid in (None, False, 0, [], {}):
            (config / 'config.json').write_text(json.dumps({'currentContext': invalid}))
            self.assertEqual(self.shell(body), 'unknown')

    def test_context_fifo_and_symlink_loop_do_not_hang(self):
        config = self.root / '.docker'
        config.mkdir()
        os.mkfifo(config / 'config.json')
        body = 'source "$MEMCAP_ROOT/libexec/docker.sh"; mc_docker_runtime'
        self.assertEqual(self.shell(body), 'unknown')
        (config / 'config.json').unlink()
        (config / 'config.json').symlink_to('config.json')
        self.assertEqual(self.shell(body), 'unknown')

    def test_orbstack_and_desktop_count_together_not_mentions(self):
        out = self.shell('source "$MEMCAP_ROOT/libexec/classify.sh"; mc_classify',
                         '901 1 100 /Applications/OrbStack.app/Contents/MacOS/OrbStack\n'
                         '902 1 200 /Applications/OrbStack.app/Contents/MacOS/bin/orbctl\n'
                         '903 1 300 /Applications/Docker.app/Contents/MacOS/com.docker.backend\n'
                         '904 1 400 /usr/bin/rg /Applications/OrbStack.app/Contents/MacOS/OrbStack\n')
        self.assertIn('DOCKER_KB=600\n', out)

    def test_database_goose_is_not_agent_but_real_agent_and_ancestry_are(self):
        binary = self.root / 'Cellar/goose/3.28.0/bin/goose'
        binary.parent.mkdir(parents=True)
        binary.write_text('fixture, never executed')
        binary.chmod(0o755)
        bindir = self.root / 'bin'
        bindir.mkdir()
        (bindir / 'goose').symlink_to(binary)
        self.env['PATH'] = str(bindir) + ':' + self.env['PATH']
        out = self.shell('source "$MEMCAP_ROOT/libexec/classify.sh"; mc_classify',
                         f'901 1 100 {bindir}/goose -dir migrations postgres example status\n'
                         '902 1 200 /another/bin/goose session\n'
                         '903 1 300 /usr/local/bin/codex\n'
                         f'904 903 400 {binary} postgres example up\n')
        self.assertIn('AGENTPIDS=" 902 903"', out)
        self.assertIn('PROTECTEDPIDS=" 902 903 904"', out)


if __name__ == '__main__':
    unittest.main()
