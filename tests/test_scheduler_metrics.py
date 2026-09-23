"""Numeric telemetry must not invent capacity or retain private tool input."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))


class MetricsTests(unittest.TestCase):
    def test_rate_uses_actual_page_size_and_elapsed_time(self):
        from scheduler_metrics import rate

        for page, expected in ((4096, 256), (16384, 1024)):
            before = dict(monotonic=10, boot_id="boot", page_bytes=page, swapouts=100)
            after = dict(monotonic=12, boot_id="boot", page_bytes=page, swapouts=228)
            self.assertEqual(rate(before, after, "swapouts"), expected)

    def test_unknown_reset_and_changed_boot_are_not_zero_traffic(self):
        from scheduler_metrics import rate

        before = dict(monotonic=10, boot_id="boot", page_bytes=16384, swapouts=100)
        for change in (
            {"monotonic": 10},
            {"monotonic": 9},
            {"swapouts": 90},
            {"boot_id": "another"},
            {"page_bytes": 4096},
            {"swapouts": float("nan")},
            {"swapouts": True},
        ):
            with self.subTest(change=change):
                self.assertIsNone(
                    rate(before, {**before, "monotonic": 12, **change}, "swapouts")
                )
        self.assertIsNone(rate({}, before, "swapouts"))

    def test_vm_stat_and_boot_identity_parse_without_assuming_page_size(self):
        from scheduler_metrics import parse_vm

        got = parse_vm(
            "Mach Virtual Memory Statistics: (page size of 4096 bytes)\n"
            "Swapins: 123.\nSwapouts: 456.\nPages occupied by compressor: 789.\n"
        )
        self.assertEqual(
            got, dict(page_bytes=4096, swapins=123, swapouts=456, compressor_kb=3156)
        )
        self.assertEqual(parse_vm("Swapins: 123.\n"), {})

    def test_event_retention_uses_oldest_event_not_continually_updated_mtime(self):
        import time
        from scheduler_metrics import append_event

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text(
                json.dumps(dict(event="sample", timestamp=time.time() - 90000)) + "\n"
            )
            append_event(Path(tmp), dict(event="sample", pressure=2))
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertGreater(rows[0]["timestamp"], time.time() - 5)

    def test_events_reject_private_strings_and_are_bounded(self):
        from scheduler_metrics import append_event

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            append_event(
                directory,
                dict(
                    event="admitted",
                    queue_wait_ms=125,
                    command="SECRET",
                    cwd="/private/path",
                    pid=123,
                ),
            )
            rows = list(directory.glob("*.jsonl"))
            self.assertEqual(len(rows), 1)
            content = rows[0].read_text()
            self.assertNotIn("SECRET", content)
            self.assertNotIn("/private/path", content)
            self.assertNotIn('"pid"', content)
            self.assertEqual(json.loads(content)["queue_wait_ms"], 125)
            self.assertEqual(rows[0].stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
