"""No real memory growth or signals: owned identities and simulated samples."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from scheduler import Scheduler
from admission import advance

GIB = 1048576


class PressureAccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.q = Scheduler(Path(self.tmp.name), policy="adaptive", max_pressure="yellow")
        self.job = dict(id="a", owner=10, owner_start="owner", status="running",
                        group=20, members={"20": "root"}, memory_kb=GIB,
                        resource="", cwd=self.tmp.name, label="fixture",
                        elastic=True, started=time.time() - 40)
        self.data = dict(jobs=[self.job])
        self.table = {
            "10": self.row(1, 10, "owner"),
            "20": self.row(10, 20, "root"),
            "30": self.row(20, 30, "detached"),
            "40": self.row(30, 30, "grandchild"),
            "50": self.row(1, 50, "unrelated"),
        }

    def row(self, parent, group, start):
        return dict(ppid=parent, group=group, start=start, uid=os.getuid())

    def test_changed_group_descendants_count_without_signal_authority(self):
        self.q.refresh(self.data, self.table)
        now = time.monotonic()
        sample = dict(monotonic=now, boot_id="fixture", pressure=2, fault=False,
                      footprints={"20": GIB // 8, "30": 2 * GIB, "40": 2 * GIB},
                      tracked_pids=[20, 30, 40], cap_kb=20 * GIB, tracked_kb=10 * GIB,
                      available_kb=2 * GIB)
        self.q.observe(self.data, sample)
        self.assertEqual(self.job["measured_kb"], 4 * GIB + GIB // 8)
        self.assertEqual(self.job["members"], {"20": "root"})
        self.q.controller = dict(now=now, healthy_since=now - 10, last_start=now - 10)
        self.assertFalse(self.q.admissible([self.job], GIB, "", sample)[0])

    def test_reparented_identity_stays_measured_but_reused_pid_does_not(self):
        self.q.refresh(self.data, self.table)
        self.table["30"]["ppid"] = 1
        self.q.refresh(self.data, self.table)
        self.assertIn("30", self.job.get("footprint_members", {}))
        self.table["30"]["start"] = "replacement"
        del self.table["40"]
        self.q.refresh(self.data, self.table)
        self.assertNotIn("30", self.job.get("footprint_members", {}))

    def test_foreign_uid_and_unrelated_work_are_not_claimed(self):
        self.table["30"]["uid"] = os.getuid() + 1
        self.q.refresh(self.data, self.table)
        self.assertEqual(self.job.get("footprint_members"), {"20": "root"})

    def test_nested_managed_group_is_counted_once(self):
        self.table["40"]["group"] = 40
        self.q.refresh(self.data, self.table)
        other = dict(self.job, id="b", group=30, members={"30": "detached"})
        self.data["jobs"].append(other)
        self.q.refresh(self.data, self.table)
        self.assertEqual(self.job.get("footprint_members"), {"20": "root"})
        self.assertEqual(other.get("footprint_members"), {"30": "detached", "40": "grandchild"})

    def test_missing_detached_footprint_cannot_lower_allowance(self):
        self.q.refresh(self.data, self.table)
        self.job["reservation_kb"] = 6 * GIB
        self.q.observe(self.data, dict(monotonic=time.monotonic(), pressure=2, fault=False,
                                     footprints={"20": 1024}))
        self.assertFalse(self.job["measurement_complete"])
        self.assertGreaterEqual(self.job["reservation_kb"], 6 * GIB)

    def test_leader_changes_group_before_first_refresh(self):
        self.table["20"]["group"] = 99
        self.q.refresh(self.data, self.table)
        self.assertEqual(self.job["members"], {})
        self.assertEqual(self.job.get("footprint_members"),
                         {"20": "root", "30": "detached", "40": "grandchild"})
        self.q.observe(self.data, dict(monotonic=time.monotonic(), pressure=2, fault=False,
                                     footprints={"20": 1024, "30": GIB, "40": GIB}))
        self.assertTrue(self.job["learning_incomplete"])
        self.assertEqual(self.job["measured_kb"], 2 * GIB + 1024)

    def test_cancellation_still_excludes_accounting_only_children(self):
        from unittest.mock import patch
        self.job["cancel"] = True
        self.q.refresh(self.data, self.table)
        with self.q.locked() as data:
            self.q.save(self.data)
        with patch("scheduler.processes", return_value=self.table):
            self.assertEqual(self.q.authorize_cancel("a", 10), ["20"])

    def test_different_boot_reinitializes_even_with_lower_monotonic(self):
        state = dict(boot_id="before", last_sample=100, paging_since=80)
        result = advance(state, dict(monotonic=10, boot_id="after", pressure=2), 11)
        self.assertEqual(result["boot_id"], "after")
        self.assertEqual(result["last_sample"], 10)
        self.assertNotIn("paging_since", result)

    def test_older_cached_yellow_cannot_erase_paging_evidence(self):
        state = dict(boot_id="fixture", last_sample=100, paging_since=80,
                     healthy_since=50, cooldown_until=106)
        old = dict(monotonic=99, boot_id="fixture", pressure=2, swap_out_kbps=0)
        result = advance(state, old, 101)
        self.assertEqual(result.get("paging_since"), 80)
        self.assertEqual(result["last_sample"], 100)
        self.assertEqual(result["cooldown_until"], 106)


if __name__ == "__main__":
    unittest.main()
