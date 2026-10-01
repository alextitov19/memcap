"""Synthetic admission evidence; no host probes, enforcement, or real workloads."""
import copy
import json
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from scheduler import Scheduler
from scheduler_policy import classify_shell

GIB = 1048576


def job(ident, small=False, **extra):
    return dict(id=ident, status='waiting', resource='', memory_kb=GIB // 2 if small else 4 * GIB,
                members={}, cwd='/fixture', session_key=ident, enqueued=990,
                lane_version=1, fairness_version=2, small_candidate=small, elastic=True,
                estimate_source=3, estimate_complete_runs=3, **extra)


def sample(**extra):
    return dict(cap_kb=16 * GIB, tracked_kb=0, available_kb=16 * GIB,
                fault=False, pressure=1, footprints={}, tracked_pids=[], **extra)


class LaneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        environment = patch.dict('os.environ', {
            'MEMCAP_CONFIG_HOME': str(Path(self.tmp.name) / 'config'),
            'MEMCAP_STATE_HOME': str(Path(self.tmp.name) / 'state'),
            'MC_DRY_RUN': '1',
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.q = Scheduler(Path(self.tmp.name), sampler=sample, max_jobs=4, headroom_gb=2)

    def test_small_passes_heavy_with_equal_session_priority(self):
        jobs = [job('heavy'), job('small', True)]
        before = copy.deepcopy(jobs)
        self.assertEqual(self.q.next_waiter(jobs, '', sample(), now=1000), 'small')
        self.assertEqual(jobs, before, 'selection must not release reservations or mutate leases')

    def test_session_turns_take_precedence_over_repeated_small_priority(self):
        data = {'jobs': [job('heavy'), job('small', True)]}
        chosen = []
        for _ in range(4):
            ident = self.q.next_waiter(data['jobs'], '', sample(), data.get('session_turns'),
                                       now=1000, lane_streak=data.get('small_streak', 0))
            chosen.append(ident)
            self.q.record_turn(data, next(j for j in data['jobs'] if j['id'] == ident))
        self.assertEqual(chosen, ['small', 'heavy', 'small', 'heavy'])

    def test_session_without_running_work_precedes_a_second_job(self):
        active = dict(job('active'), status='running', memory_kb=GIB,
                      session_key='busy/child-one')
        second = dict(job('second', True), session_key='busy/child-two')
        first = dict(job('first'), session_key='idle/child')
        self.assertEqual(self.q.next_waiter([active, second, first], '', sample(),
                                          {'busy': 1, 'idle': 9}, now=1000), 'first')

    def test_mixed_waiters_keep_a_compatible_selector_until_legacy_launches(self):
        old = dict(job('old'), fairness_version=1)
        small = job('small', True)
        self.assertEqual(self.q.next_waiter([old, small], '', sample(),
                                          {'small': 2}, now=1000), 'small')
        old['status'] = 'running'
        old['memory_kb'] = GIB
        fresh = dict(job('fresh'), session_key='idle')
        small['session_key'] = 'old'
        self.assertEqual(self.q.next_waiter([old, small, fresh], '', sample(),
                                          {'idle': 9}, now=1000), 'fresh')

    def test_pre_lane_waiter_preserves_original_rotation_during_upgrade(self):
        old = dict(job('old'), lane_version=0, fairness_version=0)
        small = job('small', True)
        self.assertEqual(self.q.next_waiter([old, small], '', sample(),
                                          {'small': 2}, now=1000), 'old')

    def test_round_robin_progress_across_eight_fitting_sessions(self):
        data = {'jobs': [dict(job(str(i), i % 2 == 0), session_key=str(i))
                         for i in range(8)]}
        order = []
        for _ in range(8):
            ident = self.q.next_waiter(data['jobs'], '', sample(),
                                      data.get('session_turns'), now=1000,
                                      lane_streak=data.get('small_streak', 0))
            order.append(ident)
            self.q.record_turn(data, next(j for j in data['jobs'] if j['id'] == ident))
        self.assertEqual(len(set(order)), 8, 'no session gets a repeat turn before every fitting peer')

    def test_small_lane_uses_available_global_slots_without_a_two_job_ceiling(self):
        jobs = [job('s1', True), job('s2', True)]
        for j in jobs:
            j['status'] = 'running'
        self.assertTrue(self.q.admissible(jobs, GIB // 2, '', sample(), lane='small')[0])
        self.assertTrue(self.q.admissible(jobs, GIB, '', sample())[0])
        jobs += [dict(job('h1'), status='running'), dict(job('h2'), status='running')]
        self.assertFalse(self.q.admissible(jobs, GIB // 2, '', sample(), lane='small')[0])

    def test_small_lane_still_obeys_shared_memory_pressure_and_reservations(self):
        for changes in ({'pressure': 4}, {'fault': True}, {'available_kb': GIB},
                        {'tracked_kb': 16 * GIB}):
            state = {**sample(), **changes}
            self.assertFalse(self.q.admissible([], GIB // 2, '', state, lane='small')[0])
        active = dict(job('large'), status='running', reservation_kb=14 * GIB)
        self.assertFalse(self.q.admissible([active], GIB // 2, '', sample(), lane='small')[0])

    def test_only_complete_exact_profiles_qualify_and_growth_upgrades(self):
        known = job('small', True)
        self.assertEqual(self.q.lane(known), 'small')
        for change in ({'estimate_source': 4}, {'estimate_complete_runs': 2},
                       {'resource': 'sim'}, {'small_candidate': False}, {'elastic': False},
                       {'lane_version': 0}, {'reservation_kb': 2 * GIB},
                       {'observed_peak_kb': GIB}, {'memory_kb': 2 * GIB}):
            self.assertEqual(self.q.lane({**known, **change}), 'heavy', change)

    def test_session_rotation_is_preserved_inside_small_lane(self):
        jobs = [job('heavy'), job('a', True), job('b', True)]
        self.assertEqual(self.q.next_waiter(jobs, '', sample(), {'heavy': 7, 'a': 5, 'b': 2}, now=1000), 'b')

    def test_new_demand_policy_has_only_heavy_queued_work(self):
        self.assertEqual(self.q.lane(dict(job('measured', True), demand_version=1)), 'heavy')

    def test_aged_heavy_accumulates_capacity_only_while_finite_jobs_can_drain(self):
        heavy = {**job('heavy'), 'memory_kb': 8 * GIB, 'enqueued': 900}
        small = job('small', True)
        running = dict(job('running', True), status='running', reservation_kb=8 * GIB)
        state = {**sample(), 'available_kb': 12 * GIB}
        self.assertEqual(self.q.next_waiter([heavy, small, running], '', state, now=1000), 'heavy')
        # No running finite work can drain: allow the fitting small request.
        running['resource'] = 'server'
        self.assertEqual(self.q.next_waiter([heavy, small, running], '', state, now=1000), 'small')

    def test_heavy_turn_is_available_with_two_running_small_jobs(self):
        active = [dict(job(str(i), True), status='running') for i in range(2)]
        self.assertEqual(self.q.next_waiter(active + [job('s', True), job('h')], '', sample(), now=1000, lane_streak=3), 'h')

    def test_script_content_change_invalidates_exact_memory_evidence(self):
        script = Path(self.tmp.name) / 'utility.py'
        script.write_text('print(1)\n')
        data = {}
        first, _ = self.q.demand([sys.executable, str(script)], script.parent, 1, data)
        same, _ = self.q.demand([sys.executable, str(script)], script.parent, 1, data)
        self.assertEqual(first, same)
        script.write_text('print(2)\n')
        second, _ = self.q.demand([sys.executable, str(script)], script.parent, 1, data)
        self.assertNotEqual(first, second)

    def test_unfingerprinted_interpreter_inputs_do_not_earn_small_priority(self):
        for executable in ('node', 'ruby', 'perl', 'php', 'lua', 'deno', 'env', 'uv', 'poetry'):
            with self.subTest(executable=executable):
                self.q.demand([executable, 'utility.script'], Path(self.tmp.name), 1, {})
                self.assertFalse(self.q.small_candidate)

    def test_ssm_and_search_are_direct_but_compound_build_is_managed(self):
        for command in ('aws ssm get-parameter --name /fixture --with-decryption', 'rg needle README.md'):
            self.assertEqual(classify_shell(command)[0], 'light')
        self.assertNotEqual(classify_shell('aws ssm get-parameter --name /fixture && npm run build')[0], 'light')

    def test_reports_keep_historical_lane_unknown_and_admission_lane_stable(self):
        from analytics_events import make_event
        from analytics_reports import summarize
        def row(event, seq, **fields):
            return make_event(event, fields, b'k' * 32, 'a' * 32, seq,
                              build='b' * 64, policy='c' * 64, boot='d' * 32,
                              wall=1000 + seq, mono=100 + seq)
        rows = [row('admitted', 1, job='small', lane_code=1, queue_wait_ms=10),
                row('reservation', 2, job='small', lane_code=2),
                row('completed', 3, job='small', lane_code=1, runtime_ms=100, exit_code=0),
                row('completed', 4, job='old', runtime_ms=100, exit_code=0),
                row('admitted', 5, job='heavy', lane_code=2, queue_wait_ms=5000)]
        report = summarize(rows)
        lanes = {r['lane']: r for r in report.get('delay_by_lane', [])}
        self.assertEqual(set(lanes), {'small', 'heavy', 'unknown'})
        self.assertEqual(lanes['small']['jobs'], 1)
        self.assertEqual(lanes['small']['queue_wait_ms']['median'], 10)
        self.assertEqual(lanes['heavy']['queue_wait_ms']['median'], 5000)

    def test_explicit_runner_retains_heavy_lane_even_with_small_history_and_executes_once(self):
        marker = Path(self.tmp.name) / 'result'
        script = Path(self.tmp.name) / 'utility.py'
        script.write_text(f"open({str(marker)!r}, 'a').write('once')\n")
        argv = [sys.executable, str(script)]
        self.q.policy = 'adaptive'
        self.q.poll = .02
        self.q.sampler = lambda: sample(monotonic=time.monotonic())
        with self.q.locked() as data:
            key, _ = self.q.demand(argv, script.parent.resolve(), self.q.workers, data)
            data['estimates'] = {key: dict(estimate_kb=GIB // 2, complete_runs=3, peaks_kb=[10000] * 3)}
            self.q.save(data)
        with patch('orphan_recovery.agent_identity', return_value={}):
            self.assertEqual(self.q.run(argv, cwd=script.parent, wait=8), 0)
        self.assertEqual(marker.read_text(), 'once')
        rows = [json.loads(line) for line in (Path(self.tmp.name) / 'events.jsonl').read_text().splitlines()]
        admitted = next(r for r in rows if r['event'] == 'admitted')
        self.assertEqual(admitted.get('lane_code'), 2)
        state = json.loads((Path(self.tmp.name) / 'jobs.json').read_text())
        self.assertEqual(state.get('small_streak'), 0)

    def test_changed_script_or_worker_profile_loses_small_priority_before_launch(self):
        for change in ('script', 'workers'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                script = root / 'utility.py'
                script.write_text('print("fixture")\n')
                argv = [sys.executable, str(script)]
                q = Scheduler(root / 'queue', sampler=sample, policy='adaptive', workers=2, poll=.02)
                with q.locked() as data:
                    key, _ = q.demand(argv, root.resolve(), 2, data)
                    data['estimates'] = {key: dict(estimate_kb=GIB // 2, complete_runs=3, peaks_kb=[10000] * 3)}
                    q.save(data)
                changed = []
                def changed_sample():
                    if not changed:
                        changed.append(True)
                        if change == 'script':
                            script.write_text(script.read_text() + '# changed after enqueue\n')
                        else:
                            q.workers = 1
                    return sample(monotonic=time.monotonic())
                q.sampler = changed_sample
                with patch('orphan_recovery.agent_identity', return_value={}):
                    self.assertEqual(q.run(argv, cwd=root, wait=8), 0)
                rows = [json.loads(line) for line in (root / 'queue/events.jsonl').read_text().splitlines()]
                self.assertEqual(next(r for r in rows if r['event'] == 'queued')['lane_code'], 2)
                self.assertEqual(next(r for r in rows if r['event'] == 'admitted')['lane_code'], 2)


if __name__ == '__main__':
    unittest.main()
