"""Sleep and clock adjustment must not be conflated in telemetry."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
import job_timing as timing


class JobTimingTests(unittest.TestCase):
    def test_sleep_and_wall_adjustments(self):
        job = dict(enqueued_monotonic=100, enqueued_continuous=200)
        with patch.object(timing.time, 'monotonic', return_value=102), \
                patch.object(timing, 'continuous', return_value=262), \
                patch.object(timing.time, 'time', side_effect=AssertionError('wall clock used')):
            self.assertEqual(timing.phase_fields(job, 'queue_wait'), dict(
                queue_wait_awake_ms=2000, queue_wait_elapsed_ms=62000, queue_wait_sleep_ms=60000))

    def test_missing_or_inconsistent_clock_is_unknown(self):
        with patch.object(timing.time, 'monotonic', return_value=102), \
                patch.object(timing, 'continuous', return_value=None):
            self.assertEqual(timing.phase_fields(dict(start_monotonic=100), 'runtime'),
                             dict(runtime_awake_ms=2000))
        with patch.object(timing.time, 'monotonic', return_value=102), \
                patch.object(timing, 'continuous', return_value=201):
            self.assertEqual(timing.phase_fields(dict(start_monotonic=100, start_continuous=200), 'runtime'),
                             dict(runtime_awake_ms=2000))

    def test_unavailable_system_clock_has_no_wall_fallback(self):
        with patch.object(timing.time, 'clock_gettime', side_effect=OSError), \
                patch.object(timing.time, 'time', side_effect=AssertionError):
            self.assertIsNone(timing.continuous())


if __name__ == '__main__':
    unittest.main()
