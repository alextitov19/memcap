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
    def test_find_name_pipeline_with_word_count_stays_native(self):
        command = 'find src -name "*.swift" | xargs wc -l | sort -n | tail -120'
        self.assertEqual(classify_shell(command)[0], "light")
        for altered in (
            command.replace("xargs wc", "xargs -P 8 wc"),
            command.replace("xargs wc -l", "xargs sh -c build"),
            command.replace('-name "*.swift"', "-delete"),
            command.replace('-name "*.swift"', '-name "*.swift" -exec build {} +'),
        ):
            self.assertNotEqual(classify_shell(altered)[0], "light")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src/a.swift").write_text("one\ntwo\n")
            result = subprocess.run(
                ["/bin/bash", "-c", command],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.split(), ["2", "src/a.swift"])

    def test_literal_file_excerpt_helper_does_not_reserve_workload_memory(self):
        definition = (
            'show(){ echo "=== $1:$2"; sed -n "$(( $2-2 )),$(( $2+2 ))p" "$1"; }; '
        )
        command = "cd /tmp && " + definition + "show src/a.ts 20; show src/b.ts 40"
        self.assertEqual(classify_shell(command)[0], "light")
        for altered in (
            command.replace("sed -n", "python -c"),
            command + "; npm test",
            command.replace("src/a.ts", "$(run-build)"),
            command.replace("src/a.ts", "-f"),
            command.replace("show", "sed"),
            command.replace("20;", "$LINE;"),
            definition + "; ".join("show src/a.ts 20" for _ in range(65)),
        ):
            self.assertNotEqual(classify_shell(altered)[0], "light")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "excerpt.txt"
            path.write_text("one\ntwo\nthree\nfour\nfive\nsix\n")
            result = subprocess.run(
                ["/bin/bash", "-c", definition + "show " + str(path) + " 3"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout, "=== " + str(path) + ":3\none\ntwo\nthree\nfour\nfive\n"
            )

    def test_escaped_delimiters_in_read_only_sed_substitution(self):
        command = r"rg -n pattern note.md | sed 's/(\.\.\/\.\.[^)]*)//' | head -80"
        self.assertEqual(classify_shell(command)[0], "light")
        for script in (
            r"s/old\/path/new\/path/g",
            r"s/old\/path//",
            r"s/old/new\/path/",
        ):
            self.assertEqual(classify_shell("sed '" + script + "' note.md")[0], "light")
        for script in (
            r"s/a\/b/x/e",
            r"s/a\/b/x/w out",
            r"s/a\/b/x/;e command",
            "s/a/b/\ne command",
            r"s/a/b\\/e",
        ):
            self.assertNotEqual(
                classify_shell("sed '" + script + "' note.md")[0], "light"
            )
        self.assertNotEqual(classify_shell("sed -i '' 's/a/b/' note.md")[0], "light")
        self.assertNotEqual(classify_shell("sed 's/a/b/' -f script.sed")[0], "light")
        result = subprocess.run(
            ["sed", r"s/(\.\.\/\.\.[^)]*)//"],
            input="label (../../src/file) end\n",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "label  end\n")

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

    def test_wait_usage_errors_never_reserve_memory(self):
        for command in [
            "memcap wait",
            "memcap wait --session --timeout 600",
            "memcap wait xyz --timeout nonsense",
            "memcap wait --help",
        ]:
            self.assertEqual(classify_shell(command)[0], "light", command)
        self.assertEqual(classify_shell("memcap wait --session; npm test")[0], "job")

    def test_github_and_json_status_inspection(self):
        for command in [
            "gh issue list -R org/repo --state open --limit 40",
            "gh pr checks 123",
            "gh pr checks 123 --watch --interval 60 --fail-fast 2>&1 | tail -5",
            "gh issue view 123 --json title,state",
            "F=/tmp/result; jq -r '.[0].text' $F | jq -r '(.issues // .)[] | [.key, .fields.status.name, (.fields.updated[:10]), (.fields.labels|join(\",\")), (.fields.parent.key // \"-\"), .fields.summary] | @tsv'",
            "jq 'keys' /tmp/result",
        ]:
            self.assertEqual(classify_shell(command)[0], "light", command)
        for command in [
            "gh issue view --web 1",
            "gh pr checkout 1",
            "git push; sleep 20; gh pr checks 123 --watch --interval 60",
            "gh alias set x '!build'",
            "jq -f /tmp/filter /tmp/result",
            "jq 'recurse' file",
            "jq '\"\\(range(100000000))\"' file",
            "jq 'range(100000000)' file",
            "jq 'while(true; .+1)' file",
        ]:
            self.assertEqual(classify_shell(command)[0], "job", command)

    def test_quoted_note_append_is_native_but_executing_heredoc_is_not(self):
        command = "cd /tmp; cat >> note.md <<'EOF'\nLiteral $(build), `build` and $HOME.\nEOF\n"
        self.assertEqual(classify_shell(command)[0], "light")
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                ["/bin/bash", "-c", command.replace("cd /tmp", "cd " + temp)],
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                (Path(temp) / "note.md").read_text(),
                "Literal $(build), `build` and $HOME.\n",
            )
        for bad in [
            command + "npm test",
            command.replace("<<'EOF'", "<<EOF"),
            command.replace("note.md", "$(build)"),
            command.replace("cd /tmp", "npm test"),
            command.replace("Literal", "EOF\nnpm test\nLiteral"),
            "python3 - <<'EOF'\nprint(1)\nEOF",
        ]:
            self.assertEqual(classify_shell(bad)[0], "job", bad)

    def test_literal_note_with_light_suffix_stays_native(self):
        command = "cat >> note.md <<'EOF'\nLiteral $(build) and `build`.\nEOF\necho ok"
        self.assertEqual(classify_shell(command)[0], "light")
        self.assertEqual(classify_shell(command + "; cat note.md")[0], "light")
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                ["/bin/bash", "-c", command], cwd=temp, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "ok\n")
            self.assertEqual(
                (Path(temp) / "note.md").read_text(), "Literal $(build) and `build`.\n"
            )
        for bad in [
            command + "; npm test",
            command.replace("echo ok", "echo $(build)"),
            command.replace("<<'EOF'", "<<EOF"),
            command.replace("Literal", "EOF\nnpm test\nLiteral"),
            command.replace("echo ok", "sh note.md"),
            command + "\ncat >> note.md <<'END'\nmore\nEND",
        ]:
            self.assertEqual(classify_shell(bad)[0], "job", bad)

    def test_filename_consumers_validate_expanded_arguments(self):
        from inspection import guarded_shell, inspect_argv

        for command in [
            "fd Package.resolved | head -2 | xargs -I{} rg -o '\"identity\"' {} ; echo done",
            'rg --files | head -2 | while read f; do rg -o \'"identity"\' "$f"; done; echo done',
        ]:
            guarded = guarded_shell(command, "/memcap")
            self.assertIsNotNone(guarded, command)
            self.assertIn("_inspect", guarded)
        for command in [
            "fd file | xargs -P8 -I{} build {}",
            "fd file | xargs -I{} sh -c '{}'",
            "rg --files | while read f; do npm test; done",
        ]:
            self.assertIsNone(guarded_shell(command, "/memcap"), command)
        with patch("inspection.os.execvpe") as execute:
            result = inspect_argv(
                ["rg", "-o", "identity", "--pre=evil"], lambda argv: 42
            )
            self.assertEqual(result, 42)
            execute.assert_not_called()

    def test_queue_transitions_do_not_repeat_host_probes(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(agent_diagnostics, "measure") as probe,
        ):
            for output in [
                "memcap: queued abcd1234: preserving host memory headroom.",
                "memcap: admitted abcd1234; command started.",
            ]:
                result = agent_diagnostics.guidance(
                    dict(
                        hook_event_name="PostToolUse",
                        tool_name="Bash",
                        tool_response=dict(stdout=output),
                    ),
                    Path(temp),
                )
                self.assertIn("MUST report", result)
                self.assertIn("native completion", result)
                self.assertLess(len(result), 1500)
            probe.assert_not_called()

    def test_literal_inspection_loops_and_remote_status(self):
        for command in [
            'memcap wait --session --timeout 60; for f in task1 task2; do rg -v "^memcap" /tmp/$f.output; done',
            'D=/tmp/tasks; for f in task1 task2; do echo "== $f"; rg -v "^memcap" $D/$f.output; done',
            "for p in 123 456; do gh pr view $p --json title; gh pr checks $p | head -20; done",
            "AWS_PROFILE=example aws cloudwatch describe-alarms --region us-east-1 --query 'MetricAlarms[].StateValue' --output table",
        ]:
            self.assertEqual(classify_shell(command)[0], "light", command)
        for command in [
            "for f in --pre=evil; do rg pattern $f; done",
            "for f in task1 task2; do npm test; done",
            "for f in $(build); do cat $f; done",
            "for f in task1; do echo done; done; npm test",
        ]:
            self.assertEqual(classify_shell(command)[0], "job", command)

    def test_unavailable_observation_retains_reduced_allowance(self):
        import copy

        gib = 1048576
        original = dict(
            memory_kb=gib,
            reservation_kb=gib // 2,
            elastic=True,
            orphaned=False,
            members={"42": "start", "43": "start"},
            reservation_window=[[100, 100000]],
            reservation_window_since=1,
        )
        for sample in [
            dict(busy=True, fault=True, footprints={}),
            dict(fault=True, footprints={}),
            dict(fault=False, footprints={"42": gib // 8}),
        ]:
            job = copy.deepcopy(original)
            self.assertEqual(
                Scheduler.reservation(job, sample, gib // 8, adaptive=True), gib // 2
            )
            if sample.get("busy"):
                self.assertEqual(
                    job["reservation_window"], original["reservation_window"]
                )
            else:
                self.assertNotIn("reservation_window", job)
        for overrides, adaptive in [
            (dict(elastic=False), True),
            (dict(orphaned=True), True),
            ({}, False),
        ]:
            self.assertEqual(
                Scheduler.reservation(
                    {**original, **overrides},
                    dict(busy=True, fault=True),
                    0,
                    adaptive=adaptive,
                ),
                gib,
            )
        self.assertEqual(
            Scheduler.reservation(
                dict(original), dict(fault=True), 3 * gib, adaptive=True
            ),
            3 * gib,
        )

    def test_contention_does_not_create_a_later_headroom_refusal(self):
        gib = 1048576
        scheduler = Scheduler.__new__(Scheduler)
        scheduler.policy = "adaptive"
        scheduler.max_jobs = 12
        scheduler.headroom_kb = 2 * gib
        scheduler.allowed_pressure = {1, 2}
        scheduler.controller = dict(now=100, healthy_since=1, last_start=1)
        job = dict(
            status="running",
            resource="",
            memory_kb=gib,
            reservation_kb=gib // 2,
            elastic=True,
            orphaned=False,
            members={"42": "start", "43": "start"},
        )
        busy = dict(busy=True, fault=True, footprints={})
        self.assertFalse(scheduler.admissible([job], gib, "", busy)[0])
        self.assertEqual(scheduler.last_decision["reason"], "sampling")
        # The host observation is valid/fresh but one changing group member is
        # absent. Retain the previous allowance; do not invent another 512 MiB.
        fresh = dict(
            fault=False,
            pressure=1,
            tracked_kb=8 * gib,
            cap_kb=20 * gib,
            available_kb=2 * gib,
            footprints={"42": gib // 8},
            tracked_pids=[42],
            monotonic=100,
        )
        self.assertTrue(scheduler.admissible([job], gib, "", fresh)[0])
        self.assertEqual(job["reservation_kb"], gib // 2)

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
