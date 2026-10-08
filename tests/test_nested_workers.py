"""Real Node launches with tiny local CLI fixtures; never run a real test suite."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'libexec'))
from scheduler_policy import worker_argv, worker_environment


@unittest.skipUnless(shutil.which('node'), 'Node required for runtime worker fixtures')
class NestedWorkers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='memcap worker fixture ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, 'HOME': str(self.root), 'MEMCAP_CONFIG_HOME': str(self.root / 'config'),
                    'MEMCAP_STATE_HOME': str(self.root / 'state'), 'MC_DRY_RUN': '1'}
        self.env.pop('NODE_OPTIONS', None)
        self.env.pop('MEMCAP_NODE_WORKERS', None)

    def cli(self, package='jest', entry='bin/jest.js'):
        directory = self.root / 'node_modules' / package
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'package.json').write_text(json.dumps({'name': package, 'version': '29.0.0'}))
        file = directory / entry
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('console.log(JSON.stringify(process.argv.slice(2)));\n')
        return file

    def execute(self, file, args=(), workers=4, env=None):
        result = subprocess.run(['node', str(file), *args], cwd=self.root,
                                env=worker_environment(env or self.env, workers),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_compound_shell_pipeline_reaches_nested_jest(self):
        file = self.cli()
        helper = self.root / 'helper.sh'
        helper.write_text('node ' + shlex.quote(str(file)) + ' --selectProjects unit\n')
        command = 'cd ' + shlex.quote(str(self.root)) + '; bash ' + shlex.quote(str(helper)) + ' | cat'
        result = subprocess.run(['/bin/bash', '-c', command], env=worker_environment(self.env, 4),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--maxWorkers=4', json.loads(result.stdout))

    def test_smaller_limits_serial_mode_and_test_arguments_survive(self):
        file = self.cli()
        self.assertIn('--maxWorkers=1', self.execute(file, ['--maxWorkers', '1']))
        self.assertEqual(self.execute(file, ['--runInBand', 'fixture']), ['--runInBand', 'fixture'])
        self.assertEqual(self.execute(file, ['-i', 'fixture']), ['-i', 'fixture'])
        result = self.execute(file, ['--maxWorkers=50', '--', '--maxWorkers=99'])
        self.assertEqual(result, ['--maxWorkers=4', '--', '--maxWorkers=99'])

    def test_symlink_and_absolute_cli_do_not_depend_on_path(self):
        file = self.cli()
        alias = self.root / 'node_modules/.bin/jest'
        alias.parent.mkdir()
        alias.symlink_to(file)
        self.assertIn('--maxWorkers=4', self.execute(alias))

    def test_jest_serial_aliases_booleans_and_end_of_options(self):
        file = self.cli()
        for args in [['--run-in-band'], ['--run-in-band=true'], ['-i=true']]:
            self.assertEqual(self.execute(file, args), args)
            self.assertEqual(worker_argv(['jest', *args], self.root, 4), ['jest', *args])
        for args in [['--runInBand', 'false'], ['--runInBand', '--no-runInBand'],
                     ['--run-in-band=false'], ['--', '--runInBand']]:
            result = self.execute(file, args)
            self.assertIn('--maxWorkers=4', result)
            self.assertIn('--maxWorkers=4', worker_argv(['jest', *args], self.root, 4))

    def test_package_identity_and_entrypoint_both_required(self):
        file = self.cli('ordinary-app', 'bin/jest.js')
        self.assertEqual(self.execute(file, ['--maxWorkers=22']), ['--maxWorkers=22'])
        helper = self.cli('jest', 'scripts/read.js')
        self.assertEqual(self.execute(helper, ['--maxWorkers=22']), ['--maxWorkers=22'])

    def test_existing_node_options_and_output_exit_status_preserved(self):
        preload = self.root / 'existing preload.cjs'
        preload.write_text('process.env.FIXTURE_PRELOADED="yes";')
        file = self.cli()
        file.write_text('console.log(process.env.FIXTURE_PRELOADED); console.error("fixture stderr"); process.exitCode=7;')
        env = {**self.env, 'NODE_OPTIONS': '--require=' + json.dumps(str(preload))}
        result = subprocess.run(['node', str(file)], env=worker_environment(env, 2), capture_output=True, text=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (7, 'yes\n', 'fixture stderr\n'))

    def test_vitest_minimum_and_playwright_only_test_mode(self):
        vitest = self.cli('vitest', 'vitest.mjs')
        result = self.execute(vitest, ['run', '--minWorkers=8', '--maxWorkers=12'], workers=2)
        self.assertIn('--maxWorkers=2', result)
        self.assertIn('--minWorkers=2', result)
        self.assertEqual(self.execute(vitest, ['-w', '--max-workers=1', '--min-workers=8']),
                         ['-w', '--maxWorkers=1', '--minWorkers=1'])
        playwright = self.cli('playwright', 'cli.js')
        self.assertIn('--workers=2', self.execute(playwright, ['test', '-j', '10'], workers=2))
        self.assertEqual(self.execute(playwright, ['install']), ['install'])

    def test_nested_environment_keeps_tighter_limit_and_single_preload(self):
        env = worker_environment(self.env, 1)
        again = worker_environment(env, 4)
        self.assertEqual(again.get('MEMCAP_NODE_WORKERS'), '1')
        self.assertEqual(again.get('NODE_OPTIONS'), env.get('NODE_OPTIONS'))
        self.assertIn('--maxWorkers=1', self.execute(self.cli(), env=again))

    def test_unmanaged_node_is_unchanged(self):
        file = self.cli()
        result = subprocess.run(['node', str(file)], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout), (0, '[]\n'))

    def test_expo_entrypoint_limits_startup_only(self):
        file = self.cli('expo', 'bin/cli')
        self.assertEqual(self.execute(file, ['start', '--clear'], workers=2),
                         ['start', '--clear', '--max-workers=2'])
        self.assertEqual(self.execute(file, ['start', '--max-workers', '1']),
                         ['start', '--max-workers=1'])
        for args in [['config', '--json'], ['start', '--help'], ['--version']]:
            self.assertEqual(self.execute(file, args), args)
        helper = self.cli('ordinary-app', 'bin/cli')
        self.assertEqual(self.execute(helper, ['start']), ['start'])

    def test_low_headroom_launch_bounds_actual_nested_workers(self):
        from scheduler import Scheduler
        file = self.cli()
        output = self.root / 'launched.json'
        file.write_text('require("fs").writeFileSync(' + json.dumps(str(output)) + ', JSON.stringify(process.argv.slice(2)));')
        sample = lambda: dict(cap_kb=20*1048576, tracked_kb=0, available_kb=2*1048576,
                              pressure=2, fault=False, footprints={}, tracked_pids=[],
                              monotonic=time.monotonic(), boot_id='fixture')
        q = Scheduler(self.root / 'queue', sampler=sample, policy='adaptive', workers=4,
                      memory_gb=1, headroom_gb=2, max_pressure='yellow', poll=.02)
        with patch.dict(os.environ, self.env, clear=True), patch('orphan_recovery.agent_identity', return_value={}):
            self.assertEqual(q.run(['/bin/bash', '-c', 'echo ready >/dev/null; node ' + shlex.quote(str(file))], wait=10), 0)
        self.assertIn('--maxWorkers=1', json.loads(output.read_text()))


class WorkerPercentage(unittest.TestCase):
    def test_small_percentage_is_not_increased_to_worker_cap(self):
        with patch('os.cpu_count', return_value=16):
            self.assertEqual(worker_argv(['jest', '--maxWorkers=10%'], Path('/tmp'), 4), ['jest', '--maxWorkers=1'])
            self.assertEqual(worker_argv(['jest', '-w10%'], Path('/tmp'), 4), ['jest', '--maxWorkers=1'])
            self.assertEqual(worker_argv(['vitest', '-w', 'fixture', '--max-workers=1'], Path('/tmp'), 4),
                             ['vitest', '-w', 'fixture', '--maxWorkers=1'])


class PendingIdentityTests(unittest.TestCase):
    def test_pending_identity_retry_respects_deadline_without_launch(self):
        import scheduler
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = scheduler.processes
            count = 0
            def identities():
                nonlocal count
                count += 1
                if count > 1:
                    raise subprocess.TimeoutExpired('ps', 10)
                return original()
            sample = lambda: dict(cap_kb=16*1048576, tracked_kb=0, available_kb=16*1048576,
                                  pressure=1, fault=False, footprints={}, tracked_pids=[])
            q = scheduler.Scheduler(root / 'queue', sampler=sample, poll=.01)
            marker = root / 'never'
            began = time.monotonic()
            with patch('scheduler.processes', side_effect=identities), patch('orphan_recovery.agent_identity', return_value={}), \
                 patch.dict(os.environ, MEMCAP_STATE_HOME=str(root / 'state'), MEMCAP_CONFIG_HOME=str(root / 'config'), MC_DRY_RUN='1'):
                self.assertEqual(q.run([sys.executable, '-c', f'open({str(marker)!r},"w").close()'], wait=.05), 75)
            self.assertFalse(marker.exists())
            self.assertLess(time.monotonic() - began, 2)
            self.assertEqual(json.loads((root / 'queue/jobs.json').read_text())['jobs'], [])

    def test_transient_ps_timeout_does_not_discard_waiting_command(self):
        import scheduler
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = scheduler.processes
            count = 0
            def identities():
                nonlocal count
                count += 1
                if count == 2:
                    raise subprocess.TimeoutExpired('ps', 10)
                return original()
            sample = lambda: dict(cap_kb=16*1048576, tracked_kb=0, available_kb=16*1048576,
                                  pressure=1, fault=False, footprints={}, tracked_pids=[])
            q = scheduler.Scheduler(root / 'queue', sampler=sample, poll=.01)
            marker = root / 'once'
            with patch('scheduler.processes', side_effect=identities), patch('orphan_recovery.agent_identity', return_value={}), \
                 patch.dict(os.environ, MEMCAP_STATE_HOME=str(root / 'state'), MEMCAP_CONFIG_HOME=str(root / 'config'), MC_DRY_RUN='1'):
                self.assertEqual(q.run([sys.executable, '-c', f'open({str(marker)!r},"a").write("x")'], wait=3), 0)
            self.assertEqual(marker.read_text(), 'x')


if __name__ == '__main__':
    unittest.main()
