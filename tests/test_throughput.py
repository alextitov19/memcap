"""Productivity regressions use bounded temporary fixtures, never host jobs."""
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from throughput import pending_request, capacity_progress, capacity_message
from command_stages import split_command, native_command
from workload_estimates import source_identity
from analytics_events import make_event
from analytics_reports import summarize
from analytics_store import Store, read_rows

GIB = 1048576


class ThroughputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, MEMCAP_STATE_HOME=str(self.root / 'state'), MC_DRY_RUN='1')
        self.env.start()
        self.addCleanup(self.env.stop)

    def row(self, event, seq, **fields):
        return make_event(event, fields, b'x' * 32, 'a' * 32, seq,
                          build='b' * 64, policy='c' * 64, boot='d' * 32,
                          wall=1000 + seq, mono=100 + seq)

    def test_pending_all_night_is_visible_and_not_a_started_job(self):
        rows = [self.row('queued', 1, job='stuck'), self.row('sample', 36001, pressure=2)]
        result = summarize(rows)
        self.assertEqual(result['pending']['age_ms']['max'], 36000000)
        self.assertEqual(result['jobs']['started'], 0)
        self.assertEqual(result['queue_wait_ms']['n'], 0)

    def test_monitoring_does_not_improve_project_wait_statistics(self):
        rows = []
        for i, purpose, wait in [(1, 'project', 1132000), (4, 'monitoring', 2)]:
            rows += [self.row('admitted', i, job=str(i), purpose=purpose, queue_wait_ms=wait),
                     self.row('completed', i+1, job=str(i), purpose=purpose, runtime_ms=4078, exit_code=0)]
        result = summarize(rows)
        self.assertEqual(result['queue_wait_ms']['median'], 1132000)
        self.assertEqual(result['monitoring_jobs']['completed'], 1)

    def test_lifecycle_survives_raw_eviction_and_reads_do_not_duplicate(self):
        directory = self.root / 'analytics'
        with Store(directory) as store:
            store.insert(self.row('queued', 1, job='one'))
            store.insert(self.row('admitted', 2, job='one', queue_wait_ms=1000))
            store.insert(self.row('completed', 3, job='one', runtime_ms=100, exit_code=0))
            store.db.execute('DELETE FROM events WHERE event != "completed"')
        rows, health = read_rows(directory)
        self.assertEqual(len(rows), 3)
        self.assertEqual(health['job_checkpoints'], 3)
        self.assertEqual(summarize(rows)['queue_wait_ms']['median'], 1000)

    def test_worker_change_only_lowers_with_complete_exact_evidence(self):
        job = dict(elastic=True, estimate_key='four', memory_kb=4*GIB)
        exact = dict(complete_runs=3, peaks_kb=[GIB // 2]*3)
        self.assertEqual(pending_request(job, 'one', GIB, exact), GIB)
        for row in ({}, dict(complete_runs=0), dict(complete_runs=3, peaks_kb=[3*GIB])):
            self.assertEqual(pending_request(job, 'one', GIB, row), 4*GIB)
        self.assertEqual(pending_request(job, 'four', GIB, exact), 4*GIB)
        self.assertEqual(pending_request({**job, 'elastic': False}, 'one', GIB, exact), 4*GIB)

    def test_source_changes_and_incomplete_scan_never_reuse_small_estimate(self):
        source = self.root / 'test.go'
        source.write_text('small')
        first = source_identity(self.root)
        self.assertEqual(first, source_identity(self.root))
        source.write_text('large')
        self.assertNotEqual(first, source_identity(self.root))
        self.assertNotEqual(source_identity(self.root, max_bytes=1), source_identity(self.root, max_bytes=1))

    def test_headroom_impasse_retains_job_and_requires_continuous_evidence(self):
        job = dict(memory_kb=3*GIB, status='waiting')
        decision = dict(reason='headroom', request_kb=3*GIB, headroom_kb=GIB//2, available_kb=2*GIB)
        job['admission'] = decision
        for now in (0, 60, 120):
            capacity_progress(job, [job], decision, now)
        self.assertTrue(job['capacity_progress']['stalled'])
        self.assertIn('3.50 GiB', capacity_message(job))
        self.assertEqual(job['status'], 'waiting')
        capacity_progress(job, [dict(status='running', resource='')], decision, 121)
        self.assertNotIn('capacity_progress', job)
        capacity_progress(job, [job], decision, 200)
        capacity_progress(job, [job], decision, 400)
        self.assertFalse(job['capacity_progress']['stalled'])

    def test_literal_stages_keep_cwd_and_boolean_control_flow(self):
        result = split_command('cd backend && go test ./pkg ; git status', '/bin/memcap', 'scope')
        self.assertTrue(result.startswith('cd backend &&'))
        self.assertIn('-- go test ./pkg ;', result)
        self.assertIn('-- git status', result)
        for command in ['go test "$PKG" && git status', 'go test ./... | cat', 'x=$(go test ./...); echo "$x"', 'go test ./... > out; cat out', 'false & go test ./...']:
            self.assertIsNone(split_command(command, '/bin/memcap'))
        for command in ['read answer; go test ./pkg', 'shopt -s failglob; go test ./pkg', 'cat ~/file; go test ./pkg']:
            self.assertIsNone(split_command(command, '/bin/memcap'))

    def test_explicit_wrappers_classify_cleanup_and_remote_reads_natively(self):
        for argv in [['docker', 'rm', '-f', 'fixture'], ['docker', 'compose', 'down'], ['aws', 'ssm', 'get-parameter', '--name', 'fixture']]:
            self.assertTrue(native_command(argv, self.root))
        self.assertFalse(native_command(['go', 'test', './...'], self.root))

    def test_script_is_executed_from_validated_bytes_and_dynamic_scripts_stay_whole(self):
        from command_stages import staged_script
        script = self.root / 'checks.sh'
        script.write_text('#!/bin/bash\nset -e\ngit status\ngo test ./pkg\n')
        argv = staged_script(['bash', str(script)], self.root, '/bin/memcap')
        self.assertEqual(argv[:2], ['bash', '-c'])
        self.assertIn('-- go test ./pkg', argv[2])
        script.write_text('echo "multi\nline"\ngo test ./pkg\n')
        self.assertIsNone(staged_script(['bash', str(script)], self.root, '/bin/memcap'))
        from command_stages import stable_shell
        self.assertFalse(stable_shell('/bin/zsh'))
        self.assertFalse(stable_shell('/bin/bash', login=True))
        with patch.dict(os.environ, BASH_ENV='startup.sh'):
            self.assertFalse(stable_shell('/bin/bash'))

    def test_notification_wait_has_background_deadline_but_ordinary_wait_stays_prompt(self):
        from scheduler_policy import hook_response
        payload = dict(hook_event_name='PreToolUse', tool_name='Bash', session_id='s', cwd=str(self.root),
                       tool_input={'command': '/bin/memcap wait abcdef12 --until-complete'})
        output = hook_response(payload, '/bin/memcap', 'claude')['hookSpecificOutput']['updatedInput']
        self.assertTrue(output['run_in_background'])
        self.assertEqual(output['timeout'], 86400000)
        payload['tool_input']['command'] = '/bin/memcap wait abcdef12 --timeout 60'
        result = hook_response(payload, '/bin/memcap', 'claude')
        self.assertFalse(result.get('hookSpecificOutput', {}).get('updatedInput', {}).get('run_in_background', False))

    def test_native_wrapper_preserves_output_exit_cwd_without_creating_queue(self):
        root = Path(__file__).resolve().parents[1]
        config = self.root / 'config'
        config.mkdir()
        result = subprocess.run([str(root / 'bin/memcap'), 'run', '--cwd', str(self.root), '--',
                                 '/bin/sh', '-c', 'printf one; pwd; exit 7'],
                                env={**os.environ, 'MEMCAP_CONFIG_HOME': str(config), 'QUEUE_POLICY': 'adaptive'},
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout, 'one' + str(self.root.resolve()) + '\n')
        self.assertFalse((self.root / 'state/memcap/queue/jobs.json').exists())

    def test_explicit_reservation_still_receives_managed_host_deadline(self):
        from scheduler_policy import hook_response
        payload = dict(hook_event_name='PreToolUse', tool_name='Bash', cwd=str(self.root),
                       tool_input={'command': 'memcap run --memory 2 -- git status'})
        result = hook_response(payload, '/bin/memcap', 'claude')['hookSpecificOutput']['updatedInput']
        self.assertTrue(result['run_in_background'])

    def test_binding_wrapper_keeps_globs_and_child_session_flags_literal(self):
        from session_identity import bind_runner_text
        text = 'memcap run --session-key old -- shellcheck libexec/*.sh --session-key child'
        result = bind_runner_text(text, 'new')
        self.assertIn('shellcheck libexec/*.sh --session-key child', result)
        self.assertNotIn("'libexec/*.sh'", result)
        self.assertIn('--session-key new', result)
        self.assertNotIn('--session-key old', result)

    def test_environment_wrapper_keeps_private_scope_and_background_wait(self):
        from scheduler_policy import hook_response
        payload = dict(hook_event_name='PreToolUse', tool_name='Bash', session_id='parent', agent_id='child', cwd=str(self.root),
                       tool_input={'command': 'memcap environment run --memory 12 --compose fixture.yml -- go test ./...'})
        result = hook_response(payload, '/bin/memcap', 'claude')['hookSpecificOutput']['updatedInput']
        self.assertTrue(result['run_in_background'])
        self.assertIn('environment run --session-key ', result['command'])
        self.assertIn('--memory 12 --compose fixture.yml -- go test ./...', result['command'])


if __name__ == '__main__':
    unittest.main()
