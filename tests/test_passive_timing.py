"""Timing observes latency; it never grants admission or changes hook output."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))


class PassiveTimingTests(unittest.TestCase):
    def test_busy_retry_separates_call_cost_from_new_sample_ready_delay(self):
        from passive_timing import SamplingTiming
        timer = SamplingTiming()
        events = []
        with patch('passive_timing.append_event', side_effect=lambda _, row: events.append(row)):
            timer.observe(Path('/fixture'), {'busy': True, 'sampling_path': 4}, 100, 100.25)
            sample = {'monotonic': 101, 'sampling_path': 1, 'pressure': 2}
            original = dict(sample)
            timer.observe(Path('/fixture'), sample, 102.25, 102.26)
        self.assertEqual(sample, original)
        self.assertAlmostEqual(events[-1]['sampler_retry_ms'], 2000)
        self.assertAlmostEqual(events[-1]['sampler_ready_to_retry_ms'], 1250)
        self.assertAlmostEqual(events[-1]['sample_call_ms'], 10)
        self.assertAlmostEqual(events[-1]['sample_ready_age_ms'], 1260)

    def test_old_or_unavailable_samples_do_not_invent_publication_delay(self):
        from passive_timing import SamplingTiming
        for sample in ({'monotonic': 99}, {'fault': True}, {'monotonic': 105},
                       {'monotonic': 101, 'busy': True}):
            events = []
            timer = SamplingTiming()
            with patch('passive_timing.append_event', side_effect=lambda _, row: events.append(row)):
                timer.observe(Path('/fixture'), {'busy': True}, 100, 100.25)
                timer.observe(Path('/fixture'), sample, 102, 102.1)
            self.assertNotIn('sampler_ready_to_retry_ms', events[-1])

    def test_telemetry_failure_cannot_break_sampler(self):
        from passive_timing import SamplingTiming
        with patch('passive_timing.append_event', side_effect=RuntimeError('recorder unavailable')):
            SamplingTiming().observe(Path('/fixture'), {'pressure': 2}, 1, 2)

    def test_hook_clock_includes_parent_start_and_rejects_foreign_parent(self):
        from passive_timing import queue_hook_fields
        with patch.dict(os.environ, MEMCAP_HOOK_PID=str(os.getppid())), \
             patch('passive_timing.process_start', return_value=100), \
             patch('passive_timing.absolute_clock', return_value=(200, 1000000, 1)):
            self.assertEqual(queue_hook_fields(), {'hook_ms': 100.0, 'hook_timing_version': 1})
        with patch.dict(os.environ, MEMCAP_HOOK_PID='0'), \
             patch('passive_timing.process_start', side_effect=AssertionError('foreign process')):
            self.assertEqual(queue_hook_fields(), {})

    def test_hook_unknown_clock_is_missing_not_zero(self):
        from passive_timing import queue_hook_fields
        with patch.dict(os.environ, MEMCAP_HOOK_PID=str(os.getppid())), \
             patch('passive_timing.process_start', return_value=None):
            self.assertEqual(queue_hook_fields(), {})

    def test_hook_endpoint_excludes_later_timing_probe_work(self):
        from passive_timing import queue_hook_fields
        with patch.dict(os.environ, MEMCAP_HOOK_PID=str(os.getppid())), \
             patch('passive_timing.process_start', return_value=100), \
             patch('passive_timing.absolute_clock', return_value=(300, 1000000, 1)), \
             patch('passive_timing.time.get_clock_info', return_value=SimpleNamespace(implementation='mach_absolute_time()')):
            self.assertEqual(queue_hook_fields(200000000)['hook_ms'], 100)
            with patch('passive_timing.time.get_clock_info', return_value=SimpleNamespace(implementation='other-clock')):
                self.assertEqual(queue_hook_fields(200000000), {})

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS native awake clock')
    def test_real_dispatcher_emits_correlated_timing_without_changing_output(self):
        repo = Path(__file__).resolve().parents[1]
        # A short pathname is required by Unix datagram sockets on macOS.
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='mct-') as tmp:
            directory = Path(tmp)/'state/memcap/analytics'
            directory.mkdir(parents=True, mode=0o700)
            (directory/'enabled').touch()
            (directory/'key').write_bytes(b'x'*32)
            env = {**os.environ, 'HOME': tmp, 'MEMCAP_ROOT': str(repo),
                   'MEMCAP_CONFIG_HOME': tmp+'/config', 'MEMCAP_STATE_HOME': tmp+'/state',
                   'MC_DRY_RUN': '1', 'MC_DOCKER_RUNTIME': 'none'}
            payload = dict(hook_event_name='PreToolUse', session_id='fixture',
                           tool_use_id='one', tool_name='Read', cwd=tmp,
                           tool_input={'file_path': 'fixture.txt'})
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
                receiver.bind(str(directory/'events.sock'))
                receiver.settimeout(.1)
                began = time.monotonic()
                result = subprocess.run(['/bin/bash', str(repo/'bin/memcap'), 'queue-hook', 'claude'],
                                        input=json.dumps(payload), text=True, capture_output=True,
                                        env=env, timeout=10)
                elapsed = (time.monotonic()-began)*1000
                rows = []
                while True:
                    try:
                        rows.append(json.loads(receiver.recv(8192)))
                    except socket.timeout:
                        break
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
            timings = [r for r in rows if r['event'] == 'hook_timing']
            routes = [r for r in rows if r['event'] == 'route']
            self.assertEqual(len(timings), 1)
            self.assertEqual(timings[0]['operation'], routes[0]['operation'])
            self.assertEqual(timings[0]['session'], routes[0]['session'])
            self.assertGreater(timings[0]['hook_ms'], routes[0]['guard_ms'])
            self.assertLessEqual(timings[0]['hook_ms'], elapsed)
            # A missing collector must retain the exact native result.
            disconnected = subprocess.run(['/bin/bash', str(repo/'bin/memcap'), 'queue-hook', 'claude'],
                                          input=json.dumps(payload), text=True, capture_output=True,
                                          env=env, timeout=10)
            self.assertEqual((disconnected.returncode, disconnected.stdout, disconnected.stderr),
                             (result.returncode, result.stdout, result.stderr))

    def test_sampler_paths_preserve_freshness_and_probe_counts(self):
        from scheduler_metrics import shared_sample
        with tempfile.TemporaryDirectory() as tmp, \
             patch('scheduler_metrics.vm_sample', return_value={}), \
             patch('scheduler_metrics.time.monotonic', return_value=100):
            root = Path(tmp)
            calls = []
            first = shared_sample(root, 'same', lambda: calls.append(1) or {'pressure': 2})
            second = shared_sample(root, 'same', lambda: self.fail('duplicate probe'))
            self.assertEqual(calls, [1])
            self.assertEqual(first.get('sampling_path'), 2)
            self.assertEqual(second.get('sampling_path'), 1)
            self.assertNotIn('sampling_path', json.loads((root/'sample.json').read_text())['sample'])

    def test_reports_keep_old_coverage_unknown_and_new_numeric_fields(self):
        from analytics_events import make_event
        from analytics_reports import summarize
        def row(event, **fields):
            return make_event(event, fields, b'key', 'a'*32, 1,
                              build='b'*64, policy='c'*64, boot='d'*32)
        old = summarize([])
        self.assertEqual(old['sampler_timing']['sample_call_ms']['n'], 0)
        report = summarize([row('sampling', sampling_path=1, sample_call_ms=10,
                                sampler_ready_to_retry_ms=1250, secret='private'),
                            row('hook_timing', hook_ms=50, hook_timing_version=1, route='native')])
        self.assertEqual(report['sampler_timing']['sample_call_ms']['median'], 10)
        self.assertEqual(report['sampler_timing']['sampler_ready_to_retry_ms']['median'], 1250)
        self.assertEqual(report['hook_ms']['median'], 50)
        self.assertNotIn('private', json.dumps(report))

    def test_negative_controls_detect_removed_timing(self):
        # Actually observe these guards fail with the measurement removed.
        for name, target, replacement in (
            ('test_busy_retry_separates_call_cost_from_new_sample_ready_delay',
             'passive_timing.SamplingTiming.observe', lambda *args: None),
            ('test_hook_clock_includes_parent_start_and_rejects_foreign_parent',
             'passive_timing.queue_hook_fields', lambda: {}),
        ):
            result = unittest.TestResult()
            with patch(target, replacement):
                PassiveTimingTests(name).run(result)
            self.assertFalse(result.wasSuccessful(), name)

    def test_release_comparison_preserves_missing_baseline(self):
        from analytics_events import make_event
        from analytics_releases import snapshot, compare_snapshots
        rows = [make_event('hook_timing', dict(hook_ms=50, hook_timing_version=1),
                           b'key', 'a'*32, i, build='b'*64, policy='c'*64,
                           boot='d'*32, wall=1000+i, mono=100+i) for i in range(20)]
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp)/'old', Path(tmp)/'new'
            snapshot([], {}, old, since=0, until=2000)
            snapshot(rows, {}, new, since=0, until=2000)
            changes = compare_snapshots(old, new)['deltas']
        self.assertEqual(changes['queue_hook_response_p95_ms'],
                         dict(baseline=None, candidate=50, delta=None))
        self.assertEqual(changes['queue_hook_response_observations']['candidate'], 20)


if __name__ == '__main__':
    unittest.main()
