"""Owned process fixtures and lock contention: no host enforcement or Docker."""

import fcntl
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from scheduler import Scheduler, QueueError
from scheduler_metrics import shared_sample

GIB = 1048576


def sample():
    return dict(
        cap_kb=20 * GIB,
        tracked_kb=23 * GIB,
        available_kb=4 * GIB,
        pressure=2,
        fault=False,
        footprints={},
        tracked_pids=[],
        boot_id="fixture",
        monotonic=time.monotonic(),
    )


class AdaptiveSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def queue(self, **kwargs):
        return Scheduler(
            self.root / "queue",
            sampler=sample,
            policy="adaptive",
            max_pressure="yellow",
            poll=0.03,
            **kwargs,
        )

    def test_sampler_can_acquire_admission_lock_while_command_waits(self):
        q = self.queue()
        calls = []

        def reader():
            with open(q.directory / "lock", "a") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                calls.append(True)
            return sample()

        q.sampler = reader
        marker = self.root / "ran"
        self.assertEqual(
            q.run([sys.executable, "-c", f'open({str(marker)!r},"w").close()'], wait=5),
            0,
        )
        self.assertTrue(marker.exists())
        self.assertGreater(len(calls), 1)

    def test_status_never_acquires_or_writes_admission_lock(self):
        q = self.queue()
        with q.locked() as data:
            q.save(data)
            original = (q.directory / "jobs.json").read_bytes()
            with patch.object(q, "locked", side_effect=AssertionError("status locked")):
                self.assertEqual(q.status(), [])
            self.assertEqual((q.directory / "jobs.json").read_bytes(), original)

    def test_busy_sampler_does_not_reset_recovery_or_drop_reservations(self):
        q = self.queue()
        data = dict(jobs=[], controller=dict(healthy_since=1, last_start=2))
        q.observe(data, dict(busy=True, fault=True, pressure=0))
        self.assertEqual(data["controller"]["healthy_since"], 1)
        q.observe(data, {})
        self.assertEqual(data["controller"]["healthy_since"], 1)

    def test_shared_sampler_reuses_result_and_busy_election_has_bounded_handoff(self):
        calls = []

        def reader():
            calls.append(True)
            return sample()

        with patch("scheduler_metrics.vm_sample", return_value={}):
            shared_sample(self.root, "same", reader)
            shared_sample(self.root, "same", reader)
            self.assertEqual(len(calls), 1)
            with open(self.root / "sample.lock", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                clock = [100.0]
                with patch('scheduler_metrics.time.monotonic', side_effect=lambda:clock[0]), \
                     patch('scheduler_metrics.time.sleep', side_effect=lambda seconds:clock.__setitem__(0, clock[0]+seconds)):
                    result = shared_sample(self.root, "different", reader)
                self.assertTrue(result["busy"])
                self.assertLessEqual(clock[0] - 100, 1.000001)
            self.assertEqual(len(calls), 1)

    def publish(self, q, key, age=0.0, **fields):
        q.directory.mkdir(parents=True, exist_ok=True)
        published = {**sample(), "monotonic": time.monotonic() - age, **fields}
        # Atomic like shared_sample: a torn read would look like a stale sample.
        staged = q.directory / f".sample-{threading.get_ident()}"
        staged.write_text(json.dumps(dict(key=key, sample=published)))
        os.replace(staged, q.directory / "sample.json")

    def test_newer_cached_sample_never_extends_freshness_or_hides_faults(self):
        from scheduler_metrics import newer_cached_sample
        q = self.queue()
        held = {**sample(), "monotonic": time.monotonic() - 2.5}
        self.publish(q, "sig", age=0.1, available_kb=3 * GIB)
        self.assertEqual(newer_cached_sample(q.directory, "sig", held)["available_kb"], 3 * GIB)
        self.assertIs(newer_cached_sample(q.directory, "other", held), held)
        busy = dict(busy=True, fault=True, pressure=0, monotonic=time.monotonic())
        self.assertEqual(newer_cached_sample(q.directory, "sig", busy)["available_kb"], 3 * GIB)
        fault = dict(fault=True, pressure=0, monotonic=time.monotonic() - 5)
        self.assertIs(newer_cached_sample(q.directory, "sig", fault), fault)
        newer = {**sample(), "monotonic": time.monotonic()}
        self.assertIs(newer_cached_sample(q.directory, "sig", newer), newer)
        self.publish(q, "sig", age=2.0)
        self.assertIs(newer_cached_sample(q.directory, "sig", held), held)
        (q.directory / "sample.json").write_text("{")
        self.assertIs(newer_cached_sample(q.directory, "sig", held), held)

    def test_host_measurement_records_the_signature_used_for_lock_refresh(self):
        import scheduler
        q = self.queue()
        q.sampler = scheduler.sample_host
        with patch("scheduler.shared_sample", return_value=sample()) as shared:
            q.measure()
        self.assertTrue(q.sample_signature)
        self.assertEqual(shared.call_args.args[1], q.sample_signature)

    def test_waiter_keeps_measuring_after_an_upgrade_removes_its_installation(self):
        # brew upgrade deletes the Cellar a queued supervisor was started from.
        # Its own bridge is gone, so sampling must use the installed one, and
        # the code digest must come from what the process loaded, not disk.
        import scheduler
        bridge = Path(self.tmp.name) / "installed-memcap"
        bridge.write_text('[ "$1" = _queue-sample ] && echo "20971520 1048576 8388608 1 0"\n')
        q = self.queue()
        q.sampler = scheduler.sample_host
        self.addCleanup(scheduler._LOADED_BUILD.clear)
        scheduler._LOADED_BUILD.clear()
        scheduler.loaded_build()
        signature = None
        with patch("scheduler.ROOT", Path(self.tmp.name) / "deleted-cellar"), \
                patch("scheduler.shutil.which", return_value=str(bridge)), \
                patch("analytics_events.build_digest", side_effect=FileNotFoundError("deleted")), \
                patch("scheduler.shared_sample", side_effect=lambda _d, _key, sampler: sampler()):
            for _ in range(2):
                measured = q.measure()
                self.assertFalse(measured["fault"])
                self.assertEqual((measured["pressure"], measured["available_kb"]), (1, 8388608))
                signature = signature or q.sample_signature
                self.assertEqual(q.sample_signature, signature)

    def test_supervisor_preloads_every_module_it_imports_lazily(self):
        import scheduler
        scheduler.preload_supervisor_modules()
        self.assertTrue(set(scheduler.SUPERVISOR_MODULES) <= set(sys.modules))
        source = Path(scheduler.__file__).read_text()
        lazy = set(re.findall(r"^\s+from (\w+) import", source, re.M)) | set(re.findall(r"^\s+import (\w+)$", source, re.M))
        top = set(re.findall(r"^from (\w+) import", source, re.M)) | set(re.findall(r"^import (\w+)", source, re.M))
        local = {p.stem for p in Path(scheduler.__file__).parent.glob("*.py")}
        self.assertEqual((lazy & local) - top - set(scheduler.SUPERVISOR_MODULES), set())

    def contended_run(self, wait=8, **published):
        # The waiter's own sample is already past the limit when it gets the
        # registry lock, as under a long lock queue; another supervisor keeps
        # republishing. Only the published sample can make admission possible.
        import scheduler_metrics
        reasons = []
        account = scheduler_metrics.account_blocker
        patcher = patch("scheduler_metrics.account_blocker",
                        side_effect=lambda job, reason, now: (reasons.append(reason), account(job, reason, now)))
        patcher.start()
        self.addCleanup(patcher.stop)
        q = self.queue()
        q.sampler = lambda: {**sample(), "monotonic": time.monotonic() - 2.5}
        q.sample_signature = "sig"
        stop = threading.Event()

        def publisher():
            while not stop.is_set():
                self.publish(q, **{"key": "sig", **published})
                stop.wait(0.1)
        self.publish(q, **{"key": "sig", **published})
        thread = threading.Thread(target=publisher)
        thread.start()
        self.addCleanup(lambda: (stop.set(), thread.join()))
        marker = self.root / "ran"
        result = q.run([sys.executable, "-c", f'open({str(marker)!r},"w").close()'], wait=wait)
        stop.set()
        thread.join()
        completed = [json.loads(line) for line in (q.directory / "events.jsonl").read_text().splitlines()
                     if '"completed"' in line] if (q.directory / "events.jsonl").exists() else []
        return result, marker.exists(), set(reasons), completed

    def test_contended_waiter_admits_on_fresher_published_sample(self):
        result, ran, reasons, completed = self.contended_run()
        self.assertEqual((result, ran), (0, True))
        self.assertNotIn("sampling", reasons)
        self.assertGreaterEqual(completed[-1]["sampling_refreshed_count"], 1)

    def test_contended_waiter_never_admits_on_stale_red_or_foreign_samples(self):
        # Red is adopted (it is fresher) and then denied on pressure; stale and
        # foreign publications are never adopted, so the waiter still sees expiry.
        for published, reason in ((dict(age=2.5), {"sampling"}),
                                  (dict(pressure=4), {"pressure_or_measurement"}),
                                  (dict(key="other"), {"sampling"})):
            with self.subTest(published=published):
                result, ran, reasons, _ = self.contended_run(wait=3, **published)
                self.assertEqual((result, ran, reasons), (75, False, reason))

    def test_observed_peak_growth_survives_a_later_memory_lull(self):
        q = self.queue()
        job = dict(
            status="running",
            resource="",
            memory_kb=GIB,
            members={"42": "id"},
            elastic=True,
            started=time.time() - 60,
            observed_peak_kb=4 * GIB,
        )
        observation = sample()
        observation["footprints"] = {"42": GIB // 4}
        self.assertEqual(q.reservation(job, observation, GIB // 4), 5 * GIB)

    def retired_peak_job(self):
        job = dict(status='running', resource='', memory_kb=GIB,
                   members={'42': 'fixture'}, elastic=True, started=1,
                   observed_peak_kb=6 * GIB)
        for stamp in range(1000, 1063, 2):
            self.observe_reservation(job, stamp)
        self.assertEqual(job['reservation_kb'], GIB // 2)
        self.assertEqual(job['observed_peak_kb'], 6 * GIB)
        return job

    @staticmethod
    def observe_reservation(job, stamp, measured=GIB // 8, **fields):
        observation = dict(monotonic=stamp, footprints={p: 0 for p in job['members']}, fault=False)
        observation['footprints']['42'] = measured
        observation.update(fields)
        with patch('scheduler.time.monotonic', return_value=stamp), patch('scheduler.time.time', return_value=stamp):
            job['reservation_kb'] = Scheduler.reservation(job, observation, measured, adaptive=True)

    def test_fresh_sample_after_gap_does_not_resurrect_a_retired_peak(self):
        job = self.retired_peak_job()
        self.observe_reservation(job, 1070)
        self.assertEqual(job['reservation_kb'], GIB // 2)
        self.assertEqual(job['observed_peak_kb'], 6 * GIB, 'learning retains the lifetime peak')

    def test_fault_then_fresh_sample_keeps_retired_allowance_until_real_growth(self):
        job = self.retired_peak_job()
        self.observe_reservation(job, 1064, fault=True, footprints={})
        self.assertEqual(job['reservation_kb'], GIB // 2)
        self.observe_reservation(job, 1070)
        self.assertEqual(job['reservation_kb'], GIB // 2)
        self.observe_reservation(job, 1072, measured=2 * GIB)
        self.assertEqual(job['reservation_kb'], 5 * GIB // 2)

    def test_partial_growth_and_uncertain_orphans_keep_their_floors_after_recovery(self):
        for change in ('partial-growth', 'orphan'):
            with self.subTest(change=change):
                job = self.retired_peak_job()
                if change == 'partial-growth':
                    job['members']['43'] = 'child'
                    self.observe_reservation(job, 1064, measured=2 * GIB, footprints={'42': 2 * GIB})
                    floor = 5 * GIB // 2
                else:
                    job.update(orphaned=True, recovery_active=False)
                    self.observe_reservation(job, 1064)
                    floor = GIB
                    job['recovery_active'] = True
                self.observe_reservation(job, 1070)
                self.assertGreaterEqual(job['reservation_kb'], floor)
                self.assertLess(job['reservation_kb'], 6 * GIB)
                for stamp in range(1072, 1133, 2):
                    self.observe_reservation(job, stamp)
                self.assertEqual(job['reservation_kb'], GIB // 2)

    def test_unknown_policy_fails_closed(self):
        with self.assertRaises(QueueError):
            Scheduler(self.root, policy="unrestricted")

    def test_dynamic_workers_share_cpu_and_preserve_configured_maximum(self):
        q = self.queue(workers=8)
        jobs = [dict(status="waiting", session_key=str(i)) for i in range(3)]
        with patch("scheduler.os.cpu_count", return_value=14):
            self.assertEqual(q.allocation([]), 8)
            self.assertEqual(q.allocation(jobs), 4)
            jobs.append(
                dict(status="running", resource="", workers=11, session_key="four")
            )
            self.assertEqual(q.allocation(jobs), 1)

    def test_workload_key_is_private_and_worker_sensitive(self):
        q = self.queue(memory_gb=1)
        data = {}
        key, prior = q.demand(["go", "test", "./..."], self.root, 2, data)
        key2, other = q.demand(["go", "test", "./..."], self.root, 4, data)
        self.assertNotEqual(key, key2)
        self.assertEqual(prior, GIB)
        self.assertNotIn(str(self.root), json.dumps(data))
        self.assertEqual(
            q.demand(["xcodebuild", "archive"], self.root, 2, data)[1], 4 * GIB
        )

    def test_adaptive_backfill_has_bounded_drain_windows(self):
        q = self.queue(memory_gb=1)
        observation = sample()
        q.controller = dict(now=time.monotonic(), healthy_since=0, last_start=0)
        jobs = [
            dict(
                id="large", status="waiting", memory_kb=8 * GIB, resource="", enqueued=0
            ),
            dict(id="small", status="waiting", memory_kb=GIB, resource="", enqueued=10),
            dict(
                id="running", status="running", memory_kb=GIB, resource="", members={}
            ),
        ]
        self.assertEqual(q.next_waiter(jobs, "", observation, now=61), "large")
        self.assertEqual(q.next_waiter(jobs, "", observation, now=67), "small")

    def test_live_supervisors_stage_starts_and_all_sessions_finish_above_target(self):
        driver = """import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scheduler import Scheduler
GIB=1048576
def sample():
 return dict(cap_kb=20*GIB,tracked_kb=23*GIB,available_kb=8*GIB,pressure=2,fault=False,footprints={},tracked_pids=[],boot_id='fixture',monotonic=time.monotonic())
s=Scheduler(sys.argv[2],sampler=sample,policy='adaptive',max_pressure='yellow',memory_gb=1,max_jobs=12,poll=.05)
raise SystemExit(s.run([sys.executable,'-c',"import time;time.sleep(.1)"],session_key=sys.argv[3],wait=15))
"""
        children = []
        for i in range(3):
            children.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        driver,
                        str(Path(__file__).resolve().parents[1] / "libexec"),
                        str(self.root / "queue"),
                        str(i),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        for child in children:
            out, err = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, (out, err))
        events = [
            json.loads(x)
            for x in (self.root / "queue/events.jsonl").read_text().splitlines()
        ]
        starts = [e["timestamp"] for e in events if e["event"] == "admitted"]
        self.assertEqual(len(starts), 3)
        self.assertTrue(all(b - a >= 1.9 for a, b in zip(starts, starts[1:])))
        self.assertEqual(self.queue().status(), [])


if __name__ == "__main__":
    unittest.main()
