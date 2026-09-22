"""Integration tests own every profile/config/state file; no real agent writes."""

import json
import os
from pathlib import Path
import shutil
import socket
import sys
import threading
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libexec"))


class IntegrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memcap profiles ")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.profile = self.home / ".claude"
        self.profile.mkdir()
        self.config = self.profile / "settings.json"
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "MEMCAP_CONFIG_HOME": str(self.home / "config"),
            "MEMCAP_STATE_HOME": str(self.home / "state"),
            "MC_DRY_RUN": "1",
            "MC_NO_NOTIFIER": "1",
            "MEMCAP_ROOT": str(ROOT),
        }
        for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
            self.env.pop(key, None)

    def cli(self, action="integrate", *args, input=None, env=None, binary=None):
        return subprocess.run(
            [str(binary or ROOT / "bin/memcap"), action, *args],
            input=input,
            text=True,
            capture_output=True,
            env=env or self.env,
            timeout=30,
        )

    def install(self, *args):
        result = self.cli("integrate", "--claude-dir", str(self.profile), *args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_install_preserves_user_settings_and_unrelated_mixed_hooks(self):
        original = {
            "permissions": {"deny": ["Bash(rm *)"]},
            "model": "keep-me",
            "hooks": {
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo memcap feedback",
                                "timeout": 7,
                            },
                            {
                                "type": "command",
                                "command": "/old/bin/memcap feedback",
                                "timeout": 5,
                            },
                        ],
                    }
                ]
            },
        }
        self.config.write_text(json.dumps(original))
        self.install()
        changed = json.loads(self.config.read_text())
        self.assertEqual(changed["permissions"], original["permissions"])
        self.assertEqual(changed["model"], "keep-me")
        handlers = [h for g in changed["hooks"]["Stop"] for h in g["hooks"]]
        self.assertIn(original["hooks"]["Stop"][0]["hooks"][0], handlers)
        self.assertEqual(len(handlers), 2)
        self.assertTrue(handlers[-1]["command"].endswith(" feedback --wait"))
        self.assertEqual(handlers[-1]["timeout"], 75)
        self.assertIn("SessionStart", changed["hooks"])
        self.assertIn("queue-hook claude", json.dumps(changed))
        backups = list(self.profile.glob(".settings.json.memcap-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), original)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_repeat_install_changes_nothing_and_makes_no_extra_backups(self):
        self.config.write_text('{"hooks":{}}\n')
        (self.profile / "CLAUDE.md").write_text("# My rules\nKeep this exactly.\n")
        self.install()
        before = {
            str(p): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in self.profile.rglob("*")
            if p.is_file()
        }
        self.install()
        after = {
            str(p): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in self.profile.rglob("*")
            if p.is_file()
        }
        self.assertEqual(after, before)
        self.assertTrue(
            (self.profile / "CLAUDE.md")
            .read_text()
            .startswith("# My rules\nKeep this exactly.\n")
        )

    def test_duplicate_old_entries_are_replaced_without_deleting_custom_commands(self):
        self.config.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "/old/memcap queue-hook claude",
                                    }
                                ],
                            },
                            {
                                "matcher": "Bash",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "/older/memcap queue-hook claude",
                                    }
                                ],
                            },
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "echo 'memcap queue-hook claude'",
                                    }
                                ]
                            },
                        ]
                    }
                }
            )
        )
        self.install()
        data = json.loads(self.config.read_text())
        commands = [
            h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"]
        ]
        self.assertEqual(sum(c.endswith(" queue-hook claude") for c in commands), 1)
        self.assertIn("echo 'memcap queue-hook claude'", commands)

    def test_bad_second_profile_prevents_all_config_and_markdown_writes(self):
        self.config.write_text('{"model":"keep"}')
        other = self.home / ".claude-work"
        other.mkdir()
        (other / "settings.json").write_text("{broken")
        before = self.config.read_bytes()
        result = self.cli(
            "integrate", "--claude-dir", str(self.profile), "--claude-dir", str(other)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse((self.profile / "CLAUDE.md").exists())
        self.assertEqual(list(self.profile.glob("*.memcap-backup-*")), [])

    def test_ambiguous_json_and_hook_shapes_are_rejected_without_changes(self):
        for content in (
            '{"hooks":{},"hooks":{}}',
            '{"hooks":[]}',
            '{"hooks":{"Stop":{}}}',
            '{"hooks":{"Stop":[{"hooks":null}]}}',
        ):
            self.config.write_text(content)
            result = self.cli("integrate", "--claude-dir", str(self.profile))
            self.assertNotEqual(result.returncode, 0, content)
            self.assertEqual(self.config.read_text(), content)

    def test_symlinked_profile_and_shared_markdown_keep_their_links(self):
        shared = self.home / "shared.md"
        shared.write_text("shared rules\n")
        (self.profile / "CLAUDE.md").symlink_to(shared)
        personal = self.home / ".claude-personal"
        personal.symlink_to(self.profile, target_is_directory=True)
        self.cli("integrate", "--claude-dir", str(personal))
        self.assertTrue(personal.is_symlink())
        self.assertTrue((self.profile / "CLAUDE.md").is_symlink())
        self.assertTrue(shared.read_text().startswith("shared rules\n"))
        self.assertEqual(shared.read_text().count("<!-- memcap:begin -->"), 1)

    def test_malformed_managed_block_and_dangling_symlinks_fail_closed(self):
        doc = self.profile / "CLAUDE.md"
        doc.write_text("my rules\n<!-- memcap:begin -->\nunfinished")
        result = self.cli("integrate", "--claude-dir", str(self.profile))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.config.exists())
        doc.unlink()
        doc.symlink_to(self.home / "missing.md")
        result = self.cli("integrate", "--claude-dir", str(self.profile))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(doc.is_symlink())
        self.assertFalse((self.home / "missing.md").exists())

    def test_doctor_detects_missing_stale_and_disabled_hooks_without_writing(self):
        result = self.cli("doctor", "--claude-dir", str(self.profile))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.config.exists())
        self.install()
        result = self.cli("doctor", "--claude-dir", str(self.profile))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(self.config.read_text())
        data["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 5
        self.config.write_text(json.dumps(data))
        before = self.config.read_bytes()
        result = self.cli("doctor", "--claude-dir", str(self.profile))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_bytes(), before)
        self.install()
        data = json.loads(self.config.read_text())
        data["disableAllHooks"] = True
        self.config.write_text(json.dumps(data))
        self.install()
        self.assertTrue(json.loads(self.config.read_text())["disableAllHooks"])
        result = self.cli("doctor", "--claude-dir", str(self.profile))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disableAllHooks", result.stdout)

    def test_custom_profile_environment_is_discovered(self):
        extra = self.home / "custom Claude"
        extra.mkdir()
        personal = self.home / ".claude-personal"
        personal.mkdir()
        (personal / "settings.json").write_text("{}")
        env = {**self.env, "CLAUDE_CONFIG_DIR": str(extra)}
        result = self.cli("integrate", "--claude", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        for profile in (self.profile, extra, personal):
            self.assertTrue((profile / "CLAUDE.md").exists(), profile)
        self.assertFalse((self.home / ".codex/hooks.json").exists())

    def test_codex_integration_preserves_toml_and_reports_unverified_trust(self):
        profile = self.home / ".codex"
        profile.mkdir()
        config = profile / "config.toml"
        config.write_text('model = "preserve-me"\n')
        result = self.cli("integrate", "--codex-dir", str(profile))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.read_text(), 'model = "preserve-me"\n')
        self.assertIn("/hooks", result.stdout)
        self.assertTrue((profile / "AGENTS.md").exists())
        result = self.cli("doctor", "--codex-dir", str(profile), "--no-runtime")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unverified", result.stdout.lower())

    def test_init_requires_explicit_opt_in_and_then_installs(self):
        result = self.cli("init", "--no-service", "--no-docker", input="16\nyes\nno\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.config.exists())
        result = self.cli("init", "--no-service", "--no-docker", input="16\nyes\nyes\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.config.exists())

    def test_homebrew_commands_use_stable_opt_path_across_upgrade(self):
        prefix = self.home / "brew"
        version = prefix / "Cellar/memcap/0.12.0"
        shutil.copytree(ROOT / "bin", version / "bin")
        shutil.copytree(ROOT / "libexec", version / "libexec")
        opt = prefix / "opt/memcap"
        opt.parent.mkdir(parents=True)
        opt.symlink_to(version)
        env = {**self.env}
        env.pop("MEMCAP_ROOT")
        result = self.cli(
            "integrate",
            "--claude-dir",
            str(self.profile),
            env=env,
            binary=version / "bin/memcap",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = [
            h["command"]
            for groups in json.loads(self.config.read_text())["hooks"].values()
            for group in groups
            for h in group["hooks"]
        ]
        self.assertTrue(all("/opt/memcap/bin/memcap" in c for c in commands))
        self.assertTrue(all("/Cellar/" not in c for c in commands))
        new = prefix / "Cellar/memcap/0.12.1"
        shutil.copytree(version, new)
        opt.unlink()
        opt.symlink_to(new)
        shutil.rmtree(version)
        command = json.loads(self.config.read_text())["hooks"]["SessionStart"][0][
            "hooks"
        ][0]["command"]
        result = subprocess.run(
            ["/bin/bash", "-c", command],
            input=json.dumps(
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "test",
                    "cwd": str(self.home),
                }
            ),
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("memcap manages shared memory", result.stdout)

    def test_partial_write_failure_restores_existing_files(self):
        sys.path.insert(0, str(ROOT / "libexec"))
        import integrate

        self.config.write_text('{"model":"preserve"}')
        doc = self.profile / "CLAUDE.md"
        doc.write_text("original instructions")
        with patch.dict(os.environ, self.env, clear=True):
            installer = integrate.Installer(ROOT / "bin/memcap", "test")
            edits = installer.prepare([("claude", self.profile)])
            write = integrate.atomic_write
            failed = []

            def disk_error(path, content, mode):
                if path == doc and not failed:
                    failed.append(True)
                    raise OSError("simulated full disk")
                return write(path, content, mode)

            with patch.object(integrate, "atomic_write", side_effect=disk_error):
                with self.assertRaises(integrate.IntegrationError):
                    integrate.commit(edits)
        self.assertEqual(self.config.read_text(), '{"model":"preserve"}')
        self.assertEqual(doc.read_text(), "original instructions")
        self.assertFalse((self.profile / ".memcap-integration.json").exists())

    def test_concurrent_edit_and_foreign_ownership_are_not_overwritten(self):
        import integrate

        self.config.write_text("{}")
        edit = integrate.Edit.read(self.config)
        edit.after = b'{"changed":true}'
        self.config.write_text('{"user":"new change"}')
        with self.assertRaises(integrate.IntegrationError):
            integrate.commit([edit])
        self.assertEqual(self.config.read_text(), '{"user":"new change"}')
        actual = self.config.stat()
        foreign = os.stat_result(
            (
                actual.st_mode,
                actual.st_ino,
                actual.st_dev,
                actual.st_nlink,
                os.getuid() + 1,
                actual.st_gid,
                actual.st_size,
                actual.st_atime,
                actual.st_mtime,
                actual.st_ctime,
            )
        )
        with patch.object(Path, "stat", return_value=foreign):
            with self.assertRaises(integrate.IntegrationError):
                integrate.Edit.read(self.config)

    def test_stale_version_metadata_is_reported_and_refreshed(self):
        self.install()
        metadata = self.profile / ".memcap-integration.json"
        value = json.loads(metadata.read_text())
        value["memcap_version"] = "0.0.1"
        metadata.write_text(json.dumps(value))
        result = self.cli("doctor", "--claude-dir", str(self.profile))
        self.assertNotEqual(result.returncode, 0)
        self.install()
        self.assertEqual(
            self.cli("doctor", "--claude-dir", str(self.profile)).returncode, 0
        )

    def test_doctor_requires_matching_enabled_trusted_runtime_hooks(self):
        import integrate
        from contextlib import redirect_stdout
        import io

        profile = self.home / ".codex"
        with (
            patch.dict(os.environ, self.env, clear=True),
            redirect_stdout(io.StringIO()),
        ):
            installer = integrate.Installer(ROOT / "bin/memcap", "test")
            installer.install([("codex", profile)])
            events = {
                "PreToolUse": "preToolUse",
                "PostToolUse": "postToolUse",
                "SessionStart": "sessionStart",
                "UserPromptSubmit": "userPromptSubmit",
                "Stop": "stop",
                "SessionEnd": "sessionEnd",
                "SubagentStart": "subagentStart",
                "SubagentStop": "subagentStop",
            }
            config = json.loads((profile / "hooks.json").read_text())
            loaded = [
                dict(
                    key=f"fixture:{event}:{i}",
                    currentHash="sha256:fixture",
                    eventName=events[event],
                    handlerType="command",
                    command=h["command"],
                    sourcePath=str(profile / "hooks.json"),
                    source="user",
                    isManaged=False,
                    enabled=True,
                    trustStatus="trusted",
                    timeoutSec=3 if event == "SessionEnd" else h["timeout"],
                    matcher=g.get("matcher"),
                )
                for event, groups in config["hooks"].items()
                for i, g in enumerate(groups)
                for h in g["hooks"]
            ]
            self.assertEqual(len(loaded), 9)
            with patch("integration_probe.codex_hooks", return_value=loaded):
                self.assertEqual(installer.doctor([("codex", profile)]), 0)
            for changes in (
                {"enabled": False},
                {"trustStatus": "untrusted"},
                {"timeoutSec": 1},
                {"command": "/old/memcap feedback"},
            ):
                altered = [{**loaded[0], **changes}, *loaded[1:]]
                with patch("integration_probe.codex_hooks", return_value=altered):
                    self.assertEqual(installer.doctor([("codex", profile)]), 1, changes)
            with patch("integration_probe.codex_hooks", return_value=[]):
                self.assertEqual(installer.doctor([("codex", profile)]), 1)

    def test_codex_probe_reads_existing_socket_without_starting_or_trusting_anything(
        self,
    ):
        import integration_probe
        import base64
        import hashlib
        import struct

        with tempfile.TemporaryDirectory(prefix="mc-socket-", dir="/tmp") as raw:
            directory = Path(raw)
            path = directory / "app-server-control/app-server-control.sock"
            path.parent.mkdir()
            failures = []
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(path))
                server.listen(1)
                server.settimeout(3)
                expected = {
                    "key": "fixture",
                    "currentHash": "hash",
                    "enabled": True,
                    "trustStatus": "trusted",
                    "command": "memcap feedback",
                }

                def serve():
                    try:
                        connection, _ = server.accept()
                        with connection, connection.makefile("rb") as stream:
                            lines = []
                            while True:
                                line = stream.readline()
                                if line == b"\r\n":
                                    break
                                lines.append(line.decode())
                            key = next(
                                line.split(": ", 1)[1].strip()
                                for line in lines
                                if line.startswith("Sec-WebSocket-Key:")
                            )
                            accept = base64.b64encode(
                                hashlib.sha1(
                                    (
                                        key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                                    ).encode()
                                ).digest()
                            ).decode()
                            connection.sendall(
                                (
                                    "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: "
                                    + accept
                                    + "\r\n\r\n"
                                ).encode()
                            )

                            def receive():
                                first, size = stream.read(2)
                                self.assertEqual(first, 129)
                                self.assertTrue(size & 128)
                                size &= 127
                                if size == 126:
                                    size = struct.unpack("!H", stream.read(2))[0]
                                mask = stream.read(4)
                                payload = stream.read(size)
                                return json.loads(
                                    bytes(
                                        b ^ mask[i % 4] for i, b in enumerate(payload)
                                    )
                                )

                            def send(value):
                                payload = json.dumps(value).encode()
                                header = (
                                    bytes([129, len(payload)])
                                    if len(payload) < 126
                                    else b"\x81\x7e" + struct.pack("!H", len(payload))
                                )
                                connection.sendall(header + payload)

                            self.assertEqual(receive()["method"], "initialize")
                            send({"id": 1, "result": {}})
                            self.assertEqual(receive()["method"], "initialized")
                            self.assertEqual(receive()["method"], "hooks/list")
                            send({"id": 2, "result": {"data": [{"hooks": [expected]}]}})
                            self.assertEqual(stream.read(1), b"")
                    except Exception as error:
                        failures.append(error)

                thread = threading.Thread(target=serve, daemon=True)
                thread.start()
                try:
                    self.assertEqual(
                        integration_probe.codex_hooks(directory), [expected]
                    )
                finally:
                    thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(failures, [])
            with self.assertRaises(ValueError):
                integration_probe.codex_hooks(directory)

    def test_websocket_probe_handles_fragmentation_ping_and_rejects_oversized_frames(
        self,
    ):
        import integration_probe
        import time
        import struct

        client, server = socket.socketpair()
        with client, server:
            connection = integration_probe.Connection(client, time.monotonic() + 1)
            server.sendall(b'\x01\x04{"id' + b"\x89\x01x" + b'\x80\x04":2}')
            self.assertEqual(connection.receive(), {"id": 2})
            pong = server.recv(7)
            self.assertEqual(pong[:2], b"\x8a\x81")
            self.assertEqual(pong[-1] ^ pong[2], ord("x"))
            server.sendall(b"\x81\x7f" + struct.pack("!Q", 4 * 1024 * 1024 + 1))
            with self.assertRaises(ValueError):
                connection.receive()


if __name__ == "__main__":
    unittest.main()
