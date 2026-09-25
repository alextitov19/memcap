"""Owner pause must preserve native scheduling, argv, environment and task mode."""

import fcntl
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libexec"))


class PauseContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state/memcap"
        self.state.mkdir(parents=True)
        self.config = self.root / "config/memcap"
        self.config.mkdir(parents=True)
        (self.config / "memcap.conf").write_text(
            "QUEUE_WORKERS=2\nQUEUE_POLICY=adaptive\n"
        )
        self.env = {
            **os.environ,
            "MEMCAP_ROOT": str(ROOT),
            "MEMCAP_CONFIG_HOME": str(self.root / "config"),
            "MEMCAP_STATE_HOME": str(self.root / "state"),
            "MC_DRY_RUN": "1",
        }
        self.env.pop("VITEST_MAX_WORKERS", None)
        self.env["GOMAXPROCS"] = "7"
        self.env["GOFLAGS"] = "-p=7"
        self.exe = str(ROOT / "bin/memcap")

    def call(self, args, payload=None):
        return subprocess.run(
            [self.exe, *args],
            env=self.env,
            input=json.dumps(payload) if payload else None,
            capture_output=True,
            text=True,
            timeout=8,
        )

    def pause(self):
        (self.state / "paused").touch()

    def test_native_wait_resolves_installed_path_without_changing_task_mode(self):
        from scheduler_policy import hook_response

        for agent, key, tool in (
            ("claude", "command", "Bash"),
            ("codex", "cmd", "exec_command"),
        ):
            original = {
                key: "memcap wait --session --timeout 0",
                "timeout": 60000,
                "run_in_background": False,
            }
            response = hook_response(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": tool,
                    "tool_input": original,
                },
                self.exe,
                agent,
            )
            result = response["hookSpecificOutput"]
            updated = result["updatedInput"]
            self.assertEqual(
                updated["command"], self.exe + " wait --session --timeout 0"
            )
            self.assertEqual(updated["timeout"], 60000)
            self.assertFalse(updated["run_in_background"])
            self.assertNotIn("permissionDecision", result)
            self.assertNotIn("cmd", updated)
            # The shell has no Homebrew bin on PATH. The resolved invocation
            # still reaches wait, creates no queued job, and returns its status.
            fixture_wait = hook_response(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": tool,
                    "tool_input": {key: "memcap wait abcdef12 --timeout 0"},
                },
                self.exe,
                agent,
            )["hookSpecificOutput"]["updatedInput"]["command"]
            # An explicit fixture ID does not need a real agent ancestor in CI.
            self.assertEqual(fixture_wait, self.exe + " wait abcdef12 --timeout 0")
            completed = subprocess.run(
                ["/bin/bash", "-c", fixture_wait],
                env={
                    **self.env,
                    "PATH": str(Path(sys.executable).parent)
                    + ":/usr/bin:/bin:/usr/sbin:/sbin",
                },
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("no longer pending", completed.stdout)

        self.assertEqual(
            hook_response(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": self.exe + " wait --session --timeout 0"},
                },
                self.exe,
                "claude",
            ),
            {},
        )

    def test_paused_hook_leaves_reported_commands_in_native_tool_mode(self):
        self.pause()
        commands = [
            "aws logs tail /aws/service --since 5m | tail -30",
            "R=owner/repo && gh workflow run deploy -R $R --ref main",
            "wc -l client/pages/{Business,Tax,FAQ}.tsx",
            "rm scratch.test.ts && sed -n 88,100p shared/scenarios.ts",
            "npx playwright test --workers=6",
            "until test -f done; do sleep 3; done",
        ]
        for agent in ["claude", "codex"]:
            for command in commands:
                result = self.call(
                    ["queue-hook", agent],
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "session_id": "fixture",
                        "tool_input": {"command": command, "run_in_background": False},
                    },
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "", command)
        self.assertFalse((self.state / "queue").exists())

    def test_active_remote_control_and_small_file_cleanup_stay_native(self):
        from scheduler_policy import classify_shell

        for command in [
            "aws logs tail /aws/service --since 5m | tail -30",
            "R=owner/repo && gh workflow list --all -R $R && gh workflow enable deploy -R $R && gh workflow run deploy -R $R --ref main && gh run list -R $R",
            "rm tests/scratch.test.ts && sed -n 88,100p shared/scenarios.ts",
            "rm -rf tree",
            "rm ./temp/*",
        ]:
            self.assertEqual(classify_shell(command)[0], "light", command)
        for command in [
            "aws logs tail /aws/service --follow",
            "R=$(python helper.py) && gh workflow run deploy -R $R",
            "R=owner/repo && gh workflow run deploy -R $R; npm test",
        ]:
            self.assertEqual(classify_shell(command)[0], "job", command)

    def test_brace_path_read_expands_once_and_never_creates_a_queue(self):
        from inspection import guarded_shell

        directory = self.root / "pages"
        directory.mkdir()
        (directory / "A.tsx").write_text("one\n")
        (directory / "B.tsx").write_text("two\n")
        wrapped = guarded_shell("wc -l ./pages/{A,B}.tsx", self.exe, "fixture")
        self.assertIsNotNone(wrapped)
        result = subprocess.run(
            ["/bin/bash", "-c", wrapped],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 total", " ".join(result.stdout.split()))
        self.assertFalse((self.state / "queue").exists())
        for command in [
            "rg x {--pre,helper}",
            "cat ./pages/{A,$(python helper.py)}",
            "cat ./pages/{A,B}; npm test",
        ]:
            self.assertIsNone(guarded_shell(command, self.exe, "fixture"))

    def test_active_hook_still_manages_heavy_work(self):
        result = self.call(
            ["queue-hook", "claude"],
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "session_id": "fixture",
                "tool_input": {"command": "npm test"},
            },
        )
        updated = json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]
        self.assertIn(" run ", updated["command"])
        self.assertTrue(updated["run_in_background"])

    def test_paused_hook_does_not_require_valid_queue_tuning(self):
        self.pause()
        (self.config / "memcap.conf").write_text("QUEUE_POLICY=invalid\n")
        result = self.call(
            ["queue-hook", "claude"],
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "npm test"},
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def recorder(self):
        path = self.root / "playwright"
        path.write_text(
            "#!"
            + sys.executable
            + '\nimport os,sys,json\nprint(json.dumps({"args":sys.argv[1:],"go":os.environ.get("GOMAXPROCS"),"flags":os.environ.get("GOFLAGS"),"vitest":os.environ.get("VITEST_MAX_WORKERS")}))\n'
        )
        path.chmod(0o755)
        return str(path)

    def check_native(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "args": ["test", "--workers=6"],
                "go": "7",
                "flags": "-p=7",
                "vitest": None,
            },
        )

    def test_paused_run_preserves_worker_arguments_and_environment(self):
        self.pause()
        self.check_native(
            self.call(["run", "--", self.recorder(), "test", "--workers=6"])
        )
        self.assertFalse((self.state / "queue").exists())

    def test_paused_run_does_not_touch_or_wait_for_registry_lock(self):
        self.pause()
        queue = self.state / "queue"
        queue.mkdir()
        with (queue / "lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.check_native(
                self.call(["run", "--", self.recorder(), "test", "--workers=6"])
            )
        self.assertFalse((queue / "jobs.json").exists())

    def test_cached_shell_wrapper_preserves_status_and_environment_when_paused(self):
        self.pause()
        result = self.call(
            [
                "run",
                "--session-key",
                "fixture",
                "--shell",
                "/bin/bash",
                "--shell-command",
                'printf "%s/%s" "$GOMAXPROCS" "${VITEST_MAX_WORKERS-unset}"; exit 7',
            ]
        )
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout, "7/unset")
        self.assertFalse((self.state / "queue").exists())

    def test_waiter_released_by_pause_does_not_add_worker_limits(self):
        driver = """import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from scheduler import Scheduler
s=Scheduler(Path(sys.argv[2]),workers=2,poll=.02,sampler=lambda:{"cap_kb":20971520,"tracked_kb":20971520,"available_kb":0,"pressure":4,"fault":False,"footprints":{},"tracked_pids":[]})
sys.exit(s.run([sys.argv[3],"test","--workers=6"],wait=5))"""
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                driver,
                str(ROOT / "libexec"),
                str(self.state / "queue"),
                self.recorder(),
            ],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            ready = select.select([proc.stderr], [], [], 5)[0]
            self.assertTrue(ready, "waiter did not register")
            first = proc.stderr.readline()
            self.assertIn("queued", first)
            self.pause()
            out, err = proc.communicate(timeout=5)
            self.check_native(
                subprocess.CompletedProcess([], proc.returncode, out, err)
            )
            self.assertEqual(
                json.loads((self.state / "queue/jobs.json").read_text())["jobs"], []
            )
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)

    def test_empty_paused_stop_hook_does_not_request_model_polling(self):
        self.pause()
        result = self.call(
            ["feedback", "--wait"],
            {"hook_event_name": "Stop", "session_id": "fixture", "cwd": str(self.root)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('"decision": "block"', result.stdout)
        self.assertNotIn("pending work", result.stdout)


if __name__ == "__main__":
    unittest.main()
