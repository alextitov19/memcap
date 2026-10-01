"""Reproductions from the remaining queue and completion reports."""
import sys
import unittest
import json
import os
import tempfile
import time
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from scheduler_policy import classify_shell, hook_response


class RemainingIssueTests(unittest.TestCase):
    def setUp(self):
        dry_run = patch.dict(os.environ, {"MC_DRY_RUN": "1"})
        dry_run.start()
        self.addCleanup(dry_run.stop)

    def test_old_runners_known_foreground_servers_do_not_block_completion(self):
        from idle_gc import Collector
        def row(parent, command):
            return dict(ppid=parent, command=command, uid=os.getuid(), start="identity")
        table = {"10": row(1, "codex app-server"), "20": row(10, "python3 idle_gc.py"),
                 "30": row(10, "python3 scheduler.py"),
                 "40": row(30, "python3 -m http.server 4183")}
        job = dict(id="fixture", owner=30, owner_start="identity", group=40,
                   members={"40": "identity"}, status="running", resource="", session_key="fixture")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/"queue").mkdir()
            registry = root/"queue/jobs.json"
            registry.write_text(json.dumps(dict(jobs=[job])))
            original = registry.read_bytes()
            collector = Collector(root)
            self.assertEqual(collector.pending_jobs("20", table, session_key="fixture"), [])
            self.assertEqual(registry.read_bytes(), original)
            for change in (
                {"start": "reused"}, {"uid": os.getuid()+1}, {"ppid": 1},
                {"command": "bash -c 'python3 -m http.server 4183; npm test'"},
                {"command": "python3 manage.py test"},
            ):
                changed = {**table, "40": {**table["40"], **change}}
                self.assertEqual([j["id"] for j in collector.pending_jobs("20", changed, session_key="fixture")], ["fixture"])
            job.update(members={}, footprint_members={"40": "identity"})
            registry.write_text(json.dumps(dict(jobs=[job])))
            self.assertEqual([j["id"] for j in collector.pending_jobs("20", table, session_key="fixture")], ["fixture"])

    def test_reported_streaming_inspection_families_stay_native(self):
        for command in (
            "lsof -nP -iTCP:4183 -sTCP:LISTEN | tail -2",
            "sed -n '1,20p' input | paste - - | cut -c1-260",
            "awk '/^class Example\\(/,/^class [A-Z][a-zA-Z]*\\(/' model.py | rg field",
            "rg -n field file.py | awk -F: '$2>=780 && $2<=960'",
            "awk '/^    Example:/{f=1;next} f&&/^    [A-Za-z]/{exit} f' schema.yml | head -45",
            '''jq -nc --arg value fixture '{value:$value,accepted:true}' ''',
            '''jq -r '(if type=="array" then . else .items end)[] | select(.slug=="fixture") | .id' ''',
        ):
            with self.subTest(command=command):
                self.assertEqual(classify_shell(command)[0], "light")

    def test_docker_settings_read_deadline_retains_unknown_and_permission_states(self):
        import threading
        from docker_read import read_mib

        release = threading.Event()
        started = time.monotonic()
        try:
            self.assertEqual(read_mib("unused", timeout=0.03, reader=lambda: (release.wait(10), '{}')[1]), (2, ""))
            self.assertLess(time.monotonic()-started, 1)
        finally:
            release.set()
        self.assertEqual(read_mib("unused", reader=lambda: '{"MemoryMiB":6144}'), (0, "6144"))
        for invalid in ('', '{}', '[]', '{"MemoryMiB":true}', '{"MemoryMiB":-1}', '{"MemoryMiB":1.5}'):
            self.assertEqual(read_mib("unused", reader=lambda: invalid), (1, ""))
        def denied():
            raise PermissionError("fixture")
        self.assertEqual(read_mib("unused", reader=denied), (2, ""))

    def test_invalid_report_usage_returns_without_admission_or_publication(self):
        root = Path(__file__).resolve().parents[1]
        for args in (["queue-stall", "--wait-seconds", "FIXTURE"], ["lightweight-queued", "--symptom"]):
            self.assertEqual(classify_shell("memcap report " + " ".join(args))[0], "light")
            with tempfile.TemporaryDirectory() as tmp:
                env = {**os.environ, "MEMCAP_ROOT": str(root), "MEMCAP_CONFIG_HOME": tmp+"/config",
                       "MEMCAP_STATE_HOME": tmp+"/state", "MC_DRY_RUN": "1"}
                result = subprocess.run([str(root/"bin/memcap"), "report", *args], env=env, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((Path(tmp)/"state/memcap/queue/jobs.json").exists())
                self.assertFalse((Path(tmp)/"state/memcap/reports").exists())

    def test_reported_filename_loops_guard_each_expanded_consumer(self):
        from inspection import guarded_shell
        commands = (
            'cd /tmp/repo; for f in tests/test_*.py; do echo "== $f"; rg -n "^class " $f; done',
            'cd /tmp/repo && for f in *.yaml; do echo "=== $f"; cat $f; done',
            'cd /tmp/repo && for f in .checks/*.yaml; do sed -n "1,14p" "$f" | rg -v "^$"; done 2>&1 | cut -c1-200',
            'cd /tmp/repo && for f in One Two Three; do rg -n label $f.tsx | head -30; done',
            'for n in 10 20 30; do echo "== $n"; sed -n "$((n-8)),$((n+6))p" input; done',
            'rg -l pattern src | while read f; do rg -q import "$f" || echo "missing: $f"; done',
        )
        for command in commands:
            with self.subTest(command=command):
                guarded = guarded_shell(command, "/opt/memcap", "fixture")
                self.assertIsNotNone(guarded)
                self.assertIn("_inspect", guarded)

    def test_remote_parameter_helper_preserves_function_arguments_and_output(self):
        from inspection import guarded_shell
        root = Path(__file__).resolve().parents[1]
        command = '''set -u
ssm() { aws ssm get-parameter --name "$1" --with-decryption --query Parameter.Value --output text; }
VALUE=$(ssm /fixture/parameter)
printf '%s\\n' "$VALUE"
'''
        guarded = guarded_shell(command, str(root/'bin/memcap'), 'fixture')
        self.assertIsNotNone(guarded)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fake = directory/'aws'
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*"\n')
            fake.chmod(0o700)
            env = {**os.environ, 'PATH': tmp+os.pathsep+os.environ['PATH'],
                   'MEMCAP_CONFIG_HOME': tmp+'/config', 'MEMCAP_STATE_HOME': tmp+'/state',
                   'MEMCAP_ROOT': str(root), 'MC_DRY_RUN': '1'}
            expected = subprocess.run(['/bin/bash', '-c', command], env=env, capture_output=True, text=True, timeout=10)
            actual = subprocess.run(['/bin/bash', '-c', guarded], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual((actual.returncode, actual.stdout, actual.stderr), (expected.returncode, expected.stdout, expected.stderr))
            self.assertFalse((directory/'state/memcap/queue/jobs.json').exists())
        for unsafe in (
            'ssm() { npm test; }; VALUE=$(ssm fixture)',
            'ssm() { ssm "$1"; }; VALUE=$(ssm fixture)',
            'curl() { npm test; }; VALUE=$(curl fixture)',
            'ssm() { aws ssm get-parameter --name "$(npm test)"; }; VALUE=$(ssm fixture)',
            'false && ssm() { aws ssm get-parameter --name "$1"; }; VALUE=$(ssm fixture)',
            'ssm() { aws ssm get-parameter --name "$1"; } | cat; VALUE=$(ssm fixture)',
        ):
            self.assertIsNone(guarded_shell(unsafe, '/opt/memcap', 'fixture'))

    def test_legacy_guards_remain_strict_but_memory_admission_requires_evidence(self):
        from inspection import guarded_shell
        for command in (
            "lsof -r 1",
            "awk '{system(\"npm test\")}' input",
            "awk -f program.awk input",
            "awk '/first/,/last/; system(\"npm test\")' input",
            '''jq -n 'if true then range(1000000000) else empty end' ''',
            '''jq -n --rawfile contents unbounded-input '{contents:$contents}' ''',
            'for f in *.py; do npm test; done',
            'for PATH in *.py; do rg x file; done',
            'for f in $(npm test); do cat "$f"; done',
            'for n in 10 20; do n=OTHER; sed -n "$((n+1))p" input; done',
            'for n in 10 20; do sed -n "$((n+OTHER))p" input; done',
            'rg -l x src | while read PATH; do rg x file; done',
        ):
            with self.subTest(command=command):
                heavy = {"awk '{system(\"npm test\")}' input", "awk '/first/,/last/; system(\"npm test\")' input",
                         'for f in *.py; do npm test; done', 'for f in $(npm test); do cat "$f"; done'}
                self.assertEqual(classify_shell(command)[0], 'job' if command in heavy else 'light')
                self.assertIsNone(guarded_shell(command, "/opt/memcap", "fixture"))

    def test_guarded_loops_preserve_native_output_exit_and_consumer_argv(self):
        from inspection import guarded_shell, inspect_argv
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "one.txt").write_text("match\nother\n")
            (directory / "two.txt").write_text("match\n")
            env = {**os.environ, "MEMCAP_CONFIG_HOME": tmp+"/config", "MEMCAP_STATE_HOME": tmp+"/state", "MC_DRY_RUN": "1", "MEMCAP_ROOT": str(root)}
            for command in (
                'for f in *.txt; do echo "== $f"; rg match "$f"; done',
                # rg's parallel traversal has no stable output order by default;
                # compare exact native/guarded output only with an explicit order.
                'rg -l --sort path match . --glob "*.txt" | while read f; do rg -q absent "$f" || echo "missing: $f"; done',
                'for n in 1 2; do sed -n "$((n+0))p" one.txt; done',
            ):
                guarded = guarded_shell(command, str(root/"bin/memcap"), "fixture")
                self.assertIsNotNone(guarded)
                native = subprocess.run(["/bin/bash", "-c", command], cwd=tmp, env=env, capture_output=True, text=True, timeout=10)
                actual = subprocess.run(["/bin/bash", "-c", guarded], cwd=tmp, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual((actual.returncode, actual.stdout, actual.stderr), (native.returncode, native.stdout, native.stderr))
            self.assertFalse((directory/"state/memcap/queue/jobs.json").exists())
        with patch("inspection.os.execvpe") as execute:
            fallback = []
            argv = ["rg", "match", "--pre=unexpected-helper"]
            self.assertEqual(inspect_argv(argv, lambda words: fallback.append(words) or 75), 75)
            execute.assert_not_called()
            self.assertEqual(fallback, [argv])

    def test_partial_measurement_retains_observed_peak_without_completing_learning(self):
        from scheduler import Scheduler, GIB
        with tempfile.TemporaryDirectory() as tmp:
            queue = Scheduler(Path(tmp) / "queue", sampler=lambda: {}, policy="adaptive")
            job = dict(status="running", members={"42": "one", "43": "two"},
                       memory_kb=GIB, reservation_kb=GIB, elastic=True,
                       started=time.time()-60, start_monotonic=0)
            sample = dict(monotonic=time.monotonic(), pressure=1, fault=False,
                          boot_id="fixture", footprints={"42": 12*GIB})
            queue.observe(dict(jobs=[job]), sample)
            self.assertTrue(job["learning_incomplete"])
            self.assertEqual(job.get("sample_count", 0), 0)
            self.assertEqual(job.get("observed_peak_kb", 0), 12*GIB)
            sample.update(monotonic=time.monotonic()+0.01, fault=True, footprints={"42": 18*GIB})
            queue.observe(dict(jobs=[job]), sample)
            self.assertEqual(job["observed_peak_kb"], 12*GIB)

    def test_single_or_partial_large_peak_can_raise_the_next_estimate(self):
        from scheduler import Scheduler, GIB
        for count, incomplete in ((1, False), (0, True)):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp) / "queue"
                sample = dict(cap_kb=20*GIB, tracked_kb=0, available_kb=20*GIB, pressure=1,
                              fault=False, footprints={}, tracked_pids=[])
                queue = Scheduler(directory, sampler=lambda: sample, policy="strict")
                table = {str(os.getpid()): dict(ppid=1, uid=os.getuid(), group=0, start="fixture")}

                def launched(argv, cwd, job, data):
                    job.update(status="running", started=time.time(), sample_count=count,
                               learning_incomplete=incomplete, observed_peak_kb=12*GIB,
                               estimate_key="fixture")
                    queue.save(data)
                    return SimpleNamespace(poll=lambda: 0)

                with patch("scheduler.processes", return_value=table), patch.object(queue, "launch", side_effect=launched):
                    self.assertEqual(queue.run(["fixture-job"], wait=1), 0)
                data = json.loads((directory / "jobs.json").read_text())
                learned = data.get("estimates", {}).get("fixture", {})
                self.assertEqual(learned.get("estimate_kb"), 15*GIB)
                self.assertEqual(learned.get("complete_runs", 0), 0)
                self.assertEqual(learned.get("peaks_kb", []), [])

    def test_completion_does_not_claim_learning_complete_without_observations(self):
        from scheduler import Scheduler, GIB

        for count, incomplete, expected in ((0, False, 0), (1, False, 0), (2, False, 1), (2, True, 0)):
            with self.subTest(count=count, incomplete=incomplete), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp) / "queue"
                sample = dict(cap_kb=20*GIB, tracked_kb=0, available_kb=20*GIB, pressure=1,
                              fault=False, footprints={}, tracked_pids=[])
                queue = Scheduler(directory, sampler=lambda: sample, policy="strict")
                table = {str(os.getpid()): dict(ppid=1, uid=os.getuid(), group=0, start="fixture")}

                def launched(argv, cwd, job, data):
                    job.update(status="running", started=time.time(), sample_count=count,
                               learning_incomplete=incomplete)
                    queue.save(data)
                    return SimpleNamespace(poll=lambda: 0)

                with patch("scheduler.processes", return_value=table), patch.object(queue, "launch", side_effect=launched):
                    self.assertEqual(queue.run(["fixture-job"], wait=1), 0)
                events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
                event = next(row for row in events if row["event"] == "completed")
                self.assertEqual(event["learning_complete"], expected)

    def test_persistence_does_not_make_a_small_server_memory_heavy(self):
        for command in (
            "python3 -m http.server 4183 --bind 127.0.0.1 --directory /tmp/site",
            "exec python3 -m http.server 4183 --bind 127.0.0.1 --directory /tmp/site > /tmp/server.log 2>&1",
            "cd /tmp/app && exec npm run dev -- --port 4183 > /tmp/server.log 2>&1",
            "python3 manage.py runserver 127.0.0.1:8022 --noreload",
            "cd /tmp/app && exec uv run python manage.py runserver 127.0.0.1:8022 --noreload > /tmp/server.log 2>&1",
        ):
            with self.subTest(command=command):
                expected = 'resource' if 'npm run dev' in command else 'light'
                self.assertEqual(classify_shell(command)[0], expected)
                output = hook_response({"hook_event_name": "PreToolUse", "session_id": "fixture", "tool_name": "Bash", "tool_input": {"command": command}}, "/opt/memcap", "claude")
                if expected == 'resource':
                    self.assertIn("--resource", output["hookSpecificOutput"]["updatedInput"]["command"])
                else:
                    self.assertEqual(output, {})

    def test_unknown_prefixes_and_finite_commands_keep_completion_tracking(self):
        for command in (
            "source /tmp/unknown.sh; exec python3 -m http.server 4183",
            "python3 manage.py test",
            "python3 -m http.server --help",
            "python3 -m http.server " + "9" * 5000,
            "python3 -c 'print(42)'",
            "exec -a python3 npm test",
            "python3 -m http.server 4183; npm test",
            "uv run --with unknown-package python manage.py runserver",
        ):
            with self.subTest(command=command):
                self.assertNotEqual(classify_shell(command)[0], "resource")


if __name__ == "__main__":
    unittest.main()
