"""Public reporting uses owned temp files and an in-memory GitHub boundary."""

import fcntl
import importlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libexec"))


class GitHub:
    def __init__(self):
        self.issues = []
        self.comments = []
        self.fail = ""
        self.gets = 0

    def request(self, method, path, payload=None):
        if method == "GET":
            self.gets += 1
            if self.fail == "get":
                raise OSError("SECRET_TOKEN private failure")
            return {"items": self.issues, "incomplete_results": False}
        if path.endswith("/comments"):
            self.comments.append(payload["body"])
            return {
                "html_url": "https://github.com/alextitov19/memcap/issues/123#issuecomment-456"
            }
        self.issues.append(
            {
                "body": payload["body"],
                "number": 123,
                "state": "open",
                "html_url": "https://github.com/alextitov19/memcap/issues/123",
            }
        )
        if self.fail == "post":
            raise TimeoutError("accepted before connection broke")
        return self.issues[-1]


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            (ROOT / "libexec/report.py").exists(), "reporting feature missing"
        )
        self.mod = importlib.import_module("report")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.github = GitHub()
        self.now = 100000
        self.reporter = self.make("one")

    def make(self, name):
        return self.mod.Reporter(
            self.root / name / "config",
            self.root / name / "state",
            "0.14.0",
            transport=self.github,
            now=lambda: self.now,
            snapshot=lambda: {
                "pressure": 2,
                "cap_kb": 20971520,
                "tracked_kb": 8388608,
                "cwd": "/SECRET/project",
                "command": "SECRET_TOKEN",
                "waiting": 3,
            },
        )

    def test_distinct_contexts_survive_dedup_without_uploading_raw_text(self):
        self.reporter.consent(True)
        first = self.reporter.report("integration", context="file-read")
        second = self.reporter.report("integration", context="wait-command")
        self.assertEqual(first["status"], "published")
        self.assertEqual(second["status"], "published")
        self.assertNotEqual(first["draft"], second["draft"])
        self.assertEqual(
            self.reporter.report("integration", context="wait-command")["status"],
            "deduplicated",
        )
        self.assertIn(
            "Activity context: wait-command", Path(second["draft"]).read_text()
        )
        with self.assertRaises(ValueError):
            self.reporter.report("integration", context="SECRET_TOKEN /private/project")
        self.assertEqual(len(self.github.issues), 2)

    def test_no_consent_never_contacts_github_and_exports_only_allowed_facts(self):
        result = self.reporter.report("queue-lock")
        self.assertEqual(result["status"], "draft")
        text = Path(result["draft"]).read_text()
        self.assertIn("20971520", text)
        self.assertNotIn("SECRET", text)
        self.assertEqual(self.github.gets, 0)
        self.assertEqual(Path(result["draft"]).stat().st_mode & 0o777, 0o600)

    def test_opt_in_publishes_once_and_other_install_contributes_once(self):
        self.reporter.consent(True)
        first = self.reporter.report("queue-lock")
        self.assertEqual(first["status"], "published")
        self.assertEqual(
            first["url"], "https://github.com/alextitov19/memcap/issues/123"
        )
        self.reporter.report("queue-lock")
        other = self.make("two")
        other.consent(True)
        self.assertEqual(other.report("queue-lock")["status"], "commented")
        other.report("queue-lock")
        self.assertEqual(len(self.github.issues), 1)
        self.assertEqual(len(self.github.comments), 1)

    def test_disabled_consent_and_dry_run_block_all_network(self):
        self.reporter.consent(True)
        self.assertEqual(
            self.reporter.report("measurement", dry_run=True)["status"], "draft"
        )
        self.reporter.consent(False)
        self.assertEqual(self.reporter.report("measurement")["status"], "draft")
        self.assertEqual(self.github.gets, 0)

    def test_consent_revoked_during_search_prevents_publication(self):
        self.reporter.consent(True)
        request = self.github.request

        def revoke(method, path, payload=None):
            result = request(method, path, payload)
            if method == "GET":
                self.reporter.consent(False)
            return result

        with patch.object(self.github, "request", side_effect=revoke):
            self.assertEqual(self.reporter.report("queue-lock")["status"], "draft")
        self.assertEqual(len(self.github.issues), 0)

    def test_search_failure_keeps_draft_and_cools_down_without_leaking_error(self):
        self.reporter.consent(True)
        self.github.fail = "get"
        first = self.reporter.report("measurement")
        self.assertTrue(Path(first["draft"]).exists())
        self.reporter.report("measurement")
        self.assertEqual(self.github.gets, 1)
        self.assertNotIn("SECRET", json.dumps(first))
        self.assertEqual(len(self.github.issues), 0)

    def test_ambiguous_post_never_blindly_retries(self):
        self.reporter.consent(True)
        self.github.fail = "post"
        first = self.reporter.report("queue-lock")
        self.assertEqual(first["status"], "submission-uncertain")
        self.now += 3601
        self.github.fail = ""
        self.assertEqual(
            self.reporter.report("queue-lock")["url"], self.github.issues[0]["html_url"]
        )
        self.assertEqual(len(self.github.issues), 1)
        self.assertEqual(len(self.github.comments), 0)

    def test_lost_post_with_no_search_match_is_not_repeated(self):
        self.reporter.consent(True)
        self.github.fail = "post"
        self.reporter.report("queue-lock")
        self.github.issues.clear()
        self.now += 3601
        self.github.fail = ""
        self.assertEqual(
            self.reporter.report("queue-lock")["status"], "submission-uncertain"
        )
        self.assertEqual(len(self.github.issues), 0)

    def test_daily_limit_spans_categories(self):
        self.reporter.consent(True)
        for kind in [
            "queue-lock",
            "measurement",
            "integration",
            "queue-stall",
            "unexpected-termination",
        ]:
            self.reporter.report(kind)
        self.assertEqual(
            self.reporter.report("missing-task-poll")["status"], "rate-limited"
        )
        self.assertEqual(len(self.github.issues), 5)

    def test_private_strings_cannot_enter_numeric_facts_or_category(self):
        self.reporter.snapshot = lambda: {
            "cap_kb": "SECRET_TOKEN",
            "pressure": "/SECRET/path",
            "waiting": True,
            "running": -1,
        }
        result = self.reporter.report("measurement")
        self.assertNotIn("SECRET", Path(result["draft"]).read_text())
        with self.assertRaises(ValueError):
            self.reporter.report("other SECRET_TOKEN")

    def test_machine_and_queue_metrics_reach_public_issue_without_private_fields(self):
        self.reporter.snapshot = lambda: {
            "physical_memory_kb": 25165824,
            "logical_cpus": 12,
            "macos_major": 26,
            "macos_minor": 0,
            "architecture": 1,
            "swap_used_kb": 1048576,
            "disk_available_kb": 50000000,
            "load_1m_milli": 4200,
            "enforcement_paused": 1,
            "docker_kb": 6000000,
            "agents_including_simulators_kb": 8000000,
            "waiting_oldest_seconds": 900,
            "waiting_sessions": 3,
            "blocked_budget": 2,
            "blocked_headroom": 1,
            "hostname": "SECRET_MACHINE",
            "command": "SECRET_COMMAND",
        }
        self.reporter.consent(True)
        self.reporter.report("queue-stall")
        body = self.github.issues[0]["body"]
        self.assertIn('"physical_memory_kb": 25165824', body)
        self.assertIn('"blocked_budget": 2', body)
        self.assertIn('"waiting_oldest_seconds": 900', body)
        self.assertNotIn("SECRET", body)

    def test_lightweight_queue_delay_is_reportable_without_a_command_failure(self):
        self.reporter.consent(True)
        result = self.reporter.report("lightweight-queued", wait_seconds=120)
        self.assertEqual(result["status"], "published")
        body = self.github.issues[0]["body"]
        self.assertIn('"agent_reported_wait_seconds": 120', body)
        self.assertIn("lightweight-queued", body)
        self.assertNotIn("SECRET", body)

    def test_invalid_agent_wait_observations_never_publish(self):
        self.reporter.consent(True)
        for value in (-1, True, "SECRET", 1.5, 604801):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.reporter.report("queue-stall", wait_seconds=value)
        self.assertFalse(self.github.issues)

    def test_new_metric_fields_reject_strings_booleans_and_invalid_enums(self):
        self.reporter.snapshot = lambda: {
            "physical_memory_kb": "SECRET",
            "logical_cpus": True,
            "swap_used_kb": -1,
            "architecture": 999,
            "enforcement_paused": 2,
            "measurement_fault": 42,
            "macos_major": 10**100,
            "blocked_budget": "SECRET_REASON",
        }
        draft = Path(self.reporter.report("queue-stall")["draft"]).read_text()
        self.assertIn("```json\n{}\n```", draft)

    def test_corrupt_consent_cannot_enable_publication(self):
        self.reporter.consent(True)
        self.reporter.consent_path.write_text("{broken")
        self.assertEqual(self.reporter.report("queue-lock")["status"], "draft")
        self.assertEqual(self.github.gets, 0)

    def test_consent_requires_a_boolean_yes_not_a_truthy_value(self):
        self.reporter.consent(True)
        self.reporter.consent_path.write_text(
            json.dumps({"schema": 1, "enabled": 1, "repository": "alextitov19/memcap"})
        )
        self.assertEqual(self.reporter.report("queue-lock")["status"], "draft")
        self.assertEqual(self.github.gets, 0)

    def test_corrupt_ledger_cannot_reset_deduplication(self):
        self.reporter.consent(True)
        self.reporter.directory.mkdir(parents=True, exist_ok=True)
        (self.reporter.directory / "ledger.json").write_text("{broken")
        result = self.reporter.report("queue-lock")
        self.assertEqual(result["status"], "local-state-error")
        self.assertEqual(self.github.gets, 0)

    def test_reporting_does_not_wait_for_its_own_busy_lock(self):
        self.reporter.consent(True)
        self.reporter.directory.mkdir(parents=True, exist_ok=True)
        with (self.reporter.directory / "lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.reporter.report("queue-lock")["status"], "busy")
        self.assertEqual(self.github.gets, 0)

    def test_foreign_issue_urls_and_malformed_search_results_are_not_followed(self):
        self.reporter.consent(True)
        first = self.reporter.report("queue-lock", dry_run=True)
        self.github.issues = [
            {
                "body": Path(first["draft"]).read_text(),
                "number": 123,
                "html_url": "https://evil.example/issues/123",
            }
        ]
        self.assertEqual(self.reporter.report("queue-lock")["status"], "pending")
        self.assertFalse(self.github.comments)

    def test_report_command_bypasses_workload_queue_without_bypassing_permissions(self):
        from scheduler_policy import hook_response

        for agent in ("claude", "codex"):
            response = hook_response(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "memcap report queue-lock"},
                },
                "memcap",
                agent,
            )
            self.assertNotIn("updatedInput", response.get("hookSpecificOutput", {}))

    def test_memcap_faults_get_specific_reports_and_capacity_waits_get_triage_guidance(
        self,
    ):
        import agent_diagnostics as diag

        with patch.object(diag, "measure", return_value=[]):
            text = diag.guidance(
                {
                    "hook_event_name": "PostToolUseFailure",
                    "error": "memcap: queue lock unavailable; command was not admitted",
                },
                self.root,
            )
            self.assertIn("memcap report queue-lock", text)
            for error in (
                "memcap: memory sampling failed; command was not admitted",
                "memcap: memory measurement unavailable; no work admitted",
            ):
                text = diag.guidance(
                    {"hook_event_name": "PostToolUseFailure", "error": error}, self.root
                )
                self.assertIn("memcap report measurement", text)
            normal = diag.guidance(
                {
                    "hook_event_name": "PostToolUse",
                    "tool_response": "memcap: queued 12345678: all slots occupied",
                },
                self.root,
            )
            self.assertNotIn("For this suspected memcap defect", normal)
            self.assertIn(
                "Capacity waiting for genuinely heavy work alone is not a defect",
                normal,
            )
            self.assertIn("memcap report lightweight-queued", normal)

    def test_transport_pins_host_and_sends_json_on_stdin_without_prompting(self):
        tools_dir = self.root / "bin"
        tools_dir.mkdir()
        log = self.root / "request.json"
        exe = tools_dir / "gh"
        exe.write_text(
            f"#!{sys.executable}\nimport sys,json,os\nfrom pathlib import Path\n"
            f'Path({str(log)!r}).write_text(json.dumps([sys.argv[1:],sys.stdin.read(),os.environ.get("GH_PROMPT_DISABLED"),os.environ.get("GH_HOST")]))\n'
            'print(json.dumps({"html_url":"https://github.com/alextitov19/memcap/issues/123"}))\n'
        )
        exe.chmod(0o700)
        with patch.dict(
            os.environ,
            {
                "PATH": str(tools_dir) + os.pathsep + os.environ["PATH"],
                "GH_HOST": "wrong.example",
            },
        ):
            got = self.mod.GitHub().request(
                "POST", "repos/alextitov19/memcap/issues", {"body": "safe\nreport"}
            )
        self.assertEqual(
            got["html_url"], "https://github.com/alextitov19/memcap/issues/123"
        )
        argv, body, prompt, host = json.loads(log.read_text())
        self.assertEqual(
            argv,
            [
                "api",
                "--hostname",
                "github.com",
                "--method",
                "POST",
                "repos/alextitov19/memcap/issues",
                "--input",
                "-",
            ],
        )
        self.assertEqual(json.loads(body), {"body": "safe\nreport"})
        self.assertEqual((prompt, host), ("1", "github.com"))

    def test_transport_deadline_does_not_wait_indefinitely_for_github(self):
        tools_dir = self.root / "bin"
        tools_dir.mkdir()
        exe = tools_dir / "gh"
        exe.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(5)\n")
        exe.chmod(0o700)
        client = self.mod.GitHub()
        client.deadline = time.monotonic() + 0.1
        with patch.dict(
            os.environ, {"PATH": str(tools_dir) + os.pathsep + os.environ["PATH"]}
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                client.request("GET", "search/issues?q=test")

    def test_search_failure_recovers_on_later_report_without_resubmission_loop(self):
        self.reporter.consent(True)
        self.github.fail = "get"
        self.reporter.report("queue-lock")
        self.now += 3601
        self.github.fail = ""
        self.assertEqual(self.reporter.report("queue-lock")["status"], "published")
        self.assertEqual(len(self.github.issues), 1)

    def test_edited_draft_cannot_inject_private_text_into_public_report(self):
        result = self.reporter.report("queue-lock")
        Path(result["draft"]).write_text("SECRET_TOKEN /private/project")
        self.reporter.consent(True)
        self.reporter.report("queue-lock")
        self.assertNotIn("SECRET", self.github.issues[0]["body"])

    def test_failed_local_persistence_prevents_post(self):
        self.reporter.consent(True)
        original = self.reporter.save

        def save(ledger):
            if ledger["attempts"]:
                raise OSError("disk full")
            original(ledger)

        with patch.object(self.reporter, "save", side_effect=save):
            result = self.reporter.report("queue-lock")
        self.assertNotEqual(result["status"], "published")
        self.assertEqual(len(self.github.issues), 0)


if __name__ == "__main__":
    unittest.main()
