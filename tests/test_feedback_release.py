"""September 24 feedback: synthetic commands and measurements, never host kills."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from scheduler_policy import classify_shell
from scheduler import Scheduler
from admission import decide
from scheduler_metrics import append_event

GIB = 1048576


class FeedbackReleaseTests(unittest.TestCase):
    def test_reported_inspection_is_light(self):
        for cmd in [
            'rg -v "^\\s*$" file.ts',
            "sed -n '/start/,/end/p' file.txt",
            "pgrep -fl GradleDaemon",
            "pgrep -af gradle",
            "memcap --version",
            "memcap -v",
            # Missing timeout reaches immediate read-only CLI validation.
            "memcap wait --session --timeout 60>/dev/null",
            "cat file | tr -d '\\r' | head -10",
            "memcap wait --session --timeout 60 >/dev/null 2>&1",
            "memcap wait abcdef12 --timeout 60 1>/dev/null 2>&1",
            'aws ssm send-command --parameters \'commands=["echo $HOME"]\' > /tmp/id; sleep 8; aws ssm get-command-invocation --command-id "$(cat /tmp/id)"',
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(classify_shell(cmd)[0], "light")

    def test_shell_execution_remains_managed(self):
        for cmd in [
            "rg $'--pre=helper' file",
            'rg $"--pre=helper" file',
            'rg "$[1+2]" file',
            'rg "$PATTERN" file',
            'rg "$(build)" file',
            'rg "`build`" file',
            'sed -n "/start/,/end/ep" file',
            'sed -n "/a/p; e build" file',
            "cat file | node build.js",
            "pgrep node; npm test",
            'memcap wait --session --timeout "60">/dev/null; npm test',
            "cat file)",
            "(cat file)",
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(classify_shell(cmd)[0], "job")

    def test_same_sample_membership_growth_increases_window_peak(self):
        job = self.job()
        for stamp in range(100, 163, 2):
            with (
                patch("scheduler.time.monotonic", return_value=stamp),
                patch("scheduler.time.time", return_value=stamp),
            ):
                job["reservation_kb"] = Scheduler.reservation(
                    job, self.sample(stamp), GIB // 4, adaptive=True
                )
        with (
            patch("scheduler.time.monotonic", return_value=162),
            patch("scheduler.time.time", return_value=162),
        ):
            self.assertEqual(
                Scheduler.reservation(
                    job,
                    {**self.sample(162), "footprints": {"42": 6 * GIB}},
                    6 * GIB,
                    adaptive=True,
                ),
                int(7.5 * GIB),
            )

    def job(self):
        return dict(
            memory_kb=GIB,
            reservation_kb=5 * GIB,
            elastic=True,
            members={"42": "identity"},
            observed_peak_kb=4 * GIB,
            started=1,
            status="running",
            resource="",
        )

    def sample(self, stamp=100, **changes):
        return dict(
            fault=False,
            pressure=2,
            monotonic=stamp,
            footprints={"42": GIB // 4},
            **changes,
        )

    def test_incomplete_or_orphaned_measurement_retains_previous_allowance(self):
        for change in [dict(fault=True), dict(footprints={})]:
            job = self.job()
            sample = {**self.sample(), **change}
            self.assertEqual(Scheduler.reservation(job, sample, 0), 5 * GIB)
        job = {**self.job(), "orphaned": True}
        self.assertEqual(Scheduler.reservation(job, self.sample(), GIB // 4), 5 * GIB)

    def test_adaptive_shrinks_only_after_continuous_complete_window(self):
        job = self.job()
        for stamp in range(100, 163, 2):
            with (
                patch("scheduler.time.monotonic", return_value=stamp),
                patch("scheduler.time.time", return_value=stamp),
            ):
                reserve = Scheduler.reservation(
                    job, self.sample(stamp), GIB // 4, adaptive=True
                )
            if stamp < 160:
                self.assertEqual(reserve, 5 * GIB)
            job["reservation_kb"] = reserve
        self.assertEqual(reserve, GIB // 2)
        self.assertEqual(job["observed_peak_kb"], 4 * GIB)
        with patch("scheduler.time.monotonic", return_value=164):
            self.assertEqual(
                Scheduler.reservation(
                    job,
                    {**self.sample(164), "footprints": {"42": 6 * GIB}},
                    6 * GIB,
                    adaptive=True,
                ),
                int(7.5 * GIB),
            )

    def test_stale_and_gapped_samples_never_decay(self):
        job = self.job()
        for stamp in (100, 102, 160, 162):
            with (
                patch("scheduler.time.monotonic", return_value=stamp),
                patch("scheduler.time.time", return_value=stamp),
            ):
                self.assertEqual(
                    Scheduler.reservation(
                        job, self.sample(stamp), GIB // 4, adaptive=True
                    ),
                    5 * GIB,
                )
        with patch("scheduler.time.monotonic", return_value=300):
            self.assertEqual(
                Scheduler.reservation(job, self.sample(162), GIB // 4, adaptive=True),
                5 * GIB,
            )

    def test_replay_headroom_admits_after_a_sustained_lull_but_red_still_blocks(self):
        job = self.job()
        policy = dict(
            mode="adaptive", max_jobs=12, headroom_kb=2 * GIB, allowed_pressure=(1, 2)
        )

        def decision(stamp, pressure=2):
            s = {
                **self.sample(stamp),
                "cap_kb": 20 * GIB,
                "tracked_kb": 18 * GIB,
                "available_kb": int(2.88 * GIB),
                "tracked_pids": [42],
                "pressure": pressure,
            }
            return decide(
                policy,
                s,
                dict(now=stamp, healthy_since=90, last_start=90),
                [job],
                dict(memory_kb=GIB, resource=""),
            )

        self.assertFalse(decision(100)["allow"])
        for stamp in range(100, 163, 2):
            with (
                patch("scheduler.time.monotonic", return_value=stamp),
                patch("scheduler.time.time", return_value=stamp),
            ):
                job["reservation_kb"] = Scheduler.reservation(
                    job, self.sample(stamp), GIB // 4, adaptive=True
                )
        self.assertTrue(decision(162)["allow"])
        self.assertFalse(decision(162, pressure=4)["allow"])

    def test_no_decay_for_explicit_or_strict_jobs(self):
        for explicit in (False, True):
            job = self.job()
            job["elastic"] = not explicit
            for stamp in range(100, 180, 2):
                with (
                    patch("scheduler.time.monotonic", return_value=stamp),
                    patch("scheduler.time.time", return_value=stamp),
                ):
                    self.assertEqual(
                        Scheduler.reservation(
                            job, self.sample(stamp), GIB // 4, adaptive=explicit
                        ),
                        5 * GIB,
                    )

    def test_headroom_decision_explains_unused_reservations(self):
        job = self.job()
        sample = {
            **self.sample(),
            "cap_kb": 20 * GIB,
            "tracked_kb": 18 * GIB,
            "available_kb": 3 * GIB,
            "tracked_pids": [42],
        }
        decision = decide(
            dict(
                mode="adaptive",
                max_jobs=12,
                headroom_kb=2 * GIB,
                allowed_pressure=(1, 2),
            ),
            sample,
            dict(now=100, healthy_since=90, last_start=90),
            [job],
            dict(memory_kb=GIB, resource=""),
        )
        self.assertFalse(decision["allow"])
        self.assertEqual(decision["outstanding_kb"], int(4.75 * GIB))
        self.assertEqual(decision["headroom_deficit_kb"], int(3.25 * GIB))

    def test_telemetry_distinguishes_signal_and_exit_and_correlates_jobs(self):
        from scheduler_metrics import completion_fields

        self.assertEqual(
            completion_fields(15), dict(exit_code=15, signal=0, completion_kind=1)
        )
        self.assertEqual(
            completion_fields(-15), dict(exit_code=143, signal=15, completion_kind=2)
        )
        self.assertEqual(completion_fields(-15, 2)["completion_kind"], 3)
        with tempfile.TemporaryDirectory() as tmp:
            append_event(
                Path(tmp),
                dict(
                    event="completed",
                    job_ref=123,
                    exit_code=143,
                    signal=15,
                    completion_kind=2,
                    command="SECRET",
                ),
            )
            row = json.loads((Path(tmp) / "events.jsonl").read_text())
            self.assertEqual(row["job_ref"], 123)
            self.assertEqual(row["signal"], 15)
            self.assertNotIn("command", row)

    def test_wait_excludes_verified_own_lease_but_not_a_forged_environment(self):
        from scheduler import wait_targets

        jobs = [
            dict(id="own", members={"42": "birth"}),
            dict(id="other", members={"43": "other-birth"}),
        ]
        table = {
            "44": {"ppid": 42, "start": "child"},
            "42": {"ppid": 1, "start": "birth"},
        }
        self.assertEqual([j["id"] for j in wait_targets(jobs, "44", table)], ["other"])
        table["42"]["start"] = "recycled"
        self.assertEqual(len(wait_targets(jobs, "44", table)), 2)

    def test_report_aggregates_effective_reservations_and_probe_status(self):
        from report_metrics import queue_facts, sanitize

        with tempfile.TemporaryDirectory() as tmp:
            q = Path(tmp) / "queue"
            q.mkdir()
            (q / "jobs.json").write_text(
                json.dumps(
                    dict(
                        jobs=[
                            dict(
                                status="running",
                                memory_kb=GIB,
                                reservation_kb=3 * GIB,
                                measured_kb=GIB,
                                measurement_complete=True,
                                resource="",
                            )
                        ]
                    )
                )
            )
            facts = queue_facts(Path(tmp))
            self.assertEqual(facts["running_reserved_kb"], 3 * GIB)
            self.assertEqual(facts["running_measured_kb"], GIB)
        self.assertEqual(
            sanitize(dict(measurement_probe_status=2))["measurement_probe_status"], 2
        )


if __name__ == "__main__":
    unittest.main()
