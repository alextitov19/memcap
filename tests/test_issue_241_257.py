import os
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from scheduler_policy import classify_shell
from inspection import guarded_shell
from control_script import rewrite, prepared


class NewReports(unittest.TestCase):
    def test_bounded_json_projections_preserve_output_without_importing_project_code(
        self,
    ):
        import json
        from text_probe import eligible

        root = Path(__file__).resolve().parents[1]
        code = """import json
d=json.load(open('data.json'))
sc=d.get('scenarios')
want='A B'.split()
for s in sc:
  if s.get('id') in want: print(s['id'], '|', {k:v for k,v in s.items() if k not in ('id','results')}); print()
"""
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "data.json").write_text(
                json.dumps(
                    dict(
                        scenarios=[
                            dict(id="A", value=1, results=[]),
                            dict(id="C", value=2),
                        ]
                    )
                )
            )
            env = {
                **os.environ,
                "HOME": tmp,
                "MEMCAP_CONFIG_HOME": str(directory / "config"),
                "MEMCAP_STATE_HOME": str(directory / "state"),
                "MEMCAP_ROOT": str(root),
                "MC_DRY_RUN": "1",
            }
            import shlex

            command = shlex.join([sys.executable, "-c", code])
            # The policy deliberately accepts the documented python3 spelling.
            command = shlex.join(["python3", "-c", code])
            guarded = guarded_shell(command, str(root / "bin/memcap"), "fixture", tmp)
            self.assertIsNotNone(guarded)
            expected = subprocess.run(
                ["python3", "-I", "-c", code],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            (directory / "json.py").write_text(
                'raise RuntimeError("project import must not run")\n'
            )
            actual = subprocess.run(
                ["/bin/bash", "-c", guarded],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(
                (actual.returncode, actual.stdout, actual.stderr),
                (expected.returncode, expected.stdout, expected.stderr),
            )
            self.assertFalse((directory / "state/memcap/queue/jobs.json").exists())
            for tail in (
                'import subprocess; subprocess.run(["true"])',
                "d=json.dumps(d); d=json.dumps(d)",
                "for s in sc:\n print(d)",
                "print(json.dumps(json.dumps(d)))",
                "d=d+d",
                "print({k:d for k,v in d.items()})",
                "while True: print(d)",
            ):
                self.assertFalse(eligible(["python3", "-c", code + "\n" + tail]))
            (directory / "large.json").write_text(" " * 65537)
            self.assertFalse(
                eligible(
                    [
                        "python3",
                        "-c",
                        "import json; d=json.load(open("
                        + repr(str(directory / "large.json"))
                        + ")); print(d)",
                    ],
                    check_files=True,
                )
            )

    def test_catalog_inspection_is_bounded_and_disables_startup(self):
        from catalog_inspection import catalog_argv

        query = "select tablename||' | '||policyname||' | '||coalesce(qual,'') from pg_policies where tablename in ('fixture')"
        argv = [
            "psql",
            "-h",
            "localhost",
            "-U",
            "fixture",
            "-d",
            "fixture",
            "-Atc",
            query,
        ]
        self.assertEqual(catalog_argv(argv), ["psql", "-X", *argv[1:]])
        import shlex

        guarded = guarded_shell(shlex.join(argv), "/opt/memcap", "fixture")
        self.assertIn("-X", guarded)
        for sql in (
            "select pg_sleep(1000) from pg_tables",
            "select * from pg_tables; copy x from program 'go build'",
            "select * from pg_tables \\! npm test",
            "select * into dump from pg_tables",
            "select * from private_table",
            "select * from pg_tables union select * from private_table",
        ):
            self.assertIsNone(catalog_argv(["psql", "-c", sql]))

    def test_remote_helper_branches_preserve_results_and_single_execution(self):
        script = """#!/bin/bash
set -euo pipefail
SERVICE="${1:-}"
case "$SERVICE" in
  '' | api | worker) ;;
  *) echo "unknown service" >&2; exit 1 ;;
esac
if [[ -z "${INSTANCE_ID:-}" ]]; then
  INSTANCE_ID=$(aws ec2 describe-instances --output text)
fi
if [[ -z "$INSTANCE_ID" || "$INSTANCE_ID" == "None" || "$INSTANCE_ID" == *[[:space:]]* ]]; then
  echo "unknown instance" >&2; exit 1
fi
CMD="docker logs --tail=200"
[[ -n "$SERVICE" ]] && CMD="$CMD $SERVICE"
CMD_ID=$(aws ssm send-command --parameters "commands=$CMD" --output text)
set +e
aws ssm wait command-executed --command-id "$CMD_ID"
set -e
aws ssm get-command-invocation --command-id "$CMD_ID" --output text
"""
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source = directory / "logs.sh"
            source.write_text(script)
            source.chmod(0o700)
            fake = directory / "aws"
            fake.write_text(
                '#!/bin/bash\necho call >> "$CALLS"\ncase "$1 $2" in\n"ec2 describe-instances") echo i-fixture;;\n"ssm send-command") echo cmd-fixture;;\n"ssm get-command-invocation") echo result;;\nesac\n'
            )
            fake.chmod(0o700)
            env = {
                **os.environ,
                "PATH": str(directory) + os.pathsep + os.environ["PATH"],
                "HOME": tmp,
                "MEMCAP_CONFIG_HOME": str(directory / "config"),
                "MEMCAP_STATE_HOME": str(directory / "state"),
                "MEMCAP_ROOT": str(root),
                "MC_DRY_RUN": "1",
                "CALLS": str(directory / "calls"),
            }
            env.pop("INSTANCE_ID", None)
            for args, count in [
                (["api"], 4),
                (["worker"], 4),
                ([], 4),
                (["unknown"], 0),
            ]:
                with self.subTest(args=args), patch.dict(os.environ, env, clear=True):
                    argv = [str(source), *args]
                    guarded = prepared(
                        argv, str(root / "bin/memcap"), "fixture", directory
                    )
                    self.assertIsNotNone(guarded)
                    outputs = []
                    for invocation in (argv, guarded):
                        (directory / "calls").write_text("")
                        result = subprocess.run(
                            invocation,
                            env=env,
                            cwd=tmp,
                            capture_output=True,
                            text=True,
                            timeout=15,
                        )
                        outputs.append(
                            (result.returncode, result.stdout, result.stderr)
                        )
                        self.assertEqual(
                            len((directory / "calls").read_text().splitlines()), count
                        )
                    self.assertEqual(*outputs)
            self.assertFalse((directory / "state/memcap/queue/jobs.json").exists())

    def test_remote_branch_proof_rejects_execution_and_unknown_grammar(self):
        for script in (
            'if [[ -z "$(npm test)" ]]; then echo ok; fi',
            'if [[ -z "$VALUE" ]]; then npm test; fi',
            'case "$VALUE" in api) npm test;; *) echo ok;; esac',
            'case "$VALUE" in api) echo ok;; esac; npm test',
            'if [[ -n "$VALUE" ]]; then PATH=/tmp; fi; cat file',
            "echo __MEMCAP_BRANCH_0__; npm test",
            "while true; do aws sts get-caller-identity; done",
            'if [[ -z "$VALUE" ]]; then eval "$VALUE"; fi',
        ):
            with self.subTest(script=script):
                self.assertIsNone(rewrite(script, "/opt/memcap", "fixture"))

    def test_remote_and_inspection_forms(self):
        for command in (
            "benmore docs access",
            "benmore check fixture",
            "benmore tail fixture --lines 40 --since 5m",
            "benmore pull fixture /tmp/output",
            "memcap run --help",
            "memcap claim abcdef12 --pin",
            "git -C /tmp/project grep -c -i -E 'fixture' HEAD -- tests",
            "docker stats --no-stream --format 'table {{.Name}}\\t{{.MemUsage}}'",
            "sips -g pixelWidth -g pixelHeight assets/image.png",
            "cd /tmp; for f in one two; do echo $f; awk 'NR<=40 && /^#/' $f.yaml | head -22; done",
        ):
            with self.subTest(command=command):
                self.assertTrue(
                    classify_shell(command)[0] == "light"
                    or guarded_shell(command, "/opt/memcap", "fixture")
                )

    def test_execution_and_unbounded_work_remain_managed(self):
        for command in (
            "benmore tail fixture --follow",
            "benmore build fixture",
            "docker stats --format json",
            "git grep --open-files-in-pager=sh fixture",
            "git grep --textconv fixture",
            "sips -s format png huge.tiff",
            "awk 'NR<=40 && system(\"npm test\")' file",
            "memcap claim abcdef12; npm test",
        ):
            with self.subTest(command=command):
                self.assertNotEqual(classify_shell(command)[0], "light")
                self.assertIsNone(guarded_shell(command, "/opt/memcap", "fixture"))

    def test_grouped_reads_and_glob_loop_keep_output(self):
        root = Path(__file__).resolve().parents[1]
        commands = (
            "(cat one.txt; cat two.txt) | sort -u",
            """for f in ./*.txt; do echo "$f: $(head -3 $f | tr '\\n' ' ' | cut -c1-170)"; done""",
            "wc -l ./{one,t*}.txt",
            "echo status=$?; cat one.txt",
        )
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "one.txt").write_text("one\ntwo\n")
            (directory / "two.txt").write_text("two\nthree\n")
            env = {
                **os.environ,
                "HOME": tmp,
                "MEMCAP_CONFIG_HOME": str(directory / "config"),
                "MEMCAP_STATE_HOME": str(directory / "state"),
                "MEMCAP_ROOT": str(root),
                "MC_DRY_RUN": "1",
            }
            for command in commands:
                with self.subTest(command=command):
                    guarded = guarded_shell(
                        command, str(root / "bin/memcap"), "fixture", tmp
                    )
                    self.assertIsNotNone(guarded)
                    outcomes = []
                    for text in (command, guarded):
                        p = subprocess.run(
                            ["/bin/bash", "-c", text],
                            env=env,
                            cwd=tmp,
                            capture_output=True,
                            text=True,
                            timeout=15,
                        )
                        outcomes.append((p.returncode, p.stdout, p.stderr))
                    self.assertEqual(*outcomes)
            self.assertFalse((directory / "state/memcap/queue/jobs.json").exists())
        for command in (
            "(npm test; cat file) | head",
            "(cat file) | sh",
            "echo $(npm test)",
            "(git grep --textconv fixture) | head",
        ):
            self.assertIsNone(guarded_shell(command, "/opt/memcap", "fixture"))


if __name__ == "__main__":
    unittest.main()
