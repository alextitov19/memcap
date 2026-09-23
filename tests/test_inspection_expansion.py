"""Reported searches expand filenames before deciding whether they need a lease."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from scheduler_policy import hook_response, classify_shell

ROOT = Path(__file__).resolve().parents[1]


class InspectionExpansionTests(unittest.TestCase):
    def test_reported_glob_search_gets_argument_guard_not_heavy_wrapper(self):
        command = (
            'cd /repo && rg -n "func Store" -A 60 *.go | rg -n "kept|status" | head -30'
        )
        result = hook_response(
            dict(
                hook_event_name="PreToolUse",
                tool_name="Bash",
                session_id="fixture",
                tool_input={"command": command},
            ),
            str(ROOT / "bin/memcap"),
            "claude",
        )
        updated = result["hookSpecificOutput"]["updatedInput"]
        self.assertIn("_inspect", updated["command"])
        self.assertNotIn(" run ", updated["command"])
        self.assertFalse(updated.get("run_in_background", False))

    def test_malicious_expansion_is_checked_after_globbing_before_execution(self):
        from inspection import guarded_shell

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "--pre=fixture.go").write_text("x")
            # Exercise the production argv validator after REAL shell expansion,
            # with a recording fallback rather than admitting any real workload.
            guard = root / "guard"
            guard.write_text(
                "#!"
                + sys.executable
                + "\n"
                + "import sys,json,os\nfrom pathlib import Path\n"
                + "sys.path.insert(0,"
                + repr(str(ROOT / "libexec"))
                + ")\n"
                + "from inspection import inspect_argv\n"
                + 'def fallback(argv):\n Path(os.environ["MARKER"]).write_text(json.dumps(argv));return 75\n'
                + 'raise SystemExit(inspect_argv(sys.argv[sys.argv.index("--")+1:],fallback))\n'
            )
            guard.chmod(0o755)
            (root / "bin").mkdir()
            tool = root / "bin/rg"
            tool.write_text('#!/bin/sh\nprintf ran > "$RAN"\n')
            tool.chmod(0o755)
            wrapped = guarded_shell("rg x *.go", str(guard), "session")
            self.assertIsNotNone(wrapped)
            result = subprocess.run(
                ["/bin/bash", "-c", wrapped],
                cwd=root,
                env={
                    **os.environ,
                    "PATH": str(root / "bin") + ":" + os.environ["PATH"],
                    "MARKER": str(root / "fallback"),
                    "RAN": str(root / "ran"),
                },
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 75, result.stderr)
            self.assertEqual(
                json.loads((root / "fallback").read_text()),
                ["rg", "x", "--pre=fixture.go"],
            )
            self.assertFalse((root / "ran").exists())

    def test_safe_expanded_arguments_run_without_registry_or_memory_sample(self):
        from inspection import guarded_shell

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.go").write_text("hello\n")
            wrapped = guarded_shell(
                "rg -n hello *.go", str(ROOT / "bin/memcap"), "session"
            )
            result = subprocess.run(
                ["/bin/bash", "-c", wrapped],
                cwd=root,
                env={
                    **os.environ,
                    "MEMCAP_ROOT": str(ROOT),
                    "MEMCAP_CONFIG_HOME": str(root / "config"),
                    "MEMCAP_STATE_HOME": str(root / "state"),
                    "MC_DRY_RUN": "1",
                },
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("1:hello", result.stdout)
            self.assertFalse((root / "state/memcap/queue").exists())

    def test_cached_wrapper_reclassifies_and_preserves_failed_search_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.go").write_text("hello\n")
            env = {
                **os.environ,
                "MEMCAP_ROOT": str(ROOT),
                "MEMCAP_CONFIG_HOME": str(root / "config"),
                "MEMCAP_STATE_HOME": str(root / "state"),
                "MC_DRY_RUN": "1",
            }
            for command, code in [
                ("rg hello *.go", 0),
                ("rg missing *.go", 1),
                ('f=$(rg -l hello .); sed -n 1,2p "$f"', 0),
            ]:
                result = subprocess.run(
                    [
                        str(ROOT / "bin/memcap"),
                        "run",
                        "--shell",
                        "/bin/bash",
                        "--cwd",
                        str(root),
                        "--session-key",
                        "fixture",
                        "--wait-forever",
                        "--shell-command",
                        command,
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertFalse((root / "state/memcap/queue").exists())
                if code == 0:
                    self.assertIn("hello", result.stdout)

    def test_path_lookup_then_file_read_checks_expanded_arguments(self):
        from inspection import guarded_shell

        command = 'f=$(rg -l "export function feature" src); echo $f; sed -n 1,200p $f | rg -n "" | sed -n 1,140p'
        result = guarded_shell(command, "memcap", "session")
        self.assertIsNotNone(result)
        self.assertIn("_inspect", result)
        for bad in [
            command.replace("rg -l", "rg --pre helper -l"),
            command.replace("echo $f", "$f"),
            command.replace("sed -n 1,200p $f", "npm test"),
        ]:
            self.assertIsNone(guarded_shell(bad, "memcap", "session"))

    def test_heavy_or_executing_stages_keep_normal_admission(self):
        from inspection import guarded_shell

        for command in [
            "rg x *.go; npm test",
            "rg --pre helper x *.go",
            "rg $(python tool.py) *.go",
            "*.go x",
            "rg x *.go > *.txt",
            "rg x {--pre,/tmp/tool}",
            "rg x *.go & npm test",
        ]:
            self.assertIsNone(guarded_shell(command, "memcap", "session"), command)

    def test_shared_guidance_rejects_docker_reclaim_arithmetic_and_paused_manual_cap(
        self,
    ):
        from report import MEMORY_GUIDANCE
        from agent_diagnostics import SESSION_GUIDANCE
        from integrate import GUIDANCE

        for text in [SESSION_GUIDANCE, GUIDANCE]:
            self.assertIn(MEMORY_GUIDANCE, text)
            self.assertIn("not a reservation", text)
            self.assertIn("never subtract container", text)
            self.assertIn("user-paused queue is not an admission refusal", text)

    def test_directory_status_loop_and_ps_diagnostics_are_light(self):
        commands = [
            'ls; for d in */; do [ -d "$d/.git" ] && echo "GIT: $d" && git -C "$d" status -sb | head -5 && git -C "$d" log --oneline -3; done',
            "ps -Ao pid,ppid,etime,rss,command | rg memcap | head -20",
        ]
        for command in commands:
            self.assertEqual(classify_shell(command)[0], "light", command)
        self.assertEqual(
            classify_shell(
                commands[0].replace('git -C "$d" log --oneline -3', "npm test")
            )[0],
            "job",
        )


if __name__ == "__main__":
    unittest.main()
