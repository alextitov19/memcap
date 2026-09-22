"""Synthetic process/registry fixtures; no process signals or live simulator use."""

import copy
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))


class BootTests(unittest.TestCase):
    def setUp(self):
        self.mod = __import__("boot_timeout")

        def row(ppid, group, command, age=240):
            return dict(
                ppid=ppid,
                group=group,
                uid=os.getuid(),
                start="start",
                command=command,
                age=age,
            )

        self.table = {
            "10": row(1, 10, "/usr/local/bin/claude"),
            "20": row(10, 10, "python3 /memcap/libexec/scheduler.py run"),
            "30": row(20, 30, "/bin/bash -c xcrun simctl boot DEVICE | head -3"),
            "31": row(
                30,
                30,
                "/Library/Developer/PrivateFrameworks/CoreSimulator.framework/Versions/A/Resources/bin/simctl boot 3A3F994D-8ABE-4566-AF82-50D56F83A50C",
            ),
            "32": row(30, 30, "head -3"),
            "90": row(1, 90, "/Library/Developer/CoreSimulator/runtime/launchd_sim"),
        }
        self.job = dict(
            id="a" * 32,
            status="running",
            resource="",
            owner=20,
            owner_start="start",
            group=30,
            members={p: "start" for p in ["30", "31", "32"]},
            cwd="/project",
        )

    def plan(self):
        return self.mod.plan(self.job, self.table, 1000)

    def test_old_registered_boot_gets_only_its_own_group(self):
        plan = self.plan()
        self.assertIsNotNone(plan)
        self.assertEqual(set(plan["members"]), {"30", "31", "32"})
        self.assertNotIn("90", plan["members"])
        self.assertEqual(
            set(self.mod.authorize(plan, [self.job], self.table, 1001, False)),
            {"30", "31", "32"},
        )

    def test_young_boot_unknown_children_and_foreign_owner_are_spared(self):
        original = copy.deepcopy(self.table)
        for mutate in [
            lambda t: t["31"].update(age=179),
            lambda t: t["32"].update(command="xcodebuild test"),
            lambda t: t["31"].update(uid=os.getuid() + 1),
            lambda t: t["20"].update(start="reused"),
            lambda t: t["10"].update(command="/bin/other"),
            lambda t: t["10"].update(uid=os.getuid() + 1),
        ]:
            self.table = copy.deepcopy(original)
            mutate(self.table)
            self.assertIsNone(self.plan())

    def test_command_mentions_and_bootstatus_are_not_boot_attempts(self):
        for cmd in [
            "rg simctl boot DEVICE",
            "/bin/echo simctl boot DEVICE",
            "/usr/bin/xcrun simctl bootstatus DEVICE -b",
            "/tmp/simctl boot DEVICE",
        ]:
            self.table["31"]["command"] = cmd
            self.assertIsNone(self.plan())

    def test_unregistered_and_detached_children_veto(self):
        self.table["33"] = dict(self.table["32"], ppid=30, group=33)
        self.assertIsNone(self.plan())
        self.table["33"]["group"] = 30
        self.assertIsNone(self.plan())

    def test_finished_boot_before_term_invalidates_the_plan(self):
        plan = self.plan()
        self.table.pop("31")
        self.assertEqual(
            self.mod.authorize(plan, [self.job], self.table, 1001, False), []
        )

    def test_escalation_only_allows_unchanged_original_survivors(self):
        plan = self.plan()
        self.table.pop("30")
        self.table.pop("31")
        self.table["32"]["ppid"] = 1
        self.job["members"] = {"32": "start"}
        self.assertEqual(
            self.mod.authorize(plan, [self.job], self.table, 1002, True), ["32"]
        )
        self.table["32"]["command"] = "head -20"
        self.assertEqual(
            self.mod.authorize(plan, [self.job], self.table, 1002, True), []
        )

    def test_expired_plan_reused_pid_or_new_child_prevents_escalation(self):
        plan = self.plan()
        self.assertEqual(
            self.mod.authorize(plan, [self.job], self.table, 1031, True), []
        )
        self.table["31"]["start"] = "new"
        self.assertEqual(
            self.mod.authorize(plan, [self.job], self.table, 1001, True), []
        )
        self.table["31"]["start"] = "start"
        self.table["33"] = dict(self.table["32"])
        self.assertEqual(
            self.mod.authorize(plan, [self.job], self.table, 1001, True), []
        )

    def test_registry_survivors_keep_reservation_until_gone(self):
        from scheduler import Scheduler
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            q = Scheduler(Path(tmp) / "queue")
            data = {"jobs": [dict(self.job, memory_kb=2097152)]}
            remaining = {p: r for p, r in self.table.items() if p not in ("30", "31")}
            q.refresh(data, remaining)
            self.assertEqual(len(data["jobs"]), 1)
            remaining.pop("32")
            remaining.pop("20")
            q.refresh(data, remaining)
            self.assertEqual(data["jobs"], [])


if __name__ == "__main__":
    unittest.main()
