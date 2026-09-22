"""Read-only diagnostic fixtures; no real simulator or process mutation."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.mod = __import__("agent_diagnostics")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def payload(self, output, event="PostToolUse"):
        return dict(
            hook_event_name=event, tool_name="Bash", tool_response={"stdout": output}
        )

    def test_boot_signal_is_not_a_root_cause_or_test_result(self):
        p = self.payload(
            "Failed to prepare device 'iPhone 17 Pro' for impending launch. Timed out trying to boot simulator after waiting 60.00s."
        )
        with patch.object(
            self.mod,
            "measure",
            return_value=["pressure now: green", "simulators now: Shutting Down=1"],
        ):
            text = self.mod.guidance(p, self.root)
        self.assertIn("not proof of memory starvation", text)
        self.assertIn("Shutting Down=1", text)
        self.assertIn("unverified", text)
        self.assertIn("another session", text)
        self.assertNotIn("No test failed", text)
        self.assertNotIn("iPhone 17 Pro", text)

    def test_reading_command_text_does_not_trigger_diagnostics(self):
        p = self.payload("all good")
        p["tool_input"] = {"command": "echo timed out trying to boot simulator"}
        with patch.object(self.mod, "measure") as probe:
            self.assertEqual(self.mod.guidance(p, self.root), "")
            probe.assert_not_called()

    def test_claude_failure_and_codex_text_output(self):
        for field in ["error", "tool_response", "tool_result"]:
            p = dict(
                hook_event_name="PostToolUseFailure",
                **{field: "ENOMEM: cannot allocate memory"},
            )
            with patch.object(
                self.mod, "measure", return_value=["pressure now: unknown"]
            ):
                text = self.mod.guidance(p, self.root)
            self.assertIn("unknown", text)
            self.assertIn("not establish", text)

    def test_failed_probes_do_not_claim_healthy_host(self):
        with patch.object(self.mod, "probe", return_value=None):
            facts = self.mod.measure(self.root, True)
        text = " ".join(facts)
        self.assertIn("unavailable", text)
        self.assertNotIn("green", text)
        self.assertNotIn("queue empty", text)

    def test_only_read_only_probe_commands_and_bounded_deadline(self):
        calls = []

        def probe(argv, deadline):
            calls.append(argv)
            if argv[-1] == "_queue-sample":
                return "20971520 16777216 5242880 1 0\n"
            if argv[0] == "ps":
                return ""
            return json.dumps(
                {"devices": {"runtime": [{"state": "Shutting Down", "name": "SECRET"}]}}
            )

        with patch.object(self.mod, "probe", side_effect=probe):
            text = " ".join(self.mod.measure(self.root, True))
        self.assertIn("green", text)
        self.assertIn("16.00/20.00 GB", text)
        self.assertIn("5.00 GB", text)
        self.assertIn("Shutting Down=1", text)
        self.assertNotIn("SECRET", text)
        self.assertTrue(
            all(
                a[-1] == "_queue-sample"
                or a[0] == "ps"
                or a[1:] == ["simctl", "list", "devices", "--json"]
                for a in calls
            )
        )

    def test_queue_timeout_does_not_tell_agent_to_poll_dead_task(self):
        with patch.object(self.mod, "measure", return_value=[]):
            text = self.mod.guidance(
                self.payload("memcap: queue wait expired; command not started"),
                self.root,
            )
        self.assertIn("exited waiter cannot resume", text)
        self.assertIn("no existing live task", text)

    def test_queue_records_are_not_mutated_or_treated_as_live_without_identity(self):
        q = self.root / "queue"
        q.mkdir()
        raw = json.dumps(
            {"jobs": [{"owner": 234, "owner_start": "old", "status": "waiting"}]}
        )
        (q / "jobs.json").write_text(raw)
        with patch.object(self.mod, "probe", return_value=None):
            text = " ".join(self.mod.measure(self.root, False))
        self.assertIn("queue: unavailable", text)
        self.assertEqual((q / "jobs.json").read_text(), raw)

    def test_session_guidance_needs_no_host_probe(self):
        with patch.object(self.mod, "measure") as probe:
            text = self.mod.guidance({"hook_event_name": "SessionStart"}, self.root)
        self.assertIn("TaskOutput", text)
        probe.assert_not_called()

    def test_background_task_output_gets_the_same_diagnostic(self):
        p = self.payload("")
        p["tool_response"] = {
            "retrieval_status": "success",
            "task": {
                "output": "Timed out trying to boot simulator after waiting 60.00s."
            },
        }
        with patch.object(self.mod, "measure", return_value=[]):
            self.assertIn("unverified", self.mod.guidance(p, self.root))

    def test_boot_error_at_end_of_large_build_log_is_not_lost(self):
        p = self.payload(
            "compile progress\n" * 20000
            + "Timed out trying to boot simulator after waiting 60.00s."
        )
        with patch.object(self.mod, "measure", return_value=[]):
            self.assertIn("unverified", self.mod.guidance(p, self.root))

    def test_termination_evidence_is_project_scoped_and_numeric_only(self):
        folder = self.root / "job-feedback"
        folder.mkdir()
        import time

        notice = dict(
            at=int(time.time()),
            cwd=str(self.root.resolve()),
            pid="45",
            footprint_kb="5242880",
            limit_gb="4",
            command="SECRET",
            message="SECRET",
        )
        (folder / "45.json").write_text(json.dumps(notice))
        found = " ".join(self.mod.recent_terminations(self.root, str(self.root)))
        self.assertIn("PID 45", found)
        self.assertIn("another session", found)
        self.assertNotIn("SECRET", found)
        notice["cwd"] = str(self.root) + "-adjacent"
        (folder / "45.json").write_text(json.dumps(notice))
        self.assertEqual(self.mod.recent_terminations(self.root, str(self.root)), [])

    def test_faulty_pressure_sample_is_not_reported_as_green(self):
        with patch.object(self.mod, "probe", return_value="20971520 0 5242880 1 1\n"):
            text = " ".join(self.mod.measure(self.root, False))
        self.assertIn("unreliable", text)
        self.assertNotIn("green", text)

    def test_live_queue_counts_check_pid_start_and_preserve_registry(self):
        folder = self.root / "queue"
        folder.mkdir()
        raw = json.dumps(
            {
                "jobs": [
                    {
                        "owner": 42,
                        "owner_start": "Mon Sep 21 12:00:00 2026",
                        "status": "waiting",
                    },
                    {"owner": 43, "owner_start": "old reused pid", "status": "running"},
                ]
            }
        )
        path = folder / "jobs.json"
        path.write_text(raw)

        def probe(argv, deadline):
            return (
                "42 Mon Sep 21 12:00:00 2026\n43 Mon Sep 21 13:00:00 2026\n"
                if argv[0] == "ps"
                else None
            )

        with patch.object(self.mod, "probe", side_effect=probe):
            text = " ".join(self.mod.measure(self.root, False))
        self.assertIn("1 waiting, 0 running with live supervisors", text)
        self.assertEqual(path.read_text(), raw)


if __name__ == "__main__":
    unittest.main()
