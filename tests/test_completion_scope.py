"""Completion isolation and reported inspection shapes, with synthetic ownership."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from idle_gc import Collector
from scheduler_policy import hook_response, classify_shell
from session_identity import key


class CompletionScopeTests(unittest.TestCase):
    def payload(self, command):
        return dict(hook_event_name="PreToolUse", tool_name="Bash", permission_mode="bypassPermissions",
                    session_id="conversation", agent_id="child",
                    tool_input=dict(command=command))

    def test_explicit_runners_receive_current_scope(self):
        for executable in ["memcap", "/opt/memcap"]:
            for suffix in ["run -- python3 build.py", "run --memory 2 -- python3 build.py",
                           "run --shell-command 'npm test'", "run --session-key old -- npm test",
                           "run --session-key=old -- npm test", 'run --shell-command "npm test"',
                           "run  --resource dev -- npm run dev"]:
                with self.subTest(executable=executable, suffix=suffix):
                    payload = self.payload(executable + " " + suffix)
                    output = hook_response(payload, "/opt/memcap", "claude")
                    words = shlex.split(output.get("hookSpecificOutput", {}).get("updatedInput", {}).get("command", ""))
                    self.assertIn("--session-key", words)
                    self.assertEqual(words[words.index("--session-key") + 1], key(payload))
                    self.assertNotIn("old", words)
                    self.assertEqual(words[:2], [executable, "run"])
                    self.assertEqual(words.count("--session-key"), 1)

    def test_absolute_session_wait_receives_current_scope(self):
        for suffix in ["", " --session-key old", " --session-key=old"]:
            payload = self.payload("/opt/memcap wait --session --timeout 60" + suffix)
            output = hook_response(payload, "/opt/memcap", "claude")
            command = output.get("hookSpecificOutput", {}).get("updatedInput", {}).get("command", "")
            self.assertIn(key(payload), shlex.split(command))
            self.assertNotIn("old", shlex.split(command))

    def test_explicit_runner_preserves_child_scope_named_arguments(self):
        payload = self.payload("memcap run -- echo --session-key child-value")
        output = hook_response(payload, "/opt/memcap", "claude")
        command = output.get("hookSpecificOutput", {}).get("updatedInput", {}).get("command", "")
        self.assertEqual(shlex.split(command)[-4:], ["--", "echo", "--session-key", "child-value"])
        payload = self.payload("memcap run -- echo hi # a comment")
        output = hook_response(payload, "/opt/memcap", "claude")
        self.assertEqual(shlex.split(output["hookSpecificOutput"]["updatedInput"]["command"])[-3:], ["--", "echo", "hi"])

    def test_codex_rewrites_follow_native_permission_contract(self):
        for command in ["memcap run -- npm test", "memcap wait --session --timeout 60"]:
            payload = self.payload(command)
            result = hook_response(payload, "/opt/memcap", "codex")["hookSpecificOutput"]
            self.assertEqual(result["permissionDecision"], "allow")
            self.assertIn(key(payload), result["updatedInput"]["command"])
            payload["permission_mode"] = "default"
            result = hook_response(payload, "/opt/memcap", "codex")["hookSpecificOutput"]
            self.assertEqual(result["permissionDecision"], "deny")
            self.assertNotIn("updatedInput", result)

    def test_redirected_and_composed_waits_keep_conversation_scope(self):
        for command in [
            "memcap wait --session --timeout 60 >/dev/null 2>&1",
            "memcap queue; /opt/memcap wait --session --timeout 60 | tail -3",
            "memcap wait --session --session-key old >/dev/null; true",
            ">/dev/null memcap wait --timeout 60 --session",
            "memcap wait >/dev/null --session --timeout 60",
        ]:
            payload = self.payload(command)
            output = hook_response(payload, "/opt/memcap", "claude")
            rewritten = output["hookSpecificOutput"]["updatedInput"]["command"]
            self.assertIn(key(payload), rewritten)
            self.assertNotIn(" old", rewritten)
            self.assertNotIn(" run ", rewritten)

    def test_scoped_completion_never_claims_unkeyed_or_foreign_uid_work(self):
        def row(parent, command, uid=None):
            return dict(ppid=parent, command=command, start="start",
                        uid=os.getuid() if uid is None else uid)
        table = {"10": row(1, "codex app-server"),
                 "90": row(10, "python3 idle_gc.py"),
                 "30": row(10, "python3 scheduler.py"),
                 "31": row(10, "python3 scheduler.py"),
                 "32": row(10, "python3 scheduler.py", os.getuid() + 1)}
        scope = key(self.payload("npm test"))
        jobs = [dict(id=name, owner=pid, owner_start="start", status="running",
                     resource="", session_key=session)
                for name, pid, session in [("mine", 30, scope), ("legacy", 31, ""),
                                            ("foreign", 32, scope)]]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "queue").mkdir()
            registry = root / "queue/jobs.json"
            registry.write_text(json.dumps(dict(jobs=jobs)))
            collector = Collector(root)
            self.assertEqual([j["id"] for j in collector.pending_jobs("90", table, session_key=scope)], ["mine"])
            self.assertEqual(json.loads(registry.read_text())["jobs"], jobs)
            registry.write_text(json.dumps(dict(jobs=jobs[1:])))
            self.assertEqual(collector.pending_jobs("90", table, session_key=scope), [])

    def test_native_status_chain_and_json_projection(self):
        for command in [
            "cat CONTRIBUTING.md && ls -la .claude && git remote -v && git log -5 --oneline",
            "jq '{jobs: [.jobs[] | {id,status,session_key}]}' state.json",
            "jq --arg id abc '.jobs[] | select(.id == $id)' state.json",
            "jq -r '.jobs[] | [.id, .status] | @tsv' state.json",
            "gh release view --json tagName,url",
            "git ls-remote origin refs/heads/main refs/tags/v0.18.2",
            "git -C checkout ls-remote --heads --exit-code origin",
        ]:
            with self.subTest(command=command):
                self.assertEqual(classify_shell(command)[0], "light")

    def test_unknown_json_execution_and_remote_mutation_remain_managed(self):
        for command in ["jq '{jobs: [range(1000000000)]}' state.json",
                        "jq '[0, recurse, 0]' state.json",
                        "jq 'select(true, recurse, false)' state.json",
                        "jq '{jobs: [0, recurse, 0]}' state.json",
                        "jq --argfile code program.json . state.json",
                        "git remote add unsafe https://example.invalid/repo",
                        "git ls-remote --upload-pack=local-program origin",
                        "git ls-remote origin --upload-pack local-program",
                        "git ls-remote ext::local-program",
                        "git -c core.sshCommand=local-program ls-remote origin",
                        "gh release view --web",
                        "memcap run -- npm test; python3 build.py"]:
            self.assertEqual(classify_shell(command)[0], "job")

    def test_helper_is_checked_in_tool_workdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "remote.sh").write_text("aws ssm list-commands\n")
            payload = self.payload("bash remote.sh")
            payload["tool_input"]["workdir"] = tmp
            output = hook_response(payload, "/opt/memcap", "codex")
            command = output["hookSpecificOutput"]["updatedInput"]["command"]
            self.assertIn("_inspect", command)
            self.assertNotIn("--shell-command", command)

    def test_remote_fallback_environment_values_are_guarded(self):
        for command in [
            'curl -H "Authorization: ${API_KEY:-$FALLBACK_KEY}" https://example.invalid',
            'curl -H "Authorization: ${API_KEY:-}" https://example.invalid',
        ]:
            with self.subTest(command=command):
                output = hook_response(self.payload(command), "/opt/memcap", "claude")
                updated = output["hookSpecificOutput"]["updatedInput"]
                self.assertIn("_inspect", updated["command"])
                self.assertNotIn("run_in_background", updated)

    def test_remote_fallback_cannot_execute_local_code(self):
        for command in [
            'curl -H "${API_KEY:-$(npm test)}" https://example.invalid',
            'curl -H "${API_KEY:-`npm test`}" https://example.invalid',
            'curl -H "${API_KEY:=$(npm test)}" https://example.invalid',
        ]:
            output = hook_response(self.payload(command), "/opt/memcap", "claude")
            self.assertIn("--shell-command", output["hookSpecificOutput"]["updatedInput"]["command"])

    def test_guarded_remote_defaults_preserve_output_status_and_single_execution(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            curl = directory / "curl"
            curl.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nprintf x >> "$RECORD"\nexit 7\n')
            curl.chmod(0o700)
            env = {**os.environ, "PATH": tmp + os.pathsep + os.environ["PATH"],
                   "MEMCAP_CONFIG_HOME": tmp + "/config", "MEMCAP_STATE_HOME": tmp + "/state",
                   "MC_DRY_RUN": "1", "MEMCAP_ROOT": str(root), "RECORD": tmp + "/record",
                   "FALLBACK_KEY": "value with spaces"}
            env.pop("API_KEY", None)
            command = 'curl -H "Authorization: ${API_KEY:-$FALLBACK_KEY}" https://example.invalid'
            payload = self.payload(command)
            output = hook_response(payload, str(root / "bin/memcap"), "claude")
            guarded = output["hookSpecificOutput"]["updatedInput"]["command"]
            self.assertIn("_inspect", guarded)
            result = subprocess.run(["/bin/bash", "-c", guarded], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(result.stdout, "-H\nAuthorization: value with spaces\nhttps://example.invalid\n")
            self.assertEqual((directory / "record").read_text(), "x")
            self.assertFalse((directory / "state/memcap/queue/jobs.json").exists())


if __name__ == "__main__":
    unittest.main()
