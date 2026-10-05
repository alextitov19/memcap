"""Release reproductions use synthetic identities and isolated state only."""
import json
import io
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from scheduler import Scheduler
GIB = 1048576


class Productivity027Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, MEMCAP_STATE_HOME=str(self.root/'state'),
                         MEMCAP_CONFIG_HOME=str(self.root/'config'), MC_DRY_RUN='1')
        env.start()
        self.addCleanup(env.stop)

    def test_environment_help_retains_native_execution_and_route(self):
        from scheduler_policy import hook_response
        for command in ('memcap environment run --help', 'memcap environment run -h'):
            observation = {}
            original = dict(command=command, timeout=10000, run_in_background=False)
            result = hook_response(dict(hook_event_name='PreToolUse', tool_name='Bash',
                                        cwd=str(self.root), tool_input=original),
                                   '/opt/homebrew/opt/memcap/bin/memcap', 'claude', observation)
            updated = result.get('hookSpecificOutput', {}).get('updatedInput', original)
            self.assertFalse(updated['run_in_background'])
            self.assertEqual(updated['timeout'], 10000)
            self.assertEqual(observation['route'], 'native')

    def test_sampler_uses_explicit_interpreter_and_preserves_bridge_contract(self):
        from scheduler import ROOT, QueueError, sample_host
        replies = [(0, '20971520 100 200 1 0\n17 42 1\n'),
                   (75, ''), (0, 'malformed\n')]
        for code, output in replies:
            with patch('scheduler.subprocess.run', return_value=subprocess.CompletedProcess(
                    [], code, output)) as run:
                if code or output == 'malformed\n':
                    with self.assertRaises(QueueError):
                        sample_host()
                else:
                    sample = sample_host()
                    self.assertEqual(sample['footprints'], {'17': 42})
                    self.assertEqual(sample['tracked_pids'], [17])
                self.assertEqual(run.call_args.args[0],
                                 ['/bin/bash', str(ROOT/'bin/memcap'), '_queue-sample'])
                self.assertEqual(run.call_args.kwargs['timeout'], 25)

    def test_hook_migration_recognizes_only_owned_interpreter_forms(self):
        from integrate import ours
        for prefix in ('', '/bin/bash '):
            for action in ('feedback', 'feedback --wait', 'queue-hook claude', 'queue-hook codex'):
                self.assertTrue(ours(dict(type='command', command=prefix+'/opt/memcap '+action)))
        for command in ('/bin/bash -c "memcap feedback"',
                        '/bin/bash /opt/another feedback',
                        '/bin/bash /opt/memcap feedback && echo unrelated',
                        'bash /opt/memcap feedback'):
            self.assertFalse(ours(dict(type='command', command=command)))

    def test_kernel_growth_has_same_boot_coverage_and_signed_deltas(self):
        from analytics_reports import kernel_growth
        field = 'kernel_data_1024_inuse_kb'
        def row(t, value, **extra):
            return dict(wall=1000+t, mono=t, boot='a'*32, build='b'*64,
                        policy='c'*64, **{field: value}, **extra)
        data = [row(0, 100), row(60, 160), row(120, 130)]
        result = kernel_growth(data)[field]
        self.assertEqual(result['observed_seconds'], 120)
        self.assertEqual(result['net_change_kb'], 30)
        self.assertEqual(result['net_kb_per_hour'], 900)
        self.assertEqual(result['decreasing_intervals'], 1)
        self.assertIsNone(kernel_growth([])[field]['net_kb_per_hour'])
        for second in (dict(row(60, 160), boot='d'*32),
                       dict(row(60, 160), build='e'*64),
                       dict(row(60, 160), policy='f'*64),
                       row(600, 160), row(60, 160, clock_uncertain=1)):
            self.assertEqual(kernel_growth([data[0], second])[field]['observed_seconds'], 0)

    def test_environment_real_work_still_uses_managed_execution(self):
        from scheduler_policy import hook_response
        observation = {}
        result = hook_response(dict(hook_event_name='PreToolUse', tool_name='Bash',
            cwd=str(self.root), tool_input=dict(command='memcap environment run --memory 2 --compose compose.yaml -- go test ./...',
                                               timeout=10000)),
            '/opt/homebrew/opt/memcap/bin/memcap', 'claude', observation)
        self.assertEqual(observation['route'], 'managed')
        self.assertTrue(result['hookSpecificOutput']['updatedInput']['run_in_background'])

    def test_heredoc_with_output_redirect_is_data_not_a_build(self):
        from demand_policy import classify
        command = "cat <<'DOC' > notes.txt\ngo build ./...\nDOC\n"
        self.assertEqual(classify(command, self.root).kind, 'light')
        self.assertEqual(classify(command + 'go build ./...\n', self.root).kind, 'heavy')
        self.assertEqual(classify("bash <<'SCRIPT' > output.txt\ngo build ./...\nSCRIPT\n", self.root).kind, 'heavy')
        for prefix in ('echo "cat <<DOC example"', '# documentation <<DOC'):
            self.assertEqual(classify(prefix+'\ngo build ./...\n', self.root).kind, 'heavy')

    def test_help_argument_passed_to_real_environment_work_is_not_native(self):
        from demand_policy import classify
        self.assertEqual(classify('memcap environment run --memory 2 --compose fixture.yaml -- go test ./... --help', self.root).kind, 'heavy')

    def test_literal_system_shell_and_exec_reach_compiler_scope(self):
        from compiler_commands import resolve
        for shell in ('bash', 'sh', '/bin/bash', '/bin/sh'):
            diagnostics = {}
            result = resolve([shell, '-c', 'exec go build ./...'], self.root,
                             {'PATH':'/bin:/usr/bin'}, diagnostics,
                             lambda name, path=None:'/bin/'+name)
            self.assertIsNotNone(result, diagnostics)
            self.assertEqual(result[0], ['go', 'build', './...'])

    def test_custom_shell_or_startup_injection_never_reaches_compiler_scope(self):
        from compiler_commands import resolve
        for shell, env, found in [('bash', {}, '/custom/bash'),
                                  ('/bin/bash', {'BASH_ENV':'custom'}, '/bin/bash'),
                                  ('bash', {'BASH_FUNC_go%%':'() { custom; }'}, '/bin/bash')]:
            result = resolve([shell, '-c', 'exec go build ./...'], self.root, env, {},
                             lambda name, path=None:found)
            self.assertIsNone(result)

    def fixture(self):
        queue = Scheduler(self.root/'queue', sampler=lambda:{}, policy='adaptive', memory_gb=1)
        job = dict(id='a'*32, status='running', owner=99, group=10, elastic=True,
                   members={'10':'leader'}, footprint_members={'10':'leader'},
                   memory_kb=GIB, reservation_kb=GIB, start_monotonic=100,
                   orphaned=False, learning_protocol=2)
        return queue, job

    def sample(self, at, child=False):
        identities = {'10':'leader', **({'11':'child'} if child else {})}
        footprints = {pid:GIB//8 for pid in identities}
        return dict(complete=True, peak_kb=sum(footprints.values()), identities=identities,
                    footprints=footprints, at=at, missing=0, fault=0, duration_ms=1)

    def test_child_born_after_probe_can_be_fully_measured_next_probe(self):
        from job_observation import complete_at
        queue, job = self.fixture()
        data = {'jobs':[job]}
        queue.observe_owned(data, job, self.sample(101))
        job['members']['11'] = job['footprint_members']['11'] = 'child'
        queue.observe_owned(data, job, self.sample(101.25))
        self.assertFalse(complete_at(job['owned_observation'], 101.3))
        self.assertFalse(job['measurement_complete'])
        self.assertGreaterEqual(job['reservation_kb'], GIB)
        queue.observe_owned(data, job, self.sample(101.5, child=True))
        self.assertTrue(complete_at(job['owned_observation'], 101.6))

    def test_unmeasured_child_exit_or_pid_reuse_never_certifies_complete(self):
        from job_observation import complete_at
        for reused in (False, True):
            queue, job = self.fixture()
            data = {'jobs':[job]}
            queue.observe_owned(data, job, self.sample(101))
            job['members']['11'] = job['footprint_members']['11'] = 'child'
            queue.observe_owned(data, job, self.sample(101.25))
            job['members'].pop('11'); job['footprint_members'].pop('11')
            sample = self.sample(101.5)
            if reused:
                job['members']['11'] = job['footprint_members']['11'] = 'reused'
                sample['identities']['11'] = 'reused'
                sample['footprints']['11'] = 1
            queue.observe_owned(data, job, sample)
            queue.observe_owned(data, job, {**sample, 'at':101.75})
            self.assertFalse(complete_at(job['owned_observation'], 102))

    def test_later_incomplete_or_out_of_order_probe_cannot_discharge_new_child(self):
        from job_observation import complete_at
        queue, job = self.fixture()
        data = {'jobs':[job]}
        queue.observe_owned(data, job, self.sample(101))
        job['members']['11'] = job['footprint_members']['11'] = 'child'
        queue.observe_owned(data, job, self.sample(101.25))
        queue.observe_owned(data, job, self.sample(101.1, child=True))
        self.assertFalse(complete_at(job['owned_observation'], 101.3))
        queue.observe_owned(data, job, {**self.sample(101.5, child=True), 'complete':False, 'missing':1})
        queue.observe_owned(data, job, self.sample(101.75, child=True))
        self.assertFalse(complete_at(job['owned_observation'], 102))

    def test_complete_child_birth_runs_feed_a_fitting_compiler_admission(self):
        from job_observation import complete_at
        from compiler_profiles import record_profile
        from admission import decide
        (self.root/'go.mod').write_text('module fixture.test\n')
        (self.root/'main.go').write_text('package main\n')
        compiler = self.root/'go'
        compiler.write_text('fixture metadata only')
        data = {'jobs':[], 'compiler_profiles':{}}
        env = dict(HOME=str(self.root), PATH='/bin:/usr/bin', GOENV='off', GOWORK='off',
                   MEMCAP_STATE_HOME=str(self.root/'state'),
                   MEMCAP_CONFIG_HOME=str(self.root/'config'), MC_DRY_RUN='1')
        def lookup(name, path=None):
            return '/bin/bash' if name == 'bash' else str(compiler)
        with patch.dict(os.environ, env, clear=True), patch('shutil.which', side_effect=lookup), patch('time.time', return_value=103):
            queue, _ = self.fixture()
            argv = ['bash', '-c', 'exec go build ./...']
            for _ in range(3):
                _, request = queue.demand(argv, self.root, 1, data)
                self.assertIsNotNone(queue.compiler_profile)
                _, job = self.fixture()
                queue.observe_owned(data, job, self.sample(101))
                job['members']['11'] = job['footprint_members']['11'] = 'child'
                queue.observe_owned(data, job, self.sample(101.25))
                queue.observe_owned(data, job, self.sample(101.5, child=True))
                state = job['owned_observation']
                self.assertTrue(complete_at(state, 101.6))
                record_profile(data['compiler_profiles'], queue.compiler_profile,
                               state['peak_kb'], complete_at(state, 101.6), 102)
            with patch('time.time', return_value=103):
                _, request = queue.demand(argv, self.root, 1, data)
        self.assertEqual(request, GIB//2)
        sample = dict(fault=False, pressure=2, tracked_kb=0, cap_kb=20*GIB,
                      available_kb=int(1.2*GIB), footprints={}, tracked_pids=[], monotonic=110)
        policy = dict(mode='adaptive', allowed_pressure=(1,2), max_jobs=4, headroom_kb=GIB//2)
        control = dict(now=110, healthy_since=100, last_start=100)
        self.assertFalse(decide(policy, sample, control, [], dict(memory_kb=GIB, resource=''))['allow'])
        self.assertTrue(decide(policy, sample, control, [], dict(memory_kb=request, resource=''))['allow'])

    def test_negative_controls_fail_at_assertions(self):
        from job_observation import accumulate
        def poison_birth(state, sample, current):
            if any(sample.get('identities', {}).get(pid) != start for pid, start in current.items()):
                sample = {**sample, 'complete':False}
            return accumulate(state, sample), sample['complete']
        controls = [
            ('test_child_born_after_probe_can_be_fully_measured_next_probe',
             'job_observation.reconcile', poison_birth),
            ('test_busy_probe_publication_is_consumed_within_bounded_handoff',
             'scheduler_metrics.shared_sample', lambda *args:{'busy':True}),
            ('test_shutdown_cleanup_still_rejects_live_mobile_tooling_or_nonterminal_devices',
             'idle_gc.simulators_clear', lambda *_:True),
            ('test_zero_running_capacity_stall_is_first_in_wait_summary',
             'throughput.wait_summary', lambda *_args, **_kwargs:'pending; keep waiting'),
        ]
        for name, target, replacement in controls:
            with self.subTest(guard=name), patch(target, replacement):
                result = unittest.TextTestRunner(stream=io.StringIO()).run(
                    unittest.TestSuite([Productivity027Tests(name)]))
                self.assertEqual(result.errors, [])
                self.assertEqual(len(result.failures), 1)

    def test_zero_running_capacity_stall_is_first_in_wait_summary(self):
        from throughput import wait_summary
        queue, job = self.fixture()
        job.update(status='waiting', elastic=True, memory_kb=2*GIB,
                   admission=dict(reason='headroom', available_kb=GIB,
                                  request_kb=2*GIB, headroom_kb=GIB//2),
                   capacity_progress=dict(stalled=True))
        message = wait_summary('abcdefgh', [job], now=200)
        self.assertIn('capacity', message[:100].lower())
        self.assertIn('2.50 GiB', message)
        self.assertIn('1.00 GiB', message)
        self.assertIn('pending', message)

    def test_sampling_metrics_use_decisions_as_denominator_and_keep_legacy_unknown(self):
        from analytics_events import make_event
        from analytics_reports import summarize
        def row(event, seq, **fields):
            return make_event(event, fields, b'x'*32, 'a'*32, seq,
                              build='b'*64, policy='c'*64, boot='d'*32,
                              wall=1000+seq, mono=seq)
        old = summarize([row('completed', 1, job='old', exit_code=0)])
        self.assertIsNone(old['learning_effectiveness'].get('sampling_busy_fraction'))
        events = [row('admitted', 1, job='new', queue_wait_ms=10000),
                  row('completed', 2, job='new', exit_code=0, sampling_busy_count=2,
                      sampling_decisions=10, sampling_handoff_count=3, sampling_handoff_ms=150,
                      observation_pending_seen=4, observation_pending_resolved=3)]
        report = summarize(events)['learning_effectiveness']
        self.assertEqual(report.get('sampling_busy_fraction'), 0.2)
        self.assertEqual(report.get('sampling_handoff_count'), 3)
        self.assertEqual(report.get('pending_observations_resolved'), 3)

    def test_busy_probe_publication_is_consumed_within_bounded_handoff(self):
        from scheduler_metrics import shared_sample
        root = self.root/'samples'; root.mkdir()
        clock = [100.0]
        def advance(seconds):
            clock[0] += seconds
            if clock[0] >= 100.1:
                (root/'sample.json').write_text(json.dumps(dict(key='same',
                    sample=dict(monotonic=100.1, pressure=2, fault=False))))
        with patch('scheduler_metrics.time.monotonic', side_effect=lambda:clock[0]), \
             patch('scheduler_metrics.time.sleep', side_effect=advance), \
             patch('scheduler_metrics.fcntl.flock', side_effect=BlockingIOError()):
            sample = shared_sample(root, 'same', lambda:self.fail('duplicate probe'))
        self.assertFalse(sample.get('busy', False))
        self.assertLessEqual(clock[0], 100.5)
        self.assertEqual(sample['pressure'], 2)

    def test_busy_probe_handoff_never_uses_expired_or_incompatible_sample(self):
        from scheduler_metrics import shared_sample
        for key, stamp in [('other', 100.0), ('same', 98.0)]:
            root = self.root/('samples-'+key); root.mkdir()
            (root/'sample.json').write_text(json.dumps(dict(key=key, sample=dict(monotonic=stamp))))
            clock = [100.0]
            with patch('scheduler_metrics.time.monotonic', side_effect=lambda:clock[0]), \
                 patch('scheduler_metrics.time.sleep', side_effect=lambda seconds:clock.__setitem__(0, clock[0]+seconds)), \
                 patch('scheduler_metrics.fcntl.flock', side_effect=BlockingIOError()):
                sample = shared_sample(root, 'same', lambda:self.fail('duplicate probe'))
            self.assertTrue(sample['busy'])
            self.assertLessEqual(clock[0], 100.5)

    def test_shutdown_in_progress_allows_only_abandoned_idle_runtime_leaves(self):
        import idle_gc
        from test_idle_gc import row
        table = {'11':row(1, '/Library/Developer/CoreSimulator/Volumes/fixture/iOS.simruntime/Contents/Resources/RuntimeRoot/usr/libexec/helper')}
        net = dict(known=True, connected=[], listeners=[])
        gc = idle_gc.Collector(self.root/'gc-state', grace=600)
        devices = json.dumps({'devices':{'runtime':[{'state':'Shutting Down'}]}})
        with patch('idle_gc.subprocess.run', return_value=subprocess.CompletedProcess([], 0, devices)):
            clear = idle_gc.simulators_clear(table)
            self.assertTrue(clear)
            self.assertEqual(gc.scan(table, net, clear, 100), [])
            self.assertEqual(gc.scan(table, net, clear, 699), [])
            ready = gc.scan(table, net, clear, 700)
            self.assertEqual([c['pid'] for c in ready], ['11'])
            self.assertTrue(gc.authorize('11', table, net, clear, 701))
            self.assertFalse(gc.authorize('11', table, {**net, 'connected':['11']}, clear, 702))

    def test_shutdown_cleanup_still_rejects_live_mobile_tooling_or_nonterminal_devices(self):
        import idle_gc
        from test_idle_gc import row
        for state in ('Booted', 'Booting', 'Creating', 'Unknown'):
            devices = json.dumps({'devices':{'runtime':[{'state':state}]}})
            with patch('idle_gc.subprocess.run', return_value=subprocess.CompletedProcess([], 0, devices)):
                self.assertFalse(idle_gc.simulators_clear({}))
        devices = json.dumps({'devices':{'runtime':[{'state':'Shutting Down'}]}})
        for command in ('/usr/libexec/launchd_sim', 'xcodebuild -project fixture', 'xcrun simctl boot fixture', 'maestro test fixture'):
            with patch('idle_gc.subprocess.run', return_value=subprocess.CompletedProcess([], 0, devices)):
                self.assertFalse(idle_gc.simulators_clear({'20':row(1, command)}))

    def test_shutdown_cleanup_retains_reactivated_reused_foreign_or_parent_processes(self):
        import idle_gc
        from test_idle_gc import row
        command = '/Library/Developer/CoreSimulator/Volumes/fixture/iOS.simruntime/Contents/Resources/RuntimeRoot/usr/libexec/helper'
        base = {'11':row(1, command)}
        net = dict(known=True, connected=[], listeners=[])
        cases = [({**base, '11':{**base['11'], 'start':'reused'}}, net, True),
                 ({**base, '11':{**base['11'], 'uid':os.getuid()+1}}, net, True),
                 ({**base, '12':row(11, 'child')}, net, True),
                 (base, {**net, 'known':False}, True),
                 (base, net, False)]
        for index, (table, network, clear) in enumerate(cases):
            gc = idle_gc.Collector(self.root/('gc-'+str(index)), grace=600)
            gc.scan(base, net, True, 100)
            self.assertEqual(len(gc.scan(base, net, True, 700)), 1)
            self.assertFalse(gc.authorize('11', table, network, clear, 701))
        gc = idle_gc.Collector(self.root/'gc-reactivate', grace=600)
        gc.scan(base, net, True, 100)
        self.assertEqual(gc.scan(base, net, False, 699), [])
        self.assertEqual(gc.scan(base, net, True, 700), [])
        self.assertEqual(gc.scan(base, net, True, 1299), [])


if __name__ == '__main__':
    unittest.main()
