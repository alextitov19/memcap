"""Owned process fixtures and lock contention: no host enforcement or Docker."""

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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

    def test_shared_sampler_reuses_result_and_busy_election_returns_immediately(self):
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
                start = time.monotonic()
                result = shared_sample(self.root, "different", reader)
                self.assertTrue(result["busy"])
                self.assertLess(time.monotonic() - start, 0.2)
            self.assertEqual(len(calls), 1)

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
