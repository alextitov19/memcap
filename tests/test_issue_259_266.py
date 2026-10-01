"""Productivity regressions; remote commands are fake and state is private."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libexec"))
from inspection import guarded_shell, inspect_argv
from session_identity import identity_key
from scheduler_policy import hook_response, classify_shell


class OpenIssueTests(unittest.TestCase):
    def test_finite_diagnostics_and_noninteractive_github_controls_stay_native(self):
        for command in (
            "gh pr edit 123 --body-file /tmp/notes.md",
            "gh release create v1.2.3 --title Release --notes-file /tmp/notes.md --target abc123",
            "rg -n needle README.md | head -25 && id -u && gh repo view --json defaultBranchRef",
            "top -l 1 -n 15 -o mem -stats pid,command,mem,cmprs",
            "vm_stat", "sysctl kern.memorystatus_vm_pressure_level kern.boottime hw.memsize",
            "zprint -t -w", "zprint data.kalloc.1024",
            "ioreg -r -c IOAccelerator -l -w 0",
            "launchctl print gui/501/com.memcap.analytics",
            "launchctl print-disabled gui/501",
        ):
            with self.subTest(command=command):
                self.assertEqual(classify_shell(command)[0], "light")

    def test_diagnostic_and_remote_calls_queue_only_positive_workloads(self):
        for command in ("top", "top -l 0", "top -l 999999", "vm_stat 1",
                        "sysctl -w kern.maxproc=99999", "sysctl kern.maxproc=99999",
                        "gh pr edit 123", "gh pr edit 123 --editor",
                        "gh release create v1", "gh repo clone owner/project", "gh repo view --web",
                        "ioreg -r -c Unknown", "zprint -d", "zprint -t; npm test",
                        "launchctl kickstart -k gui/501/com.example",
                        "launchctl debug gui/501/com.example -- /bin/sh",
                        "launchctl print gui/501/com.example; npm test"):
            with self.subTest(command=command):
                expected = 'job' if command in {'zprint -t; npm test', 'launchctl print gui/501/com.example; npm test'} else 'light'
                self.assertEqual(classify_shell(command)[0], expected)

    def test_shell_options_do_not_queue_supported_inspection(self):
        for body in (
            "cat README.md", "rg -n fixture README.md", "git status --short",
            "aws ssm get-parameter --name /fixture --with-decryption",
            "aws ssm get-command-invocation --command-id fixture --instance-id fixture",
            "memcap wait --session --timeout 60",
        ):
            with self.subTest(body=body):
                self.assertIsNotNone(guarded_shell("set -e; " + body, str(ROOT / "bin/memcap"), "fixture"))
                for agent, tool, field in (("claude", "Bash", "command"), ("codex", "exec_command", "cmd")):
                    response = hook_response(dict(hook_event_name="PreToolUse", session_id="parent", agent_id="child", permission_mode="bypassPermissions",
                        tool_name=tool, tool_input={field: "set -e; " + body}), str(ROOT / "bin/memcap"), agent)
                    if body.startswith('memcap wait'):
                        updated = response["hookSpecificOutput"]["updatedInput"]
                        self.assertIn('--session-key', updated['command'])
                        self.assertNotIn('run_in_background', updated)
                    else:
                        self.assertEqual(response, {})

    def test_shell_options_do_not_hide_execution(self):
        for body in ("npm test", "rg --pre worker fixture file", "aws ssm start-session --target fixture",
                     "set -x; cat file", "BASH_ENV=worker; cat file", "cat file; npm test"):
            with self.subTest(body=body):
                self.assertIsNone(guarded_shell("set -e; " + body, str(ROOT / "bin/memcap"), "fixture"))

    def test_option_prefixed_remote_read_preserves_output_failure_and_single_execution(self):
        command = "set -e; aws ssm get-parameter --name /fixture; printf should-not-run"
        guarded = guarded_shell(command, str(ROOT / "bin/memcap"), "fixture")
        self.assertIsNotNone(guarded)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fake = directory / "aws"
            fake.write_text('#!/bin/sh\necho call >> "$CALLS"\nprintf "fixture-value\\n"\nexit 7\n')
            fake.chmod(0o700)
            env = {**os.environ, "PATH": tmp + os.pathsep + os.environ["PATH"], "CALLS": tmp + "/calls",
                   "HOME": tmp, "MEMCAP_ROOT": str(ROOT), "MEMCAP_CONFIG_HOME": tmp + "/config",
                   "MEMCAP_STATE_HOME": tmp + "/state", "MC_DRY_RUN": "1"}
            result = subprocess.run(["/bin/bash", "-c", guarded], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (7, "fixture-value\n", ""))
            self.assertEqual((directory / "calls").read_text(), "call\n")
            self.assertFalse((directory / "state/memcap/queue/jobs.json").exists())

    def test_guarded_session_wait_cannot_observe_a_sibling_scope(self):
        current = identity_key(("parent", "current-child"))
        stale = identity_key(("parent", "sibling-child"))
        launched = []
        def execute(file, argv, env):
            launched.append(argv)
            raise SystemExit(0)
        with patch("inspection.os.execvpe", side_effect=execute):
            with self.assertRaises(SystemExit):
                inspect_argv(["memcap", "wait", "--session", "--session-key", stale, "--timeout", "0"],
                             lambda argv: self.fail("wait must not reserve capacity"), current)
        self.assertEqual(launched[0].count("--session-key"), 1)
        self.assertEqual(launched[0][launched[0].index("--session-key") + 1], current)
        self.assertEqual(launched[0][0], str(ROOT / "bin/memcap"))
        launched.clear()
        with patch("inspection.os.execvpe", side_effect=execute):
            with self.assertRaises(SystemExit):
                inspect_argv(["memcap", "wait", "abcdef123456", "--timeout", "0"],
                             lambda argv: self.fail("wait must not reserve capacity"), "")
        self.assertEqual(launched, [[str(ROOT / "bin/memcap"), "wait", "abcdef123456", "--timeout", "0"]])

    def test_cached_runner_rechecks_option_prefixed_reads_before_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "HOME": tmp, "MEMCAP_ROOT": str(ROOT),
                   "MEMCAP_CONFIG_HOME": tmp + "/config", "MEMCAP_STATE_HOME": tmp + "/state", "MC_DRY_RUN": "1"}
            result = subprocess.run([str(ROOT / "bin/memcap"), "run", "--session-key", "fixture",
                "--shell-command", "set -e; printf cached-read"], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "cached-read", ""))
            self.assertFalse((Path(tmp) / "state/memcap/queue/jobs.json").exists())


if __name__ == "__main__":
    unittest.main()
