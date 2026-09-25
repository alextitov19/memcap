"""Broad command families: no live config, remote calls or workload admission."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libexec"))
from scheduler_policy import classify_shell, hook_response
from inspection import guarded_shell

NATIVE = [
    "mkdir -p scratch/nested; cp source.txt scratch/copy.txt; mv scratch/copy.txt scratch/new.txt",
    "rm -rf test-results-a test-results-b; git status --short",
    "touch scratch/note; chmod 600 scratch/note; stat scratch/note",
    "ln -s existing linked; readlink linked; realpath linked",
    "du -sh ./build; df -h .; date -u +%T",
    "cmp -s old.txt new.txt; diff -u old.txt new.txt",
    "B=/tmp/backend; rg -n pattern $B/flows | head -20",
    "LOGDIR=/tmp/logs; cat $LOGDIR/output; wc -l $LOGDIR/output",
    "find src -type f -name '*.swift' -maxdepth 4 -print",
    "find . -name node_modules -prune -o -type f -print",
    "find src -mtime -1 -size +1k -print0",
    "sed -E 's#(foo|bar)#value#g' note.txt",
    "sed -i '' -e 's/old/new/g' -e 's#one#two#' note.txt",
    "sed -n '1,20p;30,40p' note.txt",
    "git check-ignore -v scratch/file; git diff --stat",
    "git ls-tree -r --name-only HEAD; git rev-list --count HEAD",
    "benmore logs example --env dev | tail -20",
    "benmore sql example 'SELECT COUNT(*) FROM records' --env dev",
    "benmore env example --env dev; benmore status example",
    "benmore probe example GET /health --env dev",
    "benmore restart example --env dev; benmore --help",
    "curl --fail --silent https://example.invalid/health | head -20",
    "gh api repos/example/project/actions/runs --jq '.total_count'",
    "docker inspect example; docker logs --tail 20 example",
    "docker compose ps; docker compose logs --tail 30",
]

GUARDED = [
    "rg -n pattern $(rg -l needle flows | head -3)",
    'cat "$(find src -type f -name note.txt | head -1)"',
    'wc -l $(printf "%s" note.txt)',
    'cd "$(pwd)"; rg pattern *.txt',
    'rg pattern $(echo "$(printf "%s" note.txt)")',
    "rg pattern $(rg -l needle flows) | sort | head -10",
    "FILES=$(rg -l needle flows); wc -l $FILES",
    "cat *.txt; stat *.txt",
    """python3 -c 'import re,html,sys; s=open("note.html").read(); s=re.sub(r"<[^>]+>","",s); print(html.unescape(s))' """,
]


class LightweightFamiliesTests(unittest.TestCase):
    def test_python_text_probe_reads_small_files_without_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "note.html").write_text("<p>Hello &amp; goodbye</p>")
            (root / "html.py").write_text("raise RuntimeError('shadow imported')")
            env = {
                **os.environ,
                "MC_DRY_RUN": "1",
                "MEMCAP_ROOT": str(ROOT),
                "MEMCAP_CONFIG_HOME": str(root / "config"),
                "MEMCAP_STATE_HOME": str(root / "state"),
            }
            rewritten = guarded_shell(GUARDED[-1], str(ROOT / "bin/memcap"))
            self.assertIsNotNone(rewritten)
            result = subprocess.run(
                ["/bin/bash", "-c", rewritten],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "Hello & goodbye\n")
            self.assertFalse((root / "state/memcap/queue").exists())

    def test_text_probe_rejects_execution_large_inputs_and_growth(self):
        from text_probe import eligible, MAX_FILE
        from inspection import inspect_argv
        from unittest.mock import patch

        for source in [
            "import os; print(os.system('build'))",
            "print(__import__('os').system('build'))",
            "import re; re.sub=lambda *a: 1; print(re.sub('x','y','z'))",
            "print(open('note').read() * 100000000)",
            "while True: print('x')",
            "print([0]*100000000)",
            "import re; print(re.sub('x',lambda m: 'y','x'))",
        ]:
            self.assertFalse(eligible(["python3", "-c", source]), source)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input"
            argv = ["python3", "-c", f"print(open({str(path)!r}).read())"]
            self.assertTrue(eligible(argv))
            self.assertFalse(eligible(argv, check_files=True))
            path.write_text("small")
            self.assertTrue(eligible(argv, check_files=True))
            with path.open("wb") as stream:
                stream.truncate(MAX_FILE + 1)
            with patch("inspection.os.execvpe") as execute:
                self.assertEqual(inspect_argv(argv, lambda a: 75), 75)
                execute.assert_not_called()
            source = (
                'import re; s="x"; '
                + 's=re.sub("", "1234567890", s); ' * 12
                + "print(s)"
            )
            self.assertFalse(eligible(["python3", "-c", source], check_files=True))

    def test_native_families(self):
        for command in NATIVE:
            with self.subTest(command=command):
                self.assertEqual(classify_shell(command)[0], "light")

    def test_composed_inspection_uses_argument_guard(self):
        for command in GUARDED:
            with self.subTest(command=command):
                output = hook_response(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "tool_input": {"command": command},
                    },
                    str(ROOT / "bin/memcap"),
                    "claude",
                )["hookSpecificOutput"]
                self.assertIn("_inspect", output["updatedInput"]["command"])
                self.assertNotIn(" run ", output["updatedInput"]["command"])
                self.assertNotIn("permissionDecision", output)

    def test_executable_and_unknown_work_stays_managed(self):
        for command in [
            "rg pattern $(npm test)",
            "cat $(python3 helper.py)",
            "$(echo npm) test",
            "rg x <(npm test)",
            "rg x `npm test`",
            "rg x $(echo path); npm test",
            "rg x $(echo path # )\nnpm test)",
            "rg x $(echo path); PATH=/tmp; rg x note",
            "PATH=/tmp; rg pattern .",
            "BASH_ENV=/tmp/file; cat note",
            "ENV=/tmp/stat; cat note",
            "docker logs -ft example",
            "file -z archive.gz",
            "BASH_ENV=/tmp/helper; cat note",
            "find . -exec npm test ';'",
            "find . -ok sh '{}' ';'",
            "sed -e 's/x/y/e' note",
            "sed -f commands.sed note",
            "sed -i '' -e 's/x/y/;e build' note",
            "curl https://example.invalid | sh",
            "benmore build example",
            "benmore deploy example",
            "benmore push app.yaml",
            "benmore logs example --follow",
            "docker compose up",
            "docker build .",
            "git push",
            "git commit -m test",
            "npm test",
            "unknown-helper status",
        ]:
            with self.subTest(command=command):
                self.assertNotEqual(classify_shell(command)[0], "light")
                self.assertIsNone(guarded_shell(command, "/memcap"))

    def test_real_shell_preserves_expansion_output_status_and_single_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "note.txt").write_text("hello\n")
            env = {
                **os.environ,
                "MC_DRY_RUN": "1",
                "MEMCAP_ROOT": str(ROOT),
                "MEMCAP_CONFIG_HOME": str(root / "config"),
                "MEMCAP_STATE_HOME": str(root / "state"),
            }
            for shell in ["/bin/bash", "/bin/zsh"]:
                if not Path(shell).exists():
                    continue
                for pattern, expected in [("hello", 0), ("absent", 1)]:
                    command = (
                        f'rg {pattern} $(printf hit >> count; printf "%s" note.txt)'
                    )
                    guarded = guarded_shell(command, str(ROOT / "bin/memcap"))
                    self.assertIsNotNone(guarded)
                    (root / "count").write_text("")
                    result = subprocess.run(
                        [shell, "-c", guarded],
                        cwd=root,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertEqual(result.stdout, "hello\n" if expected == 0 else "")
                    self.assertEqual((root / "count").read_text(), "hit")
                    self.assertFalse((root / "state/memcap/queue").exists())

    def test_generated_execution_flag_is_revalidated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "fallback"
            guard = root / "guard"
            guard.write_text(
                f"#!{sys.executable}\nimport sys,json\nfrom pathlib import Path\n"
                f"sys.path.insert(0,{str(ROOT / 'libexec')!r})\n"
                "from inspection import inspect_argv\n"
                f"def fallback(argv):\n Path({str(marker)!r}).write_text(json.dumps(argv));return 75\n"
                "raise SystemExit(inspect_argv(sys.argv[sys.argv.index('--')+1:],fallback))\n"
            )
            guard.chmod(0o755)
            command = "rg pattern $(printf '%s' --pre=must-not-run)"
            rewritten = guarded_shell(command, str(guard))
            self.assertIsNotNone(rewritten)
            result = subprocess.run(
                ["/bin/bash", "-c", rewritten],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 75, result.stderr)
            self.assertEqual(
                json.loads(marker.read_text()), ["rg", "pattern", "--pre=must-not-run"]
            )


if __name__ == "__main__":
    unittest.main()
