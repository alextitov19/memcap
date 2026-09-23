"""Regressions from GitHub #19, #20, #22, #23 and #24; no live workloads."""

import sys
import os
import json
import tempfile
import subprocess
from unittest.mock import patch
from contextlib import redirect_stdout
import io
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from scheduler_policy import classify_shell, hook_response


class ProductivityTests(unittest.TestCase):
    def test_light_wrappers_never_enter_workload_queue(self):
        commands = [
            "LC_ALL=C rg -n vendor server/",
            "env AWS_PROFILE=dev AWS_REGION=us-east-1 aws ssm get-command-invocation --command-id example --instance-id i-example",
            "aws --profile dev --region us-east-1 ssm send-command --document-name AWS-RunShellScript --parameters 'commands=[\"true\"]' --instance-ids i-example",
            "aws ssm wait command-executed --command-id example --instance-id i-example",
            "git -C /tmp/project status --short",
            "cat /tmp/output | sed -n '/KEYS SET/,$p'; echo status; rg vendor docs/ | head -30",
            'aws ssm send-command --instance-ids i-fixture --document-name AWS-RunShellScript --parameters \'commands=["true"]\' --query Command.CommandId --output text > /tmp/id; sleep 8; aws ssm get-command-invocation --command-id "$(cat /tmp/id)" --instance-id i-fixture',
            "# read files\ncat Makefile # comment with a quote '\nrg vendor server/",
            "rg vendor server/\nrg provider server/\n",
            "\ncat Makefile\n\nsed -n '1,20p' README.md\n",
            "rg \\\n vendor server/",
            "memcap queue --json | head -c 1000\nmemcap wait abcdef12 --timeout 60",
            "memcap wait --session --timeout 60",
            "memcap report lightweight-queued --context ssm-control --wait-seconds 60",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(classify_shell(command)[0], "light")
                for agent, name, field in [
                    ("claude", "Bash", "command"),
                    ("codex", "exec_command", "cmd"),
                ]:
                    self.assertEqual(
                        hook_response(
                            {
                                "hook_event_name": "PreToolUse",
                                "tool_name": name,
                                "tool_input": {field: command},
                            },
                            "/opt/homebrew/opt/memcap/bin/memcap",
                            agent,
                        ),
                        {},
                    )

    def test_unknown_execution_stays_managed(self):
        for command in [
            "LC_ALL=C npm test",
            "BASH_ENV=/tmp/evil rg x .",
            'env -S "npm test"',
            "aws s3 sync s3://example .",
            "rg $(cat /tmp/options) file",
            'aws ssm get-command-invocation --command-id "$(cat /tmp/id; npm test)"',
            'aws ssm get-command-invocation --command-id "$(python worker.py)"',
            "cat file | sed -n '/pattern/,$p;e npm test'",
            "sleep 3600",
            "aws ssm start-session --target example",
            "aws ssm wait unknown",
            "aws --unknown value ssm send-command",
            "git -c core.pager=evil log",
            "git -C /tmp/project diff --ext-diff",
            "cat file\nnpm test",
            "cat file # note\nnpm test",
            "echo # '\nnpm test\n# '",
            "echo ''#literal && npm test",
            'echo ""#literal && npm test',
            "cat file\n$(npm test)",
            "cat <<EOF\nnpm test\nEOF",
            "cat file &\nnpm test",
            "memcap wait $(memcap queue | head)",
            "memcap wait --session; npm test",
        ]:
            with self.subTest(command=command):
                self.assertEqual(classify_shell(command)[0], "job")

    def test_cached_wrapper_reclassifies_before_any_sampling_or_reservation(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            env = {
                **os.environ,
                "MEMCAP_CONFIG_HOME": temp + "/config",
                "MEMCAP_STATE_HOME": temp + "/state",
                "MC_DRY_RUN": "1",
            }
            result = subprocess.run(
                [
                    sys.executable,
                    str(root / "libexec/scheduler.py"),
                    "run",
                    "--session-key",
                    "fixture",
                    "--shell-command",
                    "printf cached-wrapper-ok",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "cached-wrapper-ok")
            self.assertFalse((Path(temp) / "state/memcap/queue").exists())

    def test_explicit_reservations_and_heavy_cached_wrappers_keep_admission(self):
        import scheduler

        for options in [
            ["--memory", "2", "--shell-command", "cat file"],
            ["--resource", "server", "--shell-command", "cat file"],
            ["--shell-command", "npm test"],
            ["--shell", "/tmp/unknown-shell", "--shell-command", "cat file"],
        ]:
            with (
                self.subTest(options=options),
                patch.object(
                    sys, "argv", ["memcap", "run", "--session-key", "fixture", *options]
                ),
                patch.object(scheduler.Scheduler, "run", return_value=37) as run,
            ):
                self.assertEqual(scheduler.main(), 37)
                run.assert_called_once()

    def test_session_wait_does_not_lock_write_or_reserve_and_ignores_other_agents(self):
        import scheduler, idle_gc

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            queue = scheduler.Scheduler(root / "queue")
            queue.directory.mkdir()
            current = str(os.getpid())
            table = {
                current: {"ppid": 10, "command": "python wait", "start": "s"},
                "10": {"ppid": 1, "command": "claude", "start": "a"},
                "20": {"ppid": 1, "command": "claude", "start": "b"},
                "30": {"ppid": 10, "command": "python runner", "start": "s"},
                "40": {"ppid": 20, "command": "python runner", "start": "s"},
            }
            jobs = [
                dict(
                    id="abcdef123456",
                    owner=30,
                    owner_start="s",
                    group=0,
                    members={},
                    status="waiting",
                    resource="",
                    cancel=False,
                ),
                dict(
                    id="fedcba987654",
                    owner=40,
                    owner_start="s",
                    group=0,
                    members={},
                    status="waiting",
                    resource="",
                    cancel=False,
                ),
            ]
            path = queue.directory / "jobs.json"
            path.write_text(json.dumps({"jobs": jobs}))
            before = path.read_bytes()
            output = io.StringIO()
            with (
                patch.object(idle_gc, "process_table", return_value=table),
                patch.object(scheduler, "processes", return_value=table),
                patch.object(
                    queue, "locked", side_effect=AssertionError("must not lock")
                ),
                redirect_stdout(output),
            ):
                self.assertEqual(queue.wait_for("--session", 0), 0)
                self.assertEqual(
                    [
                        j["id"]
                        for j in idle_gc.Collector(root).pending_jobs(current, table)
                    ],
                    ["abcdef123456"],
                )
            self.assertIn("pending (waiting)", output.getvalue())
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(queue.directory.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
