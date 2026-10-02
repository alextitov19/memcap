"""Release evidence and learning fixtures; no live probes or workloads."""
import gzip
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from analytics_events import make_event
from analytics_reports import summarize
from analytics_releases import snapshot, load, compare_snapshots
from scheduler import Scheduler
from throughput import capacity_progress, capacity_message

GIB = 1048576


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, MEMCAP_STATE_HOME=str(self.root / 'state'), MC_DRY_RUN='1')
        env.start()
        self.addCleanup(env.stop)

    def row(self, event, seq, **fields):
        return make_event(event, fields, b'x'*32, 'a'*32, seq,
                          build='b'*64, policy='c'*64, boot='d'*32,
                          wall=1000+seq, mono=100+seq)

    def test_completed_percentiles_do_not_hide_pending_or_include_cancelled_waits(self):
        rows = [self.row('admitted', 1, job='done', queue_wait_ms=10, explicit_memory=1),
                self.row('completed', 2, job='done', exit_code=2, runtime_ms=20, learning_complete=0),
                self.row('cancelled', 3, job='cancel', queue_wait_ms=999999),
                self.row('queued', 4, job='pending'),
                self.row('admitted', 5, job='running', queue_wait_ms=999999)]
        report = summarize(rows, now=2000)
        self.assertEqual(report['completed_evidence']['queue_wait_ms']['median'], 10)
        self.assertEqual(report['completed_evidence']['explicit_requests'], 1)
        self.assertEqual(report['completed_evidence']['diagnostics_jobs'], 0)
        self.assertEqual(report['pending']['age_ms']['max'], 996000)

    def test_snapshot_preserves_cutoff_privacy_versions_and_refuses_overwrite(self):
        rows = [self.row('queued', 1, job='crossing', runner_version=22000),
                self.row('stalled', 100, job='crossing', queue_wait_ms=99000),
                self.row('completed', 200, job='crossing', runtime_ms=10, exit_code=0),
                self.row('sample', 50, pressure=2)]
        rows[0]['command'] = 'PRIVATE_COMMAND'
        output = self.root / 'baseline'
        report = snapshot(rows, {}, output, since=1050, until=1150)
        self.assertEqual(report['summary']['pending']['observed'], 1)
        self.assertEqual(report['cohorts'][0]['version'], '0.22.0')
        self.assertEqual(report['summary']['jobs']['succeeded'], 0)
        contents = gzip.decompress((output / 'events.jsonl.gz').read_bytes())
        self.assertNotIn(b'PRIVATE_COMMAND', contents)
        self.assertEqual(len(contents.splitlines()), 3)
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        self.assertEqual((output / 'manifest.json').stat().st_mode & 0o777, 0o600)
        before = (output / 'manifest.json').read_bytes()
        with self.assertRaises(ValueError):
            snapshot([], {}, output, since=0)
        self.assertEqual((output / 'manifest.json').read_bytes(), before)
        link = self.root / 'link'
        link.symlink_to(self.root / 'absent')
        with self.assertRaises(ValueError):
            snapshot([], {}, link, since=0)
        (output / 'events.jsonl.gz').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            load(output)

    def test_release_diffs_keep_unknowns_and_old_builds_separate(self):
        rows = [self.row('admitted', 1, job='one', queue_wait_ms=100),
                self.row('completed', 2, job='one', exit_code=0, runtime_ms=20)]
        baseline, candidate = self.root / 'a', self.root / 'b'
        snapshot(rows, {}, baseline, since=0, until=2000)
        rows += [{**self.row('sample', 3, pressure=2, runner_version=23000), 'build': 'e'*64}]
        rows[0]['queue_wait_ms'] = 1000
        report = snapshot(rows, {}, candidate, since=0, until=2000)
        self.assertEqual(len(report['cohorts']), 2)
        self.assertIsNone(report['cohorts'][0]['version'])
        delta = compare_snapshots(baseline, candidate)
        self.assertEqual(delta['deltas']['completed_wait_median_ms']['delta'], 900)
        self.assertIsNone(delta['deltas']['red_fraction']['delta'])
        self.assertIsNone(delta['deltas']['completed_wait_p95_ms']['delta'])
        self.assertFalse(delta['causal'])

    def test_sampling_notice_preserves_last_valid_headroom(self):
        job = dict(memory_kb=4*GIB, elastic=False)
        decision = dict(reason='headroom', request_kb=4*GIB, headroom_kb=GIB//2, available_kb=2*GIB)
        for now in (0, 60, 120):
            capacity_progress(job, [job], decision, now)
        job['admission'] = dict(reason='sampling')
        capacity_progress(job, [job], job['admission'], 121)
        message = capacity_message(job)
        self.assertIn('4.50 GiB', message)
        self.assertIn('last measured 2.00 GiB', message)
        self.assertIn('fixed --memory', message)

    def test_learning_diagnostics_survive_both_allowlists(self):
        from scheduler_metrics import append_event
        directory = self.root / 'queue'
        directory.mkdir()
        fields = dict(learning_samples=2, learning_fault_samples=1,
                      learning_missing_samples=3, learning_detached_samples=4)
        append_event(directory, dict(event='completed', **fields))
        saved = json.loads((directory / 'events.jsonl').read_text())
        event = self.row('completed', 1, **fields)
        for key, value in fields.items():
            self.assertEqual(saved[key], value)
            self.assertEqual(event[key], value)

    def test_fixed_launch_trains_actual_workers_without_changing_floor(self):
        q = Scheduler(self.root / 'queue', policy='adaptive', workers=4)
        job = dict(id='a'*32, memory_kb=4*GIB, workers=4, elastic=False,
                   estimate_source=1, enqueued_monotonic=time.monotonic())
        data = dict(jobs=[job])
        fake = Mock(pid=987654)
        with (patch.object(q, 'allocation', return_value=1),
              patch.object(q, 'save'),
              patch('scheduler.processes', return_value={'987654': dict(group=987654, start='identity')}),
              patch('scheduler.subprocess.Popen', return_value=fake),
              patch('scheduler.os.write')):
            q.launch(['go', 'test', './pkg'], self.root, job, data)
        expected, _ = q.demand(['go', 'test', './pkg'], self.root, 1, data)
        self.assertEqual(job.get('estimate_key'), expected)
        self.assertEqual(job['memory_kb'], 4*GIB)
        self.assertEqual(job['estimate_source'], 1)
        self.assertFalse(job['elastic'])
        q.observe(data, dict(monotonic=time.monotonic()+1, footprints={'987654': 5*GIB}))
        learned = data['estimates'][expected]
        self.assertGreaterEqual(learned['estimate_kb'], 5*GIB*1.25)
        self.assertEqual(learned.get('complete_runs', 0), 0)
        q.observe(data, dict(monotonic=time.monotonic()+2, footprints={}))
        self.assertTrue(job['learning_incomplete'])
        self.assertEqual(job['learning_missing_samples'], 1)
        q.observe(data, dict(monotonic=time.monotonic()+3, fault=True))
        self.assertEqual(job['learning_fault_samples'], 1)

    def test_fixed_external_environments_cannot_teach_a_lower_process_only_estimate(self):
        from throughput import fixed_learning_scope
        self.assertTrue(fixed_learning_scope(['go', 'build', './cmd/server']))
        self.assertTrue(fixed_learning_scope(['/project/node_modules/.bin/tsc', '--noEmit']))
        for argv in (['go', 'test', './...'], ['docker', 'build', '.'],
                     ['bash', '-c', 'go build ./...'], ['python3', 'environments.py'],
                     ['node', 'test-stack.js']):
            self.assertFalse(fixed_learning_scope(argv))
        self.assertFalse(fixed_learning_scope(['go', 'build', './...'], resource=True))


if __name__ == '__main__':
    unittest.main()
