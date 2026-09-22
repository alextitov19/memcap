"""No live processes: cleanup authorization uses adversarial synthetic tables."""

import copy
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from test_queue_storm import TICK, FILE_POLL


class PollCleanupTests(unittest.TestCase):
    def setUp(self):
        import poll_cleanup

        self.mod = poll_cleanup

        def row(ppid, group, command):
            return dict(
                ppid=ppid,
                group=group,
                command=command,
                age=600,
                start="start",
                uid=os.getuid(),
            )

        self.table = {
            "10": row(1, 10, "claude"),
            "20": row(
                10,
                10,
                "/usr/bin/python3 /opt/homebrew/Cellar/memcap/0.12.0/libexec/scheduler.py run --shell /bin/bash --wait-forever --shell-command "
                + TICK,
            ),
        }
        self.job = dict(
            id="a" * 32,
            status="waiting",
            owner=20,
            owner_start="start",
            group=0,
            members={},
            resource="",
            cwd="/project",
            enqueued=0,
        )

    def test_queued_wait_loop_only_authorizes_its_supervisor(self):
        p = self.mod.plan(self.job, self.table, 1000)
        self.assertIsNotNone(p)
        self.assertEqual(set(p["members"]), {"20"})
        self.assertEqual(
            self.mod.authorize(p, [self.job], self.table, 1001, False), ["20"]
        )

    def test_ordinary_work_foreign_owner_children_and_reused_pid_veto(self):
        original = copy.deepcopy(self.table)
        for mutate in [
            lambda t: t["20"].update(command=t["20"]["command"] + "; make build"),
            lambda t: t["20"].update(uid=os.getuid() + 1),
            lambda t: t["20"].update(start="reused"),
            lambda t: t.update({"21": dict(t["20"], ppid=20, command="top")}),
            lambda t: t["10"].update(command="other"),
        ]:
            self.table = copy.deepcopy(original)
            mutate(self.table)
            self.assertIsNone(self.mod.plan(self.job, self.table, 1000))

    def orphan(self):
        self.table.pop("20")
        self.table["30"] = dict(
            self.table["10"], ppid=1, group=30, command="/bin/bash -c " + FILE_POLL
        )
        self.table["31"] = dict(self.table["30"], ppid=30, command="sleep 10")
        self.job.update(
            status="running",
            group=30,
            members={"30": "start", "999": "old child"},
            orphaned=True,
        )

    def test_abandoned_wait_only_group_includes_fresh_sleep_child(self):
        self.orphan()
        p = self.mod.plan(self.job, self.table, 1000)
        self.assertIsNotNone(p)
        self.assertEqual(
            set(self.mod.authorize(p, [self.job], self.table, 1001, False)),
            {"30", "31"},
        )
        self.table.pop("30")
        self.table["31"]["ppid"] = 1
        self.assertEqual(
            self.mod.authorize(p, [self.job], self.table, 1002, True), ["31"]
        )

    def test_orphan_unknown_work_live_supervisor_and_changed_children_veto(self):
        self.orphan()
        original = copy.deepcopy(self.table)
        for mutate in [
            lambda t: t["31"].update(command="make build"),
            lambda t: t["30"].update(start="reused"),
            lambda t: t["30"].update(age=59),
            lambda t: t.update({"20": dict(t["10"])}),
            lambda t: t.update({"32": dict(t["31"], group=32)}),
        ]:
            self.table = copy.deepcopy(original)
            mutate(self.table)
            self.assertIsNone(self.mod.plan(self.job, self.table, 1000))

    def test_admission_command_change_and_stale_plan_prevent_signal(self):
        p = self.mod.plan(self.job, self.table, 1000)
        self.job["status"] = "running"
        self.assertEqual(self.mod.authorize(p, [self.job], self.table, 1001, False), [])
        self.job["status"] = "waiting"
        self.assertEqual(self.mod.authorize(p, [self.job], self.table, 1031, False), [])
        self.table["20"]["command"] += "; make build"
        self.assertEqual(self.mod.authorize(p, [self.job], self.table, 1001, True), [])


if __name__ == "__main__":
    unittest.main()
