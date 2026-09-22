"""Read-only reporting fixtures; no live process, registry or network dependency."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.report = importlib.import_module("report")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)

    def probe(self, argv, **kwargs):
        if argv[-1] == "_queue-sample":
            out = "20971520 14000000 5000000 2 0\n"
        elif argv[-1] == "_report-sample":
            out = "20971520 14000000 5000000 2 0\n8000000 6000000 2000000\n8 2097152 2 2\n"
        elif argv == [
            "sysctl",
            "-n",
            "hw.memsize",
            "hw.logicalcpu",
            "hw.physicalcpu",
            "hw.optional.arm64",
        ]:
            out = "25769803776\n12\n12\n1\n"
        elif argv == ["sw_vers", "-productVersion"]:
            out = "26.0.1\n"
        elif argv == ["sysctl", "-n", "vm.swapusage"]:
            out = "total = 2048.00M  used = 512.50M  free = 1535.50M  (encrypted)\n"
        else:
            raise AssertionError(f"Unexpected probe: {argv}")
        return subprocess.CompletedProcess(argv, 0, out, "SECRET_STDERR")

    def capture(self, probe=None):
        with (
            patch("subprocess.run", side_effect=probe or self.probe),
            patch("os.getloadavg", return_value=(4.2, 2.1, 1.0)),
        ):
            return self.report.capture(self.state)

    def registry(self, jobs):
        path = self.state / "queue/jobs.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"jobs": jobs}))
        os.utime(path, (950, 950))
        return path

    def test_collects_machine_capacity_and_subtotals_without_process_output(self):
        (self.state / "paused").touch()
        facts = self.capture()
        self.assertEqual(facts.get("physical_memory_kb"), 25165824)
        self.assertEqual(facts["logical_cpus"], 12)
        self.assertEqual(facts["architecture"], 1)
        self.assertEqual(facts["macos_patch"], 1)
        self.assertEqual(facts["swap_used_kb"], 524800)
        self.assertEqual(facts["load_1m_milli"], 4200)
        self.assertEqual(facts["enforcement_paused"], 1)
        self.assertEqual(facts["docker_kb"], 6000000)
        self.assertEqual(facts["simulators_browsers_kb"], 2000000)
        self.assertEqual(facts["queue_headroom_kb"], 2097152)
        self.assertNotIn("SECRET", json.dumps(facts))

    def test_queue_aggregates_ages_reasons_and_sessions_without_identifiers(self):
        jobs = [
            {
                "status": "waiting",
                "enqueued": 100,
                "memory_kb": 2000,
                "session_key": "SECRET_A",
                "cwd": "/SECRET/project",
                "admission": {"reason": "budget", "at": 990},
            },
            {
                "status": "waiting",
                "enqueued": 800,
                "memory_kb": 3000,
                "session_key": "SECRET_A",
                "admission": {"reason": "headroom", "at": 995},
            },
            {
                "status": "waiting",
                "enqueued": 900,
                "memory_kb": 4000,
                "session_key": "SECRET_B",
            },
            {
                "status": "running",
                "started": 400,
                "memory_kb": 5000,
                "session_key": "SECRET_C",
                "resource": "SECRET_SERVER",
            },
        ]
        path = self.registry(jobs)
        before = path.read_bytes()
        with patch("time.time", return_value=1000):
            facts = self.capture()
        self.assertEqual(facts.get("waiting_oldest_seconds"), 900)
        self.assertEqual(facts["running_oldest_seconds"], 600)
        self.assertEqual(facts["waiting_sessions"], 2)
        self.assertEqual(facts["waiting_requested_kb"], 9000)
        self.assertEqual(facts["running_resources"], 1)
        self.assertEqual(facts["blocked_budget"], 1)
        self.assertEqual(facts["blocked_headroom"], 1)
        self.assertEqual(facts.get("blocked_unknown"), 1)
        self.assertEqual(facts["admission_oldest_seconds"], 10)
        self.assertEqual(facts["registry_age_seconds"], 50)
        self.assertNotIn("SECRET", json.dumps(facts))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((path.parent / "lock").exists())

    def test_malformed_and_future_queue_fields_are_unknown_not_zero(self):
        self.registry(
            [
                {
                    "status": "waiting",
                    "enqueued": 1100,
                    "memory_kb": True,
                    "session_key": [],
                    "admission": {"reason": "SECRET_REASON", "at": float("nan")},
                }
            ]
        )
        with patch("time.time", return_value=1000):
            facts = self.capture()
        self.assertEqual(facts["blocked_unknown"], 1)
        for key in (
            "waiting_oldest_seconds",
            "waiting_requested_kb",
            "waiting_sessions",
            "admission_oldest_seconds",
        ):
            self.assertNotIn(key, facts)
        self.assertNotIn("SECRET", json.dumps(facts))

    def test_failed_measurements_do_not_claim_zero_usage_or_healthy_pressure(self):
        def failed(argv, **kwargs):
            if argv[-1] == "_report-sample":
                return subprocess.CompletedProcess(
                    argv, 0, "20971520 1 1 0 1\n1 1 1\n8 2097152 2 2\n", ""
                )
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

        facts = self.capture(failed)
        self.assertEqual(facts.get("measurement_fault"), 1)
        for key in (
            "tracked_kb",
            "docker_kb",
            "pressure",
            "physical_memory_kb",
            "swap_used_kb",
        ):
            self.assertNotIn(key, facts)
        self.assertEqual(facts["queue_max_jobs"], 8)

    def test_unavailable_registry_is_distinct_from_empty_and_symlinks_are_ignored(self):
        self.assertNotIn("waiting", self.capture())

        path = self.registry([])
        self.assertEqual(self.capture()["waiting"], 0)
        path.unlink()
        secret = self.state / "private.json"
        secret.write_text('{"jobs": []}')
        path.symlink_to(secret)
        self.assertNotIn("waiting", self.capture())

    def test_corrupt_oversized_timestamps_do_not_crash_draft_collection(self):
        self.registry(
            [
                {
                    "status": "waiting",
                    "enqueued": 10**500,
                    "admission": {"reason": "budget", "at": 10**500},
                }
            ]
        )
        facts = self.capture()
        self.assertEqual(facts["waiting"], 1)
        self.assertEqual(facts["blocked_unknown"], 1)
        self.assertNotIn("waiting_oldest_seconds", facts)


if __name__ == "__main__":
    unittest.main()
