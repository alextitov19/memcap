"""Behavioral fixtures own all processes and state; never invoke enforcement."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libexec"))


def healthy():
    return {
        "cap_kb": 16 * 1048576,
        "tracked_kb": 0,
        "available_kb": 16 * 1048576,
        "pressure": 1,
        "fault": False,
        "footprints": {},
        "tracked_pids": [],
    }


class SchedulerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = __import__("scheduler")
        cls.policy = __import__("scheduler_policy")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def queue(self, **kwargs):
        return self.mod.Scheduler(
            self.root / "queue", sampler=healthy, poll=0.02, **kwargs
        )

    def fixture(self, code, wait=5, resource="", sample=None):
        """Separate interpreters exercise the real OS lock, registry and runner."""
        driver = (
            "import sys,json;sys.path.insert(0,sys.argv[1]);"
            "from scheduler import Scheduler;"
            "s=Scheduler(sys.argv[2],sampler=lambda:json.loads(sys.argv[3]),"
            "poll=0.02);"
            "sys.exit(s.run([sys.executable,'-c',sys.argv[4]],"
            "wait=float(sys.argv[5]),resource=sys.argv[6]))"
        )
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                driver,
                str(ROOT / "libexec"),
                str(self.root / "queue"),
                json.dumps(sample or healthy()),
                code,
                str(wait),
                resource,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "MC_DRY_RUN": "1"},
        )
        # Fixture commands are bounded and exit on their own even on assertion failure.
        self.addCleanup(lambda: proc.communicate(timeout=15))
        return proc

    def test_five_simultaneous_jobs_never_exceed_two(self):
        events = self.root / "events"
        gate = self.root / "release"
        code = (
            "import os,time;from pathlib import Path;"
            f"f=open({str(events)!r},'a',buffering=1);"
            "f.write('start\\n');deadline=time.monotonic()+6\n"
            f"while not Path({str(gate)!r}).exists() and time.monotonic()<deadline: time.sleep(.02)\n"
            "f.write('end\\n')"
        )
        procs = [self.fixture(code) for _ in range(5)]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            records = self.queue().status()
            if (
                len(records) == 5
                and events.exists()
                and events.read_text().count("start") >= 2
            ):
                break
            time.sleep(0.02)
        # Keep both slots occupied long enough for every competing interpreter
        # to reach admission. Fast fixture exits used to hide a missing guard.
        time.sleep(0.5)
        gate.touch()
        for proc in procs:
            out, err = proc.communicate(timeout=15)
            self.assertEqual(proc.returncode, 0, err)
        active = peak = starts = 0
        for event in events.read_text().splitlines():
            active += 1 if event == "start" else -1
            starts += event == "start"
            peak = max(peak, active)
        self.assertEqual(starts, 5)
        self.assertEqual(active, 0)
        self.assertEqual(peak, 2)

    def test_pressure_and_unknown_measurement_never_launch(self):
        for change in ({"pressure": 2}, {"fault": True}, {"available_kb": 1}):
            marker = self.root / "forbidden"
            proc = self.fixture(
                f"open({str(marker)!r},'w').close()",
                wait=0.1,
                sample={**healthy(), **change},
            )
            _, err = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 75, err)
            self.assertFalse(marker.exists())

    def test_yellow_policy_allows_warning_but_never_red_or_unknown(self):
        q = self.queue(max_pressure="yellow")
        for pressure in (1, 2):
            self.assertTrue(
                q.admissible([], 2 * 1048576, "", {**healthy(), "pressure": pressure})[
                    0
                ]
            )
        for pressure in (0, 3, 4, 5, -1):
            self.assertFalse(
                q.admissible([], 2 * 1048576, "", {**healthy(), "pressure": pressure})[
                    0
                ]
            )

    def test_yellow_policy_keeps_budget_headroom_and_measurement_guards(self):
        q = self.queue(max_pressure="yellow")
        for change in (
            {"tracked_kb": 15 * 1048576},
            {"available_kb": 4 * 1048576},
            {"fault": True},
        ):
            self.assertFalse(
                q.admissible(
                    [], 2 * 1048576, "", {**healthy(), "pressure": 2, **change}
                )[0]
            )

    def test_pressure_policy_rejects_red_and_misspellings(self):
        for value in ("red", "Yellow", "", None, 2):
            with self.assertRaises(self.mod.QueueError):
                self.queue(max_pressure=value)

    def test_output_stderr_and_exit_status_survive(self):
        proc = self.fixture(
            "import sys;print('out');print('err',file=sys.stderr);sys.exit(7)"
        )
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 7)
        self.assertEqual(out, "out\n")
        self.assertIn("err", err)

    def test_reservations_hold_capacity_before_memory_is_allocated(self):
        sample = {**healthy(), "tracked_kb": 12 * 1048576}
        q = self.queue()
        jobs = [
            {
                "status": "running",
                "memory_kb": 3 * 1048576,
                "resource": "",
                "members": {},
            }
        ]
        allowed, reason = q.admissible(jobs, 2 * 1048576, "", sample)
        self.assertFalse(allowed, reason)

    def test_running_memory_is_not_double_charged(self):
        q = self.queue()
        sample = {
            **healthy(),
            "tracked_kb": 14 * 1048576,
            "footprints": {"42": 2 * 1048576},
            "tracked_pids": [42],
        }
        jobs = [
            {
                "status": "running",
                "memory_kb": 2 * 1048576,
                "resource": "",
                "members": {"42": "identity"},
            }
        ]
        allowed, reason = q.admissible(jobs, 2 * 1048576, "", sample)
        self.assertTrue(allowed, reason)

    def test_resource_memory_counts_without_consuming_finite_slots(self):
        q = self.queue()
        jobs = [
            {
                "status": "running",
                "memory_kb": 1048576,
                "resource": "server",
                "members": {},
            }
            for _ in range(2)
        ]
        self.assertTrue(q.admissible(jobs, 1048576, "", healthy())[0])
        self.assertFalse(
            q.admissible(jobs, 1048576, "", {**healthy(), "tracked_kb": 14 * 1048576})[
                0
            ]
        )

    def record(self, **updates):
        job = {
            "id": "lease",
            "owner": 100,
            "owner_start": "original owner",
            "group": 200,
            "members": {"201": "original child"},
            "status": "running",
            "resource": "",
            "cwd": str(self.root),
            "memory_kb": 2 * 1048576,
            "enqueued": 1,
            "label": "fixture",
            "cancel": True,
        }
        job.update(updates)
        return job

    def test_dead_supervisor_does_not_free_a_surviving_child_reservation(self):
        q = self.queue()
        data = {"jobs": [self.record()]}
        q.refresh(
            data,
            {
                "201": {
                    "ppid": 1,
                    "group": 200,
                    "uid": os.getuid(),
                    "start": "original child",
                }
            },
        )
        self.assertEqual(len(data["jobs"]), 1)
        self.assertTrue(data["jobs"][0]["orphaned"])

    def test_dead_waiter_and_finished_group_release_their_reservations(self):
        data = {"jobs": [self.record(), self.record(id="waiting", status="waiting")]}
        self.queue().refresh(data, {})
        self.assertEqual(data["jobs"], [])

    def test_cancellation_requires_live_owner_and_unchanged_child_identity(self):
        q = self.queue()
        with q.locked() as data:
            data["jobs"] = [self.record()]
            q.save(data)
        table = {
            "100": {
                "ppid": 1,
                "group": 100,
                "uid": os.getuid(),
                "start": "original owner",
            },
            "201": {
                "ppid": 1,
                "group": 200,
                "uid": os.getuid(),
                "start": "original child",
            },
        }
        with patch.object(self.mod, "processes", return_value=table):
            self.assertEqual(q.authorize_cancel("lease", 100), ["201"])
            self.assertEqual(q.authorize_cancel("lease", 101), [])
            table["201"]["start"] = "reused PID"
            self.assertEqual(q.authorize_cancel("lease", 100), [])
            table["201"]["start"] = "original child"
            table["201"]["uid"] += 1
            self.assertEqual(q.authorize_cancel("lease", 100), [])

    def test_pause_prevents_cancellation_even_for_an_authorized_owner(self):
        q = self.queue()
        with q.locked() as data:
            data["jobs"] = [self.record()]
            q.save(data)
        (self.root / "paused").touch()
        table = {
            "100": {"start": "original owner"},
            "201": {"group": 200, "uid": os.getuid(), "start": "original child"},
        }
        with patch.object(self.mod, "processes", return_value=table):
            self.assertEqual(q.authorize_cancel("lease", 100), [])

    def test_cancellation_during_admission_never_starts_command(self):
        q = self.queue()

        def sampler():
            q.handle_signal(15, None)
            return healthy()

        q.sampler = sampler
        marker = self.root / "cancelled"
        self.assertEqual(
            q.run([sys.executable, "-c", f"open({str(marker)!r},'w').close()"]), 143
        )
        self.assertFalse(marker.exists())
        self.assertEqual(q.status(), [])

    def test_nested_lease_needs_actual_process_ancestry(self):
        q = self.queue()
        table = {
            str(os.getpid()): {"ppid": 201, "start": "nested"},
            "201": {"ppid": 1, "start": "original child"},
        }
        with patch.dict(os.environ, {"MEMCAP_QUEUE_LEASE": "lease"}):
            self.assertTrue(q.nested({"jobs": [self.record()]}, table))
            table["201"]["start"] = "another process"
            self.assertFalse(q.nested({"jobs": [self.record()]}, table))

    def test_queue_recovers_and_launches_when_pressure_clears(self):
        q = self.queue()
        samples = iter([{**healthy(), "pressure": 4}, healthy()])
        q.sampler = lambda: next(samples)
        marker = self.root / "recovered"
        self.assertEqual(
            q.run([sys.executable, "-c", f"open({str(marker)!r},'w').close()"]), 0
        )
        self.assertTrue(marker.exists())

    def test_reservation_larger_than_machine_fails_without_waiting(self):
        with self.assertRaisesRegex(self.mod.QueueError, "exceeds"):
            self.queue().run(["must-not-execute"], memory_gb=32)

    def test_expired_request_does_not_launch_when_capacity_returns_late(self):
        q = self.queue()
        q.poll = 1.1
        samples = iter([{**healthy(), "pressure": 4}, healthy()])
        q.sampler = lambda: next(samples)
        marker = self.root / "too-late"
        self.assertEqual(
            q.run([sys.executable, "-c", f"open({str(marker)!r},'w').close()"], wait=1),
            75,
        )
        self.assertFalse(marker.exists())

    def test_go_program_arguments_and_vitest_minimum_keep_their_meaning(self):
        p = self.policy
        self.assertEqual(
            p.worker_argv(["go", "run", "main.go", "-p", "8080"], self.root, 2),
            ["go", "run", "main.go", "-p", "8080"],
        )
        self.assertEqual(
            p.worker_argv(["go", "test", "./...", "-args", "-p", "8080"], self.root, 2),
            ["go", "test", "-p=2", "./...", "-args", "-p", "8080"],
        )
        self.assertEqual(
            p.worker_argv(
                ["vitest", "run", "--maxWorkers=1", "--minWorkers=8"], self.root, 2
            ),
            ["vitest", "run", "--maxWorkers=1", "--minWorkers=1"],
        )

    def test_unavailable_process_table_does_not_discard_registry(self):
        q = self.queue()
        with q.locked() as data:
            data["jobs"] = [self.record()]
            q.save(data)
        original = (q.directory / "jobs.json").read_bytes()
        with patch.object(
            self.mod, "processes", side_effect=self.mod.QueueError("unavailable")
        ):
            with self.assertRaises(self.mod.QueueError):
                q.status()
        self.assertEqual((q.directory / "jobs.json").read_bytes(), original)

    def test_ordinary_background_child_keeps_slot_after_parent_exit(self):
        code = "import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(.6)'])"
        start = time.monotonic()
        proc = self.fixture(code)
        _, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, err)
        self.assertGreaterEqual(time.monotonic() - start, 0.6)

    def test_resource_duplicate_does_not_start_another_process(self):
        started = self.root / "started"
        first = self.fixture(
            f"import time;open({str(started)!r},'w').close();time.sleep(1)",
            resource="dev",
        )
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(started.exists())
        second = self.fixture("print('DUPLICATE')", resource="dev")
        out, err = second.communicate(timeout=5)
        self.assertEqual(second.returncode, 0, err)
        self.assertNotIn("DUPLICATE", out)
        self.assertIn("already running", err)
        first.communicate(timeout=5)

    def test_malformed_registry_fails_closed(self):
        q = self.queue()
        q.directory.mkdir(parents=True)
        (q.directory / "jobs.json").write_text("not json")
        with self.assertRaises(self.mod.QueueError):
            q.run([sys.executable, "-c", "raise Exception('must not run')"], wait=0.1)

    def test_worker_flags_preserve_smaller_explicit_limits(self):
        p = self.policy
        for argv, expected in [
            (["vitest", "run"], ["vitest", "run", "--maxWorkers=2"]),
            (["jest", "--maxWorkers=16"], ["jest", "--maxWorkers=2"]),
            (["playwright", "test", "-j", "1"], ["playwright", "test", "--workers=1"]),
            (["jest", "--runInBand"], ["jest", "--runInBand"]),
            (["jest", "-w16"], ["jest", "--maxWorkers=2"]),
            (["go", "test", "-p", "16", "./..."], ["go", "test", "-p=2", "./..."]),
            (["cargo", "test", "-j8"], ["cargo", "test", "--jobs=2"]),
        ]:
            self.assertEqual(p.worker_argv(argv, self.root, 2), expected)

    def test_package_script_gets_worker_limit(self):
        (self.root / "package.json").write_text(
            json.dumps({"scripts": {"test": "vitest run"}})
        )
        self.assertEqual(
            self.policy.worker_argv(["npm", "test"], self.root, 2),
            ["npm", "test", "--", "--maxWorkers=2"],
        )

    def test_worker_environment_limits_are_inherited(self):
        env = self.policy.worker_environment(
            {"GOMAXPROCS": "1", "CARGO_BUILD_JOBS": "16"}, 2
        )
        self.assertEqual(env["GOMAXPROCS"], "1")
        self.assertEqual(env["CARGO_BUILD_JOBS"], "2")
        self.assertEqual(env["VITEST_MAX_WORKERS"], "2")

    def test_shell_classifier_does_not_trust_argument_matches(self):
        for command in ("rg 'npm test' README.md", "git diff --stat", "cat README.md"):
            self.assertEqual(self.policy.classify_shell(command)[0], "light")
        for command in (
            "echo $(npm test)",
            "cat <(npm test)",
            "git -c core.pager=evil diff",
            "rg foo && npm test",
            "python worker.py",
            "npm test",
        ):
            self.assertEqual(self.policy.classify_shell(command)[0], "job")

    def test_lightweight_inspection_and_ci_watch_do_not_reserve_build_slots(self):
        for command in (
            'rg -n "zip-counties|case" backend/cmd/ingest/main.go | head -30',
            'rg -n "zip-counties" backend/cmd/ingest/*.go',
            "memcap status 2>&1 | head -30",
            "memcap queue --json",
            "cd /tmp/project && cat Makefile",
            "sed -n '1,120p' fastlane/Fastfile",
            'gh run watch 123 -R owner/repo --exit-status > /tmp/watch.log 2>&1; echo "EXIT=$?" >> /tmp/watch.log; tail -6 /tmp/watch.log',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.policy.classify_shell(command)[0], "light")

    def test_lightweight_shell_parsing_keeps_execution_and_heavy_stages_queued(self):
        for command in (
            "rg x file | python worker.py",
            "cat Makefile && make archive",
            'echo "$(npm test)"',
            "echo `npm test`",
            "cat <(npm test)",
            "rg --pre ./expensive x file | head",
            "rg x *",
            'sed -n "1e npm test" file',
            "gh run view 123 --web",
            "memcap off",
            "cat file & npm test",
            "cat file\nnpm test",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.policy.classify_shell(command)[0], "job")

    def test_hook_rewrites_exact_command_without_running_it(self):
        command = "printf '%s' 'a; $(touch /tmp/never)' && npm test"
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "permission_mode": "bypassPermissions",
            "tool_input": {"command": command, "timeout": 120000},
            "cwd": str(self.root),
        }
        result = self.policy.hook_response(payload, "/path with spaces/memcap")
        updated = result["hookSpecificOutput"]["updatedInput"]
        import shlex

        args = shlex.split(updated["command"])
        self.assertIn(command, args)
        self.assertEqual(updated["timeout"], 120000)
        self.assertIn("--shell-command", args)

    def test_hooks_leave_non_shell_calls_and_small_commands_alone(self):
        for name, command in (("Bash", "git status --short"), ("Edit", "npm test")):
            self.assertEqual(
                self.policy.hook_response(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": name,
                        "tool_input": {"command": command},
                    },
                    "memcap",
                ),
                {},
            )

    def test_hook_preserves_codex_shell_workdir_and_login(self):
        import shlex

        result = self.policy.hook_response(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "permission_mode": "bypassPermissions",
                "tool_input": {
                    "cmd": "npm test",
                    "shell": "/bin/zsh",
                    "login": True,
                    "workdir": "/tmp/project space",
                    "yield_time_ms": 1000,
                },
            },
            "memcap",
        )
        updated = result["hookSpecificOutput"]["updatedInput"]
        args = shlex.split(updated["command"])
        self.assertEqual(args[args.index("--shell") + 1], "/bin/zsh")
        self.assertEqual(args[args.index("--cwd") + 1], "/tmp/project space")
        self.assertIn("--login", args)
        self.assertEqual(updated["yield_time_ms"], 1000)

    def test_hook_never_auto_approves_commands_in_a_restricted_session(self):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "permission_mode": "default",
            "tool_input": {"command": "npm test"},
        }
        codex = self.policy.hook_response(payload, "memcap", "codex")[
            "hookSpecificOutput"
        ]
        self.assertEqual(codex["permissionDecision"], "deny")
        self.assertIn("memcap run", codex["permissionDecisionReason"])
        claude = self.policy.hook_response(payload, "memcap", "claude")[
            "hookSpecificOutput"
        ]
        self.assertNotIn("permissionDecision", claude)
        self.assertIn("updatedInput", claude)

    def test_generated_wrapper_is_not_recursively_rewritten(self):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "permission_mode": "bypassPermissions",
            "tool_input": {"command": "npm test && echo done"},
        }
        first = self.policy.hook_response(payload, "memcap")["hookSpecificOutput"][
            "updatedInput"
        ]
        payload["tool_input"] = first
        self.assertEqual(self.policy.hook_response(payload, "memcap"), {})
        payload["tool_input"] = {"command": first["command"] + "; npm test"}
        self.assertNotEqual(self.policy.hook_response(payload, "memcap"), {})


if __name__ == "__main__":
    unittest.main()
