"""Finite shell helper regressions using local fixtures, never remote APIs."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libexec"))
from control_script import prepared, rewrite
from inspection import guarded_shell, inspect_argv

REMOTE = r"""#!/usr/bin/env bash
# A small SSM helper with only local streaming transforms.
set -euo pipefail
SQLFILE="$1"
PROFILE=example REGION=us-east-1
INSTANCE_ID=$(aws ec2 describe-instances --profile $PROFILE --region $REGION \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)
SQL=$(cat "$SQLFILE")
B64=$(printf '%s' "$SQL" | gzip -9 | base64 | tr -d '\n')
CMD="echo $B64 | base64 -d | gunzip | docker compose exec -T postgres psql"
PAYLOAD=$(printf '%s' "$CMD" | base64 | tr -d '\n')
ID=$(aws ssm send-command --profile $PROFILE --region $REGION --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript --parameters "commands=[\"echo $PAYLOAD | base64 -d | bash\"]" \
  --query Command.CommandId --output text)
aws ssm wait command-executed --profile $PROFILE --region $REGION --command-id "$ID" --instance-id "$INSTANCE_ID" >/dev/null 2>&1 || true
aws ssm get-command-invocation --profile $PROFILE --region $REGION --command-id "$ID" --instance-id "$INSTANCE_ID" \
  --query '[Status,StandardOutputContent,StandardErrorContent]' --output text
"""


class ControlScriptTests(unittest.TestCase):
    def test_reported_shape_is_proven_without_executing_it(self):
        guarded = rewrite(REMOTE, "/memcap", "session")
        self.assertIsNotNone(guarded)
        self.assertIn("_inspect --session-key session -- aws", guarded)
        self.assertIn('SQLFILE="$1"', guarded)
        self.assertIn("printf '%s' \"$SQL\"", guarded)
        self.assertNotIn(" run ", guarded)

    def test_unknown_execution_and_shell_state_remain_managed(self):
        for text in [
            "npm test",
            "CMD=npm; $CMD test",
            "cat $(npm test)",
            "source other.sh",
            "eval 'echo hi'",
            "PATH=/tmp; cat note",
            "export BASH_ENV=helper",
            "while true; do cat note; done",
            'F="$1"; printf "$F" hi',
            'cat "${HOME}/note"',
            "cat `npm test`",
            "cat <(npm test)",
            "A=$((1+2)); echo $A",
            "set -T; cat note",
            "trap 'npm test' EXIT; cat note",
            "A=1; A=2 npm test",
            "cat note & npm test",
            "exec npm test",
            'A="$(npm test)"; echo $A',
            "cat note; function x() { npm test; }",
        ]:
            self.assertIsNone(rewrite(text, "/memcap", ""), text)

    def test_native_text_snapshot_preserves_arguments_output_and_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "read.sh"
            script.write_text(
                'set -eu\nINPUT="$1"\nVALUE=$(cat "$INPUT" | gzip | base64)\nprintf "%s" "$VALUE" | base64 -d | gunzip\nfalse\n'
            )
            (root / "a note").write_text("hello\nworld\n")
            env = {
                **os.environ,
                "MC_DRY_RUN": "1",
                "MEMCAP_ROOT": str(ROOT),
                "MEMCAP_CONFIG_HOME": str(root / "config"),
                "MEMCAP_STATE_HOME": str(root / "state"),
            }
            command = f"cd {temp} && bash read.sh 'a note'"
            guarded = guarded_shell(command, str(ROOT / "bin/memcap"), "session")
            self.assertIsNotNone(guarded)
            for shell in ("/bin/bash", "/bin/zsh"):
                if not Path(shell).exists():
                    continue
                expected = subprocess.run(
                    [shell, "-c", command], env=env, capture_output=True, timeout=15
                )
                actual = subprocess.run(
                    [shell, "-c", guarded], env=env, capture_output=True, timeout=15
                )
                self.assertEqual(
                    (actual.returncode, actual.stdout, actual.stderr),
                    (expected.returncode, expected.stdout, expected.stderr),
                )
            self.assertFalse((root / "state/memcap/queue").exists())
            argv = prepared(["bash", str(script), "a note"], str(ROOT / "bin/memcap"))
            script.write_text("npm test")
            # Captured text is immutable; changing the original does not alter it.
            self.assertNotIn("npm test", argv[2])
            with patch("inspection.os.execvpe") as execute:
                self.assertEqual(inspect_argv(["bash", str(script)], lambda _: 75), 75)
                execute.assert_not_called()

    def test_expanded_execution_option_reaches_guard_not_child(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "read.sh"
            script.write_text('ARG="$1"\nrg "$ARG" note\n')
            guard = root / "guard"
            guard.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$RECORD"\nexit 75\n')
            guard.chmod(0o700)
            argv = prepared(["bash", str(script), "--pre=bad-helper"], str(guard))
            result = subprocess.run(
                argv,
                env={**os.environ, "RECORD": str(root / "record")},
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 75)
            self.assertIn("--pre=bad-helper", (root / "record").read_text())
            with patch("inspection.os.execvpe") as execute:
                self.assertEqual(
                    inspect_argv(["rg", "--pre=bad-helper", "note"], lambda _: 75), 75
                )
                execute.assert_not_called()

    def test_unavailable_large_fifo_and_startup_hooks_do_not_qualify(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "script"
            self.assertIsNone(prepared(["bash", str(path)], "/memcap"))
            path.write_text("#" * 32769)
            self.assertIsNone(prepared(["bash", str(path)], "/memcap"))
            path.unlink()
            os.mkfifo(path)
            self.assertIsNone(prepared(["bash", str(path)], "/memcap"))
            path.unlink()
            path.write_text("cat note")
            with patch.dict(os.environ, {"BASH_ENV": "somewhere"}):
                self.assertIsNone(prepared(["bash", str(path)], "/memcap"))


if __name__ == "__main__":
    unittest.main()
