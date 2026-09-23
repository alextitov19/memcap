"""Queue storm regressions: temporary state and synthetic measurements only."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
import scheduler
from scheduler_policy import hook_response

TICK = "end=$((SECONDS+58))\nwhile [ $SECONDS -lt $end ]; do sleep 10; done\necho tick"
FILE_POLL = 'F=/private/tmp/claude-501/project/session/tasks/task.output; until [ -s "$F" ] && ! grep -q "memcap: queued" "$F" 2>/dev/null; do sleep 10; done; echo "=== DOCS READY ==="; cat "$F"'


class StormTests(unittest.TestCase):
    def test_polling_loop_is_denied_before_a_new_job_is_created(self):
        for command in [
            TICK,
            TICK.replace("echo tick", 'echo "drain tick done"'),
            FILE_POLL,
        ]:
            for agent in ["claude", "codex"]:
                out = hook_response(
                    dict(
                        hook_event_name="PreToolUse",
                        tool_name="Bash",
                        tool_input={"command": command},
                    ),
                    "/opt/homebrew/opt/memcap/bin/memcap",
                    agent,
                )
                decision = out.get("hookSpecificOutput", {})
                self.assertEqual(decision.get("permissionDecision"), "deny")
                self.assertNotIn("updatedInput", decision)
                self.assertIn(
                    "TaskOutput", decision.get("permissionDecisionReason", "")
                )

    def test_later_tick_and_output_file_poll_variants_are_also_rejected(self):
        commands = [
            'end=$((SECONDS+58)); while [ $SECONDS -lt $end ]; do sleep 10; done; echo "poll tick"',
            'f=/private/tmp/claude-501/project/session/tasks/task.output\nend=$((SECONDS+58))\nwhile [ $SECONDS -lt $end ]; do\n  grep -q "RENDER_COMPLETE\\|exited with code" "$f" 2>/dev/null && break\n  sleep 6\ndone\ntail -n 60 "$f"',
        ]
        for command in commands:
            response = hook_response(
                dict(
                    hook_event_name="PreToolUse",
                    tool_name="Bash",
                    tool_input={"command": command},
                ),
                "memcap",
                "claude",
            )["hookSpecificOutput"]
            self.assertEqual(response.get("permissionDecision"), "deny")
            self.assertNotIn("updatedInput", response)
        from scheduler_policy import polling_loop

        for command in commands:
            self.assertFalse(polling_loop(command + "; make build"))

    def test_sampling_waiters_reuse_one_sample_without_releasing_reservations(self):
        sample = dict(
            cap_kb=20 * 1048576,
            tracked_kb=0,
            available_kb=20 * 1048576,
            pressure=1,
            fault=False,
            footprints={},
            tracked_pids=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            count = Path(tmp) / "calls"

            def measure():
                with count.open("a") as f:
                    f.write("sample\n")
                return dict(sample)

            with (
                patch("scheduler.sample_host", measure),
                patch("scheduler_metrics.vm_sample", return_value={}),
            ):
                for _ in range(24):
                    q = scheduler.Scheduler(Path(tmp) / "queue", sampler=measure)
                    observed = q.measure()
                    self.assertEqual(
                        q.admissible([], 2 * 1048576, "", observed),
                        (True, "capacity available"),
                    )
                self.assertEqual(len(count.read_text().splitlines()), 1)
                # Cache now belongs to the sampler, independent of the registry.
                path = q.directory / "sample.json"
                cached = json.loads(path.read_text())
                cached["sample"]["monotonic"] -= 3
                path.write_text(json.dumps(cached))
                q.measure()
                self.assertEqual(len(count.read_text().splitlines()), 2)

    def test_wait_fallback_is_read_only_bounded_and_never_queued(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "state/memcap/queue"
            queue.mkdir(parents=True)
            own = scheduler.processes()[str(os.getpid())]
            job = dict(
                id="a" * 32,
                owner=os.getpid(),
                owner_start=own["start"],
                status="waiting",
                group=0,
                members={},
                memory_kb=2097152,
                resource="",
                cwd=tmp,
                label="bash",
            )
            registry = queue / "jobs.json"
            registry.write_text(json.dumps({"jobs": [job]}))
            before = registry.read_bytes()
            import fcntl

            lock = (queue / "lock").open("w")
            self.addCleanup(lock.close)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            import subprocess

            env = {
                **os.environ,
                "MEMCAP_STATE_HOME": str(root / "state"),
                "MEMCAP_CONFIG_HOME": str(root / "config"),
                "MC_DRY_RUN": "1",
            }
            result = subprocess.run(
                [
                    str(Path(__file__).resolve().parents[1] / "bin/memcap"),
                    "wait",
                    "aaaaaaaa",
                    "--timeout",
                    "0.05",
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("pending", result.stdout)
            self.assertEqual(registry.read_bytes(), before)
            payload = dict(
                hook_event_name="PreToolUse",
                tool_name="Bash",
                tool_input={"command": "memcap wait aaaaaaaa --timeout 60"},
            )
            self.assertEqual(hook_response(payload, "memcap", "claude"), {})

    def test_red_pressure_after_cached_green_sample_prevents_launch(self):
        sample = dict(
            cap_kb=20971520,
            tracked_kb=0,
            available_kb=20971520,
            pressure=1,
            fault=False,
            footprints={},
            tracked_pids=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "must-not-exist"

            def measure():
                return sample

            with (
                patch("scheduler.sample_host", measure),
                patch("scheduler.current_pressure", return_value=4),
            ):
                q = scheduler.Scheduler(
                    Path(tmp) / "queue", sampler=measure, max_pressure="yellow"
                )
                self.assertEqual(q.run(["/usr/bin/touch", str(marker)], wait=0), 75)
                self.assertFalse(marker.exists())

    def test_cached_sample_never_authorizes_red_pressure(self):
        self.assertFalse(scheduler.pressure_allows((1, 2), lambda: 4))
        self.assertFalse(scheduler.pressure_allows((1, 2), lambda: 0))
        self.assertTrue(scheduler.pressure_allows((1, 2), lambda: 2))


if __name__ == "__main__":
    unittest.main()
