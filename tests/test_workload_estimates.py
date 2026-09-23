"""Whole-run estimates account for peaks and never expose command identities."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
GIB = 1048576


class EstimateTests(unittest.TestCase):
    def test_repeated_small_peaks_replace_blanket_reservation(self):
        from workload_estimates import estimate

        self.assertEqual(estimate(2 * GIB, [GIB // 4] * 20, 20), GIB // 2)

    def test_one_large_peak_increases_allowance_above_old_request(self):
        from workload_estimates import estimate

        self.assertEqual(estimate(GIB, [4 * GIB], 1), 5 * GIB)

    def test_uncertain_or_invalid_history_keeps_prior(self):
        from workload_estimates import estimate

        for peaks, complete in (([], 0), ([GIB // 4], 0), ([-1], 1), ([True], 1)):
            self.assertEqual(estimate(2 * GIB, peaks, complete), 2 * GIB)

    def test_history_records_only_completed_measured_runs_and_falls_gradually(self):
        from workload_estimates import record

        row = dict(estimate_kb=2 * GIB, peaks_kb=[], complete_runs=0)
        self.assertEqual(record(row, GIB // 4, complete=False), row)
        updated = record(row, GIB // 4, complete=True)
        self.assertGreaterEqual(updated["estimate_kb"], int(2 * GIB * 0.9))
        self.assertEqual(len(updated["peaks_kb"]), 1)
        raised = record(updated, 4 * GIB, complete=True)
        self.assertEqual(raised["estimate_kb"], 5 * GIB)

    def test_worker_configuration_and_project_change_private_identity(self):
        from workload_estimates import fingerprint

        first = dict(project="/secret/project", command="tool --secret abc", workers=2)
        token = fingerprint(first, b"local-private-key")
        self.assertEqual(len(token), 64)
        self.assertEqual(token, fingerprint(first, b"local-private-key"))
        self.assertNotEqual(
            token, fingerprint({**first, "workers": 4}, b"local-private-key")
        )
        self.assertNotEqual(token, fingerprint(first, b"another-machine-key"))


if __name__ == "__main__":
    unittest.main()
