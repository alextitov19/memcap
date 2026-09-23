"""Behavioral fixtures own all processes and state; never invoke enforcement."""

import json
import io
from contextlib import redirect_stderr
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

    def fixture(self, code, wait=5, resource="", sample=None, session_key=""):
        """Separate interpreters exercise the real OS lock, registry and runner."""
        driver = (
            "import sys,json;sys.path.insert(0,sys.argv[1]);"
            "from scheduler import Scheduler;"
            "s=Scheduler(sys.argv[2],sampler=lambda:json.loads(sys.argv[3]),"
            "poll=0.02);"
            "sys.exit(s.run([sys.executable,'-c',sys.argv[4]],"
            "wait=float(sys.argv[5]),resource=sys.argv[6],session_key=sys.argv[7]))"
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
                session_key,
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
        # Admission must outlive the test's readiness wait and deliberate hold;
        # this checks concurrency, not a five-second deadline on CI hardware.
        procs = [self.fixture(code, wait=30) for _ in range(5)]
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

    def test_waiting_job_persists_last_admission_reason_for_read_only_reporting(self):
        proc = self.fixture(
            "raise RuntimeError('must not run')",
            wait=2,
            sample={**healthy(), "available_kb": 1},
        )
        deadline = time.monotonic() + 1.5
        record = {}
        while time.monotonic() < deadline:
            path = self.root / "queue/jobs.json"
            if path.exists():
                jobs = json.loads(path.read_text())["jobs"]
                if jobs and jobs[0].get("admission"):
                    record = jobs[0]["admission"]
                    break
            time.sleep(0.02)
        self.assertEqual(record.get("reason"), "headroom")
        self.assertGreater(record["at"], time.time() - 5)
        _, err = proc.communicate(timeout=5)
        self.assertEqual(proc.returncode, 75, err)

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
        q.sampler = lambda: next(samples, healthy())
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
        q.sampler = lambda: next(samples, healthy())
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
            r'grep -rln "weatherKitEntitled\|WeatherProviderFactory" KoreSkinSyncTests/ 2>/dev/null',
            "rg --files -g '*.swift' | head -20",
            "rg -n 'weather.*Entitled$' KoreSkinSyncTests/",
            'rg -n "zip-counties|case" backend/cmd/ingest/main.go | head -30',
            'rg -n "zip-counties" backend/cmd/ingest/*.go',
            "memcap status 2>&1 | head -30",
            "/opt/homebrew/opt/memcap/bin/memcap status 2>&1 | sed -n '1,20p'",
            "cat Makefile | sed -n '1p'",
            "memcap queue --json",
            "cd /tmp/project && cat Makefile",
            "sed -n '1,120p' fastlane/Fastfile",
            'gh run watch 123 -R owner/repo --exit-status > /tmp/watch.log 2>&1; echo "EXIT=$?" >> /tmp/watch.log; tail -6 /tmp/watch.log',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.policy.classify_shell(command)[0], "light")

    def test_performance_reports_remain_available_while_workloads_are_queued(self):
        for command in (
            "memcap report lightweight-queued",
            "memcap report polling-overhead --wait-seconds 120",
            "memcap report queue-stall --wait-seconds 120 --dry-run",
            "memcap report queue-stall --dry-run --wait-seconds 0",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.policy.classify_shell(command)[0], "light")
        for command in (
            "memcap report queue-stall --wait-seconds SECRET",
            "memcap report queue-stall --wait-seconds 1 --wait-seconds 2",
            "memcap report queue-stall --wait-seconds 120 && npm test",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.policy.classify_shell(command)[0], "job")

    def test_memory_capacity_can_admit_more_than_two_jobs(self):
        q = self.queue(max_jobs=8, headroom_gb=2, max_pressure="yellow")
        sample = healthy()
        sample["pressure"] = 2
        active = [
            dict(status="running", resource="", memory_kb=2 * 1048576, members={})
            for _ in range(3)
        ]
        self.assertTrue(q.admissible(active, 2 * 1048576, "", sample)[0])
        sample["pressure"] = 4
        self.assertFalse(q.admissible(active, 2 * 1048576, "", sample)[0])

        sample["pressure"] = 2
        sample["available_kb"] = 9 * 1048576
        self.assertFalse(q.admissible(active, 2 * 1048576, "", sample)[0])

    def test_diagnostic_reads_do_not_wait_for_build_capacity(self):
        for command in (
            "xcrun simctl list devices --json",
            "docker stats --no-stream",
            "docker ps",
            "docker ps -a",
            "docker buildx ls",
            "ps -Ao pid,ppid,command",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.policy.classify_shell(command)[0], "light")
        for command in (
            "docker stats",
            "docker buildx inspect --bootstrap",
            "docker buildx stop",
            "xcrun simctl shutdown all",
            "xcrun simctl boot DEVICE",
            "docker ps && npm test",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.policy.classify_shell(command)[0], "job")

    def test_claude_queued_jobs_wait_in_background_until_admitted(self):
        payload = dict(
            hook_event_name="PreToolUse",
            tool_name="Bash",
            cwd=str(self.root),
            tool_input=dict(command="make preflight", timeout=120000),
        )
        response = self.policy.hook_response(payload, "/opt/memcap", "claude")[
            "hookSpecificOutput"
        ]
        self.assertTrue(response["updatedInput"]["run_in_background"])
        self.assertIn("--wait-forever", response["updatedInput"]["command"])
        self.assertIn("TaskOutput", response["additionalContext"])

    def test_unlimited_queue_wait_polls_and_eventually_executes_once(self):
        calls = []

        def sample():
            calls.append(1)
            value = healthy()
            if len(calls) < 4:
                value["pressure"] = 4
            return value

        q = self.queue()
        q.sampler = sample
        marker = self.root / "ran"
        output = io.StringIO()
        with redirect_stderr(output):
            self.assertEqual(
                q.run(
                    [sys.executable, "-c", f"open({str(marker)!r},'a').write('once')"],
                    wait=None,
                ),
                0,
            )
        self.assertIn("keep polling", output.getvalue())
        self.assertIn("admitted", output.getvalue())
        self.assertEqual(marker.read_text(), "once")
        self.assertGreaterEqual(len(calls), 4)

    def test_large_waiter_does_not_block_smaller_job_that_fits(self):
        q = self.queue(max_jobs=8, headroom_gb=2)
        sample = healthy()
        sample["tracked_kb"] = 12 * 1048576
        jobs = [
            dict(id="large", status="waiting", resource="", memory_kb=6 * 1048576),
            dict(id="small", status="waiting", resource="", memory_kb=2 * 1048576),
        ]
        self.assertEqual(q.next_waiter(jobs, "", sample), "small")
        sample["tracked_kb"] = 8 * 1048576
        self.assertEqual(q.next_waiter(jobs, "", sample), "large")

    def test_session_rotation_applies_across_job_and_resource_types(self):
        q = self.queue()
        jobs = [
            dict(
                id=ident,
                status="waiting",
                resource=resource,
                memory_kb=1048576,
                session_key=session,
                enqueued=990,
            )
            for ident, session, resource in [
                ("a1", "a", ""),
                ("a2", "a", ""),
                ("b1", "b", "server"),
                ("c1", "c", ""),
            ]
        ]
        turns = {"a": 2, "b": 1}
        self.assertEqual(q.next_waiter(jobs, "", healthy(), turns, now=1000), "c1")
        turns["c"] = 3
        self.assertEqual(q.next_waiter(jobs, "", healthy(), turns, now=1000), "b1")
        turns["b"] = 4
        self.assertEqual(
            q.next_waiter(jobs, "server", healthy(), turns, now=1000), "a1"
        )

    def test_real_supervisors_rotate_sessions_when_slots_open(self):
        gate = self.root / "release"
        events = self.root / "fair-events"
        holder = (
            "import time;from pathlib import Path;"
            f"p=Path({str(gate)!r});deadline=time.monotonic()+8;"
            "exec('while not p.exists() and time.monotonic()<deadline: time.sleep(0.02)')"
        )
        holders = [self.fixture(holder, wait=30, session_key="a") for _ in range(2)]
        q = self.queue()
        deadline = time.monotonic() + 4
        while (
            sum(j["status"] == "running" for j in q.status()) < 2
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        self.assertEqual(sum(j["status"] == "running" for j in q.status()), 2)
        work = []
        for label, session in [("a1", "a"), ("a2", "a"), ("b", "b"), ("c", "c")]:
            code = f"import time;open({str(events)!r},'a').write({label!r}+'\\n');time.sleep(0.1)"
            work.append(self.fixture(code, wait=30, session_key=session))
        deadline = time.monotonic() + 4
        while (
            sum(j["status"] == "waiting" for j in q.status()) < 4
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        self.assertEqual(sum(j["status"] == "waiting" for j in q.status()), 4)
        gate.touch()
        for proc in holders + work:
            _, err = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, err)
        order = events.read_text().splitlines()
        self.assertEqual(set(order[:2]), {"b", "c"}, order)
        self.assertEqual(set(order[2:]), {"a1", "a2"}, order)

    def test_automatic_reservation_releases_unused_startup_allowance(self):
        q = self.queue(headroom_gb=2)
        sample = healthy()
        sample.update(
            cap_kb=20 * 1048576,
            tracked_kb=17 * 1048576,
            available_kb=5 * 1048576,
            footprints={"42": 10485},
            tracked_pids=[42],
        )
        job = dict(
            status="running",
            resource="",
            memory_kb=2 * 1048576,
            members={"42": "start"},
            elastic=True,
            started=time.time() - 60,
        )
        self.assertTrue(q.admissible([job], 2 * 1048576, "", sample)[0])
        # Explicit reservations, startup bursts, unknown members and lost owners retain capacity.
        for changes in (
            {"elastic": False},
            {"started": time.time()},
            {"members": {"43": "unknown"}},
            {"orphaned": True},
        ):
            self.assertFalse(
                q.admissible([{**job, **changes}], 2 * 1048576, "", sample)[0]
            )
        self.assertFalse(
            q.admissible([job], 2 * 1048576, "", {**sample, "pressure": 4})[0]
        )

    def test_observed_growth_is_counted_and_not_forgotten(self):
        q = self.queue(headroom_gb=2)
        sample = healthy()
        sample.update(
            cap_kb=20 * 1048576,
            tracked_kb=17 * 1048576,
            available_kb=5 * 1048576,
            footprints={"42": 10485},
            tracked_pids=[42],
        )
        job = dict(
            status="running",
            resource="",
            memory_kb=2 * 1048576,
            members={"42": "start"},
            elastic=True,
            started=time.time() - 60,
            observed_peak_kb=2 * 1048576,
        )
        self.assertFalse(q.admissible([job], 2 * 1048576, "", sample)[0])
        sample["footprints"]["42"] = 4 * 1048576
        sample["tracked_pids"] = []
        self.assertFalse(q.admissible([job], 2 * 1048576, "", sample)[0])

    def test_integration_diagnostics_and_setup_do_not_wait_for_a_job_slot(self):
        for command in (
            "memcap doctor",
            "memcap doctor --claude --no-runtime",
            "memcap integrate --codex",
            "memcap doctor --claude-dir '/tmp/profile space'",
        ):
            self.assertTrue(self.policy.light_shell(command), command)
        for command in (
            "memcap doctor && npm test",
            "memcap integrate $(npm test)",
            "memcap doctor --claude-dir",
            "memcap off",
        ):
            self.assertFalse(self.policy.light_shell(command), command)

    def test_aged_large_waiter_drains_new_admissions_without_interrupting_running_jobs(
        self,
    ):
        q = self.queue(headroom_gb=2)
        sample = healthy()
        sample["tracked_kb"] = 12 * 1048576
        jobs = [
            dict(
                id="large",
                status="waiting",
                resource="",
                memory_kb=6 * 1048576,
                session_key="a",
                enqueued=950,
            ),
            dict(
                id="small",
                status="waiting",
                resource="",
                memory_kb=1048576,
                session_key="b",
                enqueued=960,
            ),
        ]
        self.assertEqual(q.next_waiter(jobs, "", sample, {}, now=1000), "small")
        self.assertEqual(q.next_waiter(jobs, "", sample, {}, now=1011), "small")
        running = dict(status="running", resource="", memory_kb=1048576, members={})
        self.assertEqual(
            q.next_waiter(jobs + [running], "", sample, {}, now=1011), "large"
        )
        self.assertEqual([j["status"] for j in jobs], ["waiting", "waiting"])
        sample["tracked_kb"] = 8 * 1048576
        self.assertEqual(q.next_waiter(jobs, "", sample, {}, now=1012), "large")

    def test_lightweight_shell_parsing_keeps_execution_and_heavy_stages_queued(self):
        for command in (
            "rg x file | python worker.py",
            "cat Makefile && make archive",
            'echo "$(npm test)"',
            "echo `npm test`",
            "cat <(npm test)",
            "rg --pre ./expensive x file | head",
            "rg x *",
            "rg x {--pre,/tmp/worker}",
            "rg x ~[worker]",
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
