"""Reported command shapes and scheduling regressions; no live host mutation."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from scheduler_policy import hook_response, classify_shell
from workload_estimates import record


class ProductivityTests(unittest.TestCase):
    def test_reported_native_shapes(self):
        for command in [
            "git branch -a --sort=-committerdate | head -15; git status --short",
            "jq 'del(.content)' response.json",
            '''jq -r '.[] | "\\(.slug) | \\(.title)"' response.json''',
            "jq -r '.content // .body // .' response.json",
            "pdftotext -layout report.pdf -; textutil -convert txt -stdout report.docx",
            "gh issue comment 10 --repo example/project --body-file /tmp/note.md",
        ]:
            with self.subTest(command=command):
                self.assertEqual(classify_shell(command)[0], "light")

    def test_environment_arguments_use_runtime_guard(self):
        for command in [
            'curl -s -H "Authorization: $API_TOKEN" https://example.invalid/health',
            'aws ssm get-command-invocation --command-id "$COMMAND_ID"',
            'rg pattern "$SEARCH_PATH"',
            'H="Authorization: $API_TOKEN"; B=https://example.invalid; curl -s -H "$H" "$B/health"',
        ]:
            response = hook_response(dict(hook_event_name="PreToolUse", tool_name="Bash",
                session_id="parent", tool_input=dict(command=command)), "/opt/memcap", "claude")
            updated = response["hookSpecificOutput"]["updatedInput"]
            self.assertIn("_inspect", updated["command"])
            self.assertNotIn("--shell-command", updated["command"])
            self.assertNotIn("run_in_background", updated)

    def test_unbounded_jq_and_execution_remain_managed(self):
        for command in [
            "jq '[range(1000000000)]' file.json", "jq 'recurse' file.json",
            '''jq '"\\(range(1000000000))"' file.json''',
            "jq -f arbitrary.jq file.json", "git branch -D important",
            "pdftoppm report.pdf image", "rg --pre script pattern .",
        ]:
            with self.subTest(command=command):
                self.assertEqual(classify_shell(command)[0], "job")

    def test_incomplete_measurements_can_only_raise_history(self):
        row = dict(estimate_kb=1048576, complete_runs=2, peaks_kb=[500000])
        grown = record(row, 12 * 1048576, complete=False)
        self.assertGreaterEqual(grown["estimate_kb"], 15 * 1048576)
        self.assertEqual(grown["complete_runs"], 2)
        self.assertEqual(grown["peaks_kb"], row["peaks_kb"])
        self.assertEqual(record(grown, 100, complete=False), grown)

    def test_blocker_durations_charge_previous_decision(self):
        from scheduler_metrics import account_blocker, blocker_fields

        job = {}
        account_blocker(job, "headroom", 10)
        account_blocker(job, "sampling", 13)
        account_blocker(job, "sampling", 14)
        account_blocker(job, None, 15)
        account_blocker(job, None, 25)
        self.assertEqual(blocker_fields(job), dict(blocked_headroom_ms=3000, blocked_sampling_ms=2000))

    def test_subagents_get_distinct_matching_admission_and_wait_scope(self):
        import shlex
        from session_identity import key, identity, identity_key
        import hashlib

        scopes = []
        for child in ["first", "second"]:
            payload = dict(hook_event_name="PreToolUse", tool_name="Bash", session_id="parent", agent_id=child)
            scopes.append(key(payload))
            self.assertEqual(key(payload), identity_key(identity(payload)))
            for command in ["npm test", "memcap wait --session --timeout 60"]:
                output = hook_response({**payload, "tool_input": dict(command=command)}, "/opt/memcap", "claude")
                words = shlex.split(output["hookSpecificOutput"]["updatedInput"]["command"])
                self.assertEqual(words[words.index("--session-key") + 1], scopes[-1])
        self.assertNotEqual(*scopes)
        from scheduler import Scheduler
        self.assertEqual(Scheduler.session_bucket(dict(session_key=scopes[0])), Scheduler.session_bucket(dict(session_key=scopes[1])))
        self.assertEqual(key(dict(session_id="parent")), hashlib.sha256(b"parent").hexdigest())

    def test_sampler_serves_only_fresh_data_during_early_refresh(self):
        import fcntl
        import json
        import tempfile
        from scheduler_metrics import shared_sample

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.json").write_text(json.dumps(dict(key="same", sample=dict(monotonic=10, pressure=2))))
            with (root / "sample.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch("scheduler_metrics.time.monotonic", return_value=11.5):
                    self.assertEqual(shared_sample(root, "same", lambda: self.fail("lock held"))["pressure"], 2)
                with patch("scheduler_metrics.time.monotonic", return_value=12):
                    self.assertTrue(shared_sample(root, "same", lambda: self.fail("lock held"))["busy"])

    def test_sampler_refreshes_before_expiry(self):
        import json
        import tempfile
        from scheduler_metrics import shared_sample

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.json").write_text(json.dumps(dict(key="same", sample=dict(monotonic=10, pressure=2))))
            calls = []
            with patch("scheduler_metrics.time.monotonic", return_value=11.5), patch("scheduler_metrics.vm_sample", return_value={}):
                shared_sample(root, "same", lambda: calls.append(1) or dict(pressure=2))
            self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
