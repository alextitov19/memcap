"""Synthetic process identities only; no real cleanup or queue state."""

import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import tempfile
import io
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from orphan_recovery import observe, authorize, recovered, cleanup_reason
from scheduler import Scheduler, GIB


class RecoveryTests(unittest.TestCase):
    def test_paused_watchdog_does_not_probe_or_change_registry(self):
        from orphan_recovery import main

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "memcap"
            (state / "queue").mkdir(parents=True)
            (state / "paused").write_text("owner pause")
            registry = state / "queue/jobs.json"
            registry.write_text("leave these exact bytes")
            with (
                patch.dict(os.environ, {"MEMCAP_STATE_HOME": tmp}),
                patch("orphan_recovery.table_now") as processes,
                patch("orphan_recovery.network_state") as network,
            ):
                main()
                processes.assert_not_called()
                network.assert_not_called()
            self.assertEqual(registry.read_text(), "leave these exact bytes")

    def test_queue_displays_effective_allowance_and_unknown_measurement_age(self):
        from scheduler import main

        self.job.update(orphaned=True, measured_kb=119888)
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"MEMCAP_STATE_HOME": tmp}),
            patch("sys.argv", ["scheduler.py", "queue"]),
            patch.object(Scheduler, "status", return_value=[self.job]),
        ):
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(main(), 0)
            output = stream.getvalue()
            self.assertIn("requested=1 GiB", output)
            self.assertIn(f"reserved={6504920 / GIB:.3f} GiB", output)
            self.assertIn("age unknown", output)
            self.assertIn("orphaned", output)

    def test_script_tag_changes_reuse_observed_peak_without_crossing_content_or_worker_identity(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "deploy.sh"
            script.write_text("#!/bin/bash\ngo build ./...\n")
            scheduler = Scheduler(root / "queue", policy="adaptive", memory_gb=1)
            data = dict(jobs=[])
            key, initial = scheduler.demand(
                ["bash", str(script), "tag-one"], root, 2, data
            )
            family = scheduler.estimate_family_key
            self.assertTrue(family)
            data["estimates"] = {family: dict(estimate_kb=12 * GIB)}
            _, next_request = scheduler.demand(
                ["bash", str(script), "tag-two"], root, 2, data
            )
            self.assertEqual(next_request, 12 * GIB)
            self.assertEqual(
                scheduler.demand(["bash", str(script), "tag-two"], root, 1, data)[1],
                initial,
            )
            script.write_text("#!/bin/bash\necho different\n")
            self.assertEqual(
                scheduler.demand(["bash", str(script), "tag-two"], root, 2, data)[1],
                initial,
            )

    def setUp(self):
        self.job = dict(
            id="a" * 32,
            owner=10,
            owner_start="owner",
            group=20,
            agent_owner=5,
            agent_start="agent",
            status="running",
            members={"20": "server"},
            cwd="/project",
            resource="",
            memory_kb=1048576,
            reservation_kb=6504920,
        )
        self.table = {
            "20": dict(
                ppid=1,
                group=20,
                uid=os.getuid(),
                start="server",
                command="node /project/node_modules/.bin/vite",
                age=300,
            )
        }
        self.network = dict(known=True, connected=[], listeners=["20"])
        self.sessions = {}

    def scan(self, now=1000):
        return observe(self.job, self.table, self.network, self.sessions, now)

    def ready(self):
        self.scan()
        self.scan(1060)
        return self.scan(1120)

    def test_repeated_death_and_grace(self):
        self.assertIsNone(self.scan())
        self.assertIsNone(self.scan(1060))
        proposal = self.scan(1120)
        self.assertIsNotNone(proposal)
        self.assertEqual(
            authorize(
                proposal, self.job, self.table, self.network, self.sessions, 1121, False
            ),
            ["20"],
        )

    def test_live_owner_and_pid_reuse(self):
        self.table["5"] = dict(
            self.table["20"], start="agent", group=5, command="codex"
        )
        self.table["10"] = dict(self.table["20"], start="owner", group=10)
        self.assertIsNone(self.ready())
        del self.table["10"]
        self.table["20"]["start"] = "reused"
        self.assertIsNone(self.ready())
        self.assertFalse(recovered(self.job, self.table, 1121))

    def test_live_supervisor_with_dead_verified_agent_is_recoverable(self):
        self.table["10"] = dict(
            self.table["20"], start="owner", group=10, command="python scheduler.py"
        )
        self.assertIsNotNone(self.ready())

    def test_origin_liveness_vetoes_cleanup_even_after_project_changes(self):
        self.table["5"] = dict(
            self.table["20"], start="agent", group=5, command="codex"
        )
        self.sessions = {
            "s": dict(owner="5", start="agent", cwd="/elsewhere", phase="idle")
        }
        self.assertIsNone(self.ready())
        self.assertEqual(self.job["cleanup_blocker"], "live-origin")

    def test_exec_change_restarts_grace_and_shared_job_vetoes_authorization(self):
        proposal = self.ready()
        peer = dict(self.job, id="b" * 32)
        self.assertEqual(
            authorize(
                proposal,
                self.job,
                self.table,
                self.network,
                {},
                1121,
                False,
                jobs=[self.job, peer],
            ),
            [],
        )
        self.table["20"]["command"] = "node /other/node_modules/.bin/vite"
        self.assertIsNone(self.scan(1121))
        self.assertEqual(self.job["cleanup_blocker"], "grace-period")

    def test_legacy_origin_retains_cleanup_but_can_recover_measurement(self):
        self.job.pop("agent_owner")
        self.assertIsNone(self.ready())
        self.assertEqual(self.job["cleanup_blocker"], "origin-unverified")
        self.assertTrue(recovered(self.job, self.table, 1121))

    def test_peak_is_learned_before_a_supervisor_can_die(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = Scheduler(tmp, policy="adaptive")
            self.job.update(estimate_key="fixture", elastic=True, started=1)
            data = dict(jobs=[self.job])
            scheduler.observe(
                data, dict(monotonic=1000, footprints={"20": 12 * GIB}, fault=False)
            )
            self.assertEqual(data["estimates"]["fixture"]["estimate_kb"], 15 * GIB)
            self.assertNotIn("complete_runs", data["estimates"]["fixture"])

    def test_observed_script_growth_updates_already_waiting_automatic_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = Scheduler(tmp, policy="adaptive")
            self.job.update(
                estimate_key="first",
                estimate_family_key="script",
                elastic=True,
                started=1,
            )
            pending = dict(
                self.job,
                id="b" * 32,
                status="waiting",
                estimate_key="second",
                memory_kb=GIB,
            )
            explicit = dict(pending, id="c" * 32, elastic=False)
            data = dict(jobs=[self.job, pending, explicit])
            scheduler.observe(
                data, dict(monotonic=1000, footprints={"20": 12 * GIB}, fault=False)
            )
            self.assertEqual(pending["memory_kb"], 15 * GIB)
            self.assertEqual(pending["estimate_source"], 4)
            self.assertEqual(explicit["memory_kb"], GIB)

    def test_unknown_and_foreign_descendants_veto(self):
        for command, uid in [
            ("go build ./...", os.getuid()),
            ("vite", os.getuid() + 1),
        ]:
            with self.subTest(command=command):
                self.setUp()
                self.table["21"] = dict(
                    self.table["20"], ppid=20, start="child", command=command, uid=uid
                )
                self.job["members"]["21"] = "child"
                self.assertIsNone(self.ready())

    def test_changed_or_escaped_group_cannot_recover(self):
        self.scan()
        self.table["21"] = dict(self.table["20"], ppid=20, group=21, start="escaped")
        self.assertFalse(recovered(self.job, self.table, 1001))
        self.assertIsNone(self.ready())

    def test_network_uncertainty_and_active_clients(self):
        for network in [
            dict(known=False),
            dict(known=True, connected=["20"], listeners=["20"]),
        ]:
            self.setUp()
            self.network = network
            self.assertIsNone(self.ready())

    def test_missing_lifecycle_retains(self):
        self.sessions = None
        self.assertIsNone(self.ready())

    def test_live_session_even_idle_retains_related_project(self):
        self.table["30"] = dict(
            self.table["20"], start="agent", group=30, command="codex"
        )
        self.sessions = {
            "s": dict(owner="30", start="agent", cwd="/project/subdir", phase="idle")
        }
        self.assertIsNone(self.ready())

    def test_claim_and_pin(self):
        self.job["pinned"] = True
        self.assertIsNone(self.ready())
        self.job["pinned"] = False
        self.table["30"] = dict(
            self.table["20"], start="agent", group=30, command="codex"
        )
        self.job["claims"] = {"30": "agent"}
        self.assertIsNone(self.ready())

    def test_expiry_pause_and_races(self):
        proposal = self.ready()
        self.assertEqual(
            authorize(proposal, self.job, self.table, self.network, {}, 1151, False), []
        )
        self.assertEqual(
            authorize(
                proposal,
                self.job,
                self.table,
                self.network,
                {},
                1121,
                False,
                paused=True,
            ),
            [],
        )
        self.job["pinned"] = True
        self.assertEqual(
            authorize(proposal, self.job, self.table, self.network, {}, 1121, True), []
        )

    def test_escalation_excludes_new_children(self):
        proposal = self.ready()
        self.table["21"] = dict(self.table["20"], ppid=20, start="new")
        self.assertEqual(
            authorize(proposal, self.job, self.table, self.network, {}, 1121, True), []
        )

    def test_recovery_lease_expires_and_does_not_change_reservation(self):
        self.scan()
        self.assertTrue(recovered(self.job, self.table, 1001))
        self.assertFalse(recovered(self.job, self.table, 1091))
        self.assertEqual(self.job["reservation_kb"], 6504920)
        self.assertEqual(self.job["owner"], 10)

    def test_gap_resets_cleanup_grace(self):
        self.scan()
        self.assertIsNone(self.scan(1300))

    def test_shell_wrapper_is_unknown_work(self):
        self.table["20"]["command"] = "bash /tmp/postfix.sh"
        self.assertIsNone(self.ready())
        self.assertEqual(self.job["cleanup_blocker"], "unknown-work")

    def test_recovered_reservation_needs_full_fresh_window(self):
        self.job.update(elastic=True, started=1, observed_peak_kb=5203936)
        self.scan()
        self.job.update(orphaned=True, recovery_active=True)
        for stamp in range(1000, 1063, 2):
            sample = dict(monotonic=stamp, footprints={"20": 119888}, fault=False)
            with (
                patch("scheduler.time.monotonic", return_value=stamp),
                patch("scheduler.time.time", return_value=stamp),
            ):
                self.job["reservation_kb"] = Scheduler.reservation(
                    self.job, sample, 119888, adaptive=True
                )
            if stamp <= 1060:
                self.assertGreater(self.job["reservation_kb"], 6 * GIB)
        self.assertEqual(self.job["reservation_kb"], GIB // 2)

    def test_recovery_restores_admission_without_changing_host_policy(self):
        from admission import decide

        policy = dict(
            mode="adaptive", max_jobs=2, headroom_kb=2 * GIB, allowed_pressure=[1, 2]
        )
        sample = dict(
            monotonic=1000,
            available_kb=2 * GIB,
            tracked_kb=18 * GIB,
            cap_kb=20 * GIB,
            pressure=2,
            fault=False,
            footprints={"20": 119888},
            tracked_pids=[20],
        )
        controller = dict(now=1000, healthy_since=990)
        candidate = dict(memory_kb=GIB, resource="")
        self.assertEqual(
            decide(policy, sample, controller, [self.job], candidate)["reason"],
            "headroom",
        )
        self.job["reservation_kb"] = GIB // 2
        self.assertTrue(
            decide(policy, sample, controller, [self.job], candidate)["allow"]
        )

    def test_claim_writes_are_atomic_and_never_change_owner_or_reservation(self):
        from orphan_recovery import claim

        with tempfile.TemporaryDirectory() as tmp:
            scheduler = Scheduler(tmp)
            with scheduler.locked() as data:
                data["jobs"] = [dict(self.job, label="node")]
                scheduler.save(data)
            self.table["30"] = dict(
                self.table["20"], group=30, start="agent", command="codex"
            )
            with (
                patch("orphan_recovery.table_now", return_value=self.table),
                patch("orphan_recovery.owner", return_value="30"),
            ):
                claim(scheduler, "aaaaaaaa")
                claim(scheduler, "aaaaaaaa", True)
                with scheduler.locked() as data:
                    row = data["jobs"][0]
                    self.assertEqual(row["claims"], {"30": "agent"})
                    self.assertTrue(row["pinned"])
                    self.assertEqual(row["owner"], 10)
                    self.assertEqual(row["reservation_kb"], 6504920)
                claim(scheduler, "aaaaaaaa", False)
                claim(scheduler, "aaaaaaaa", release=True)
                with scheduler.locked() as data:
                    self.assertFalse(data["jobs"][0]["pinned"])
                    self.assertEqual(data["jobs"][0]["claims"], {})

    def test_claims_protect_against_other_cleanup_and_reject_reused_identity(self):
        from orphan_recovery import protected_claims

        self.job["pinned"] = True
        self.assertEqual(protected_claims([self.job], self.table), ["20"])
        self.table["20"]["start"] = "reused"
        self.assertEqual(protected_claims([self.job], self.table), [])

    def test_agent_origin_comes_from_live_ancestry_not_payload(self):
        from orphan_recovery import agent_identity

        table = {
            str(os.getpid()): dict(self.table["20"], ppid=30),
            "30": dict(
                self.table["20"], ppid=1, group=30, start="agent", command="codex"
            ),
        }
        with patch("orphan_recovery.table_now", return_value=table):
            self.assertEqual(
                agent_identity(), dict(agent_owner=30, agent_start="agent")
            )
            table["30"]["uid"] += 1
            self.assertEqual(agent_identity(), {})

    def test_missing_lease_explicit_request_and_missing_samples_keep_floor(self):
        for update, footprints in [
            (dict(recovery_active=False, elastic=True), {"20": 119888}),
            (dict(recovery_active=True, elastic=False), {"20": 119888}),
            (dict(recovery_active=True, elastic=True), {}),
        ]:
            with self.subTest(update=update, footprints=footprints):
                job = dict(self.job, orphaned=True, started=1, **update)
                sample = dict(monotonic=1062, footprints=footprints, fault=False)
                self.assertEqual(
                    Scheduler.reservation(job, sample, 119888, adaptive=True), 6504920
                )

    def test_refresh_releases_only_after_group_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = Scheduler(tmp)
            data = dict(jobs=[self.job])
            self.scan()
            scheduler.refresh(data, self.table)
            self.assertEqual(len(data["jobs"]), 1)
            scheduler.refresh(data, {})
            self.assertEqual(data["jobs"], [])


if __name__ == "__main__":
    unittest.main()
