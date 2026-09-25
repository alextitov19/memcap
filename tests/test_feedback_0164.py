"""Reported September 25 command shapes; synthetic paths and no real workloads."""

from pathlib import Path
import sys
import tempfile
import subprocess
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from scheduler_policy import classify_shell, hook_response
import agent_diagnostics
from report import Reporter
from scheduler import Scheduler
from admission import decide


class FeedbackTests(unittest.TestCase):
    def test_sampler_contention_is_not_reported_as_a_failed_measurement(self):
        result = decide(
            dict(mode="adaptive"),
            dict(busy=True, fault=True),
            {},
            [],
            dict(memory_kb=1048576),
        )
        self.assertFalse(result["allow"])
        self.assertEqual(result["reason"], "sampling")

    def test_stale_cached_reads_do_not_erase_complete_window(self):
        gib = 1048576
        job = dict(
            memory_kb=gib,
            reservation_kb=5 * gib,
            elastic=True,
            members={"42": "start"},
            started=1,
            observed_peak_kb=4 * gib,
        )
        for stamp in range(100, 164, 3):
            sample = dict(monotonic=stamp, fault=False, footprints={"42": gib // 4})
            with (
                patch("scheduler.time.monotonic", return_value=stamp),
                patch("scheduler.time.time", return_value=stamp),
            ):
                job["reservation_kb"] = Scheduler.reservation(
                    job, sample, gib // 4, adaptive=True
                )
            with patch("scheduler.time.monotonic", return_value=stamp + 2.5):
                retained = Scheduler.reservation(job, sample, gib // 4, adaptive=True)
                self.assertGreaterEqual(retained, job["reservation_kb"])
                job["reservation_kb"] = retained
        self.assertEqual(job["reservation_kb"], gib // 2)

    def test_native_inspection_shapes(self):
        for command in [
            "cat ~/notes.md; rg pattern ~/.config/file | head -5",
            "cd /project; SP=/tmp/logs; rg -n pattern $SP/test.log | sort -u | head",
            "R=org/repo; gh run cancel -R $R 123; gh workflow disable -R $R 456",
            "memcap report --help | head -40",
            "memcap report status; gh auth status",
            "memcap wait bf02cox12 --timeout 60 | tail -3",
            "aws sts get-caller-identity --profile example --query Arn --output text",
            "adb devices -l; xcrun simctl list devices available",
            "sed 's/[0-9]\\{12\\}/ACCOUNT/' file",
            "awk '/1\\) \\[chromium\\]/{p=1} p{print} /Error Context/{if(p) exit}' file | head -50",
            'fd -i -e md "scope|agreement" . ~/Downloads | head',
        ]:
            with self.subTest(command=command):
                self.assertEqual(classify_shell(command)[0], "light")
                self.assertEqual(
                    hook_response(
                        dict(
                            hook_event_name="PreToolUse",
                            tool_name="Bash",
                            tool_input=dict(command=command),
                        ),
                        "/memcap",
                        "claude",
                    ),
                    {},
                )

    def test_execution_and_ambiguous_expansion_still_managed(self):
        for command in [
            "SP=$(build); cat $SP/file",
            "SP=/tmp; node $SP/build.js",
            "SP=/tmp; rg $SP; npm test",
            "BASH_ENV=/tmp/file; cat file",
            "SP=/tmp; cat ${SP:-$(build)}",
            "cat ~other/file",
            "sort --compress-program=build file",
            "sort --compress-p=build file",
            "fd -x build",
            "fd --exec-batch build",
            "sed 's/x/y/e' file",
            "awk 'BEGIN {system(\"build\")}'",
            "git commit -m update",
            "adb shell build",
            "aws s3 cp s3://bucket/file /tmp/file",
        ]:
            with self.subTest(command=command):
                self.assertEqual(classify_shell(command)[0], "job")

    def test_native_shell_preserves_home_and_alias_read_results(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "log").write_text("match\nother\nmatch\n")
            command = "SP=" + temp + "; cat ~/log | rg match | sort -u; head -1 $SP/log"
            self.assertEqual(classify_shell(command)[0], "light")
            result = subprocess.run(
                ["/bin/bash", "-c", command],
                env={**os.environ, "HOME": temp},
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "match\nmatch\n")

    def test_persistent_wrapped_commands(self):
        for command in [
            "cd /project && npm run dev > /tmp/server.log 2>&1",
            "cd /project; PORT=3000 npm start",
            "PORT=3000 npm start",
            "adb -s emulator-5554 logcat > /tmp/device.log",
        ]:
            with self.subTest(command=command):
                self.assertEqual(classify_shell(command)[0], "resource")
        for command in [
            "adb logcat -d",
            "adb logcat -t 100",
            "adb logcat -c",
            "npm start; npm test",
            "cd /project; npm run build && npm start",
        ]:
            self.assertNotEqual(classify_shell(command)[0], "resource")

    def test_inspection_output_does_not_trigger_fault_guidance(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(agent_diagnostics, "measure") as probe,
        ):
            for name, command in [
                ("Read", ""),
                ("Bash", "cat libexec/example.py"),
                ("exec_command", "rg timeout libexec"),
            ]:
                payload = dict(
                    hook_event_name="PostToolUse",
                    tool_name=name,
                    tool_input=dict(command=command),
                    tool_response=dict(
                        stdout='"memcap: queue lock unavailable"; timed out trying to boot simulator'
                    ),
                )
                self.assertEqual(agent_diagnostics.guidance(payload, Path(temp)), "")
            probe.assert_not_called()

    def test_report_symptom_is_fixed_private_vocabulary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reporter = Reporter(
                root / "config", root / "state", "0.16.4", snapshot=lambda: {}
            )
            result = reporter.report(
                "integration", symptom="persistent-work-pending", dry_run=True
            )
            self.assertIn("persistent-work-pending", Path(result["draft"]).read_text())
            with self.assertRaises(ValueError):
                reporter.report("integration", symptom="secret /project/path")

    def test_brief_guidance_has_no_host_probe(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(agent_diagnostics, "measure") as probe,
        ):
            text = agent_diagnostics.guidance(
                dict(hook_event_name="UserPromptSubmit"), Path(temp), brief=True
            )
            self.assertLess(len(text), 200)
            self.assertNotIn("MUST report", text)
            probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
