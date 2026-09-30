"""Analytics contracts: independent of enforcement and private by construction."""
import json
import errno
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from http.client import HTTPConnection
import queue
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
import analytics_events as events
from analytics_store import Store
from analytics_reports import summarize, compare, interval_union
from analytics_otlp import decode_logs
from analytics_install import configure_profiles, initialize, telemetry_environment


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "analytics"
        self.root.mkdir(mode=0o700)
        (self.root / "key").write_bytes(b"k" * 32)
        (self.root / "enabled").touch()
        self.addCleanup(self.temp.cleanup)

    def row(self, event, sequence=1, **fields):
        return events.make_event(event, fields, b"k" * 32, "a" * 32,
                                 sequence, build="b" * 64, policy="c" * 64,
                                 boot="d" * 32, wall=1000 + sequence,
                                 mono=100 + sequence)

    def test_secrets_never_survive_typed_boundary(self):
        row = self.row("hook", session="secret-session", command="SSM-SECRET",
                       prompt="PRIVATE", tool="Bash", phase="PreToolUse", duration_ms=5)
        encoded = json.dumps(row)
        self.assertNotIn("secret-session", encoded)
        self.assertNotIn("SSM-SECRET", encoded)
        self.assertNotIn("PRIVATE", encoded)
        self.assertEqual(row["duration_ms"], 5)
        self.assertNotIn("duration_ms", self.row("hook", duration_ms=float("nan")))

    def test_absent_recorder_does_not_raise_or_retry(self):
        producer = events.Producer(self.root)
        self.addCleanup(producer.close)
        self.assertFalse(producer.emit("hook", phase="PreToolUse"))
        self.assertEqual(producer.dropped, 1)
        receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        receiver.bind(str(self.root / "events.sock"))
        self.addCleanup(receiver.close)
        self.assertTrue(producer.emit("hook", phase="PostToolUse"))
        row = json.loads(receiver.recv(8192))
        self.assertEqual(row["producer_dropped"], 1)

    def test_legacy_import_handles_macos_backpressure_and_reports_drops(self):
        import analytics
        queue_dir = self.root.parent / "queue"
        queue_dir.mkdir()
        (queue_dir / "events.jsonl").write_text(json.dumps({
            "event": "completed", "timestamp": 1000, "job_ref": "old", "runtime_ms": 10
        }) + "\n")
        source = Mock(key=b"k" * 32)
        full = OSError(errno.ENOBUFS, "No buffer space available")
        source.socket.sendto.side_effect = [full, 100]
        with patch.object(analytics, "Producer", return_value=source), patch.object(analytics.time, "sleep"):
            result = analytics.import_legacy(self.root)
            self.assertEqual(result["submitted"], 1)
            self.assertEqual(result["dropped"], 0)
            self.assertEqual(source.socket.sendto.call_count, 2)
            source.socket.sendto.reset_mock(side_effect=True)
            source.socket.sendto.side_effect = full
            result = analytics.import_legacy(self.root)
            self.assertEqual(result["submitted"], 0)
            self.assertEqual(result["dropped"], 1)
            self.assertEqual(source.socket.sendto.call_count, 2)
            source.socket.sendto.side_effect = FileNotFoundError(errno.ENOENT, "No recorder")
            with self.assertRaises(FileNotFoundError):
                analytics.import_legacy(self.root)
        source.close.assert_called()

    def test_duplicate_and_out_of_order_records_preserve_unknown(self):
        with Store(self.root) as store:
            end = self.row("completed", 2, job="one", runtime_ms=10, exit_code=0)
            store.insert(end)
            store.insert(end)
            store.insert(self.row("queued", 1, job="one"))
            store.insert(self.row("queued", 3, job="unfinished"))
            report = summarize(store.rows())
            self.assertEqual(report["jobs"]["started"], 2)
            self.assertEqual(report["jobs"]["unfinished"], 1)
            self.assertEqual(report["jobs"]["succeeded"], 1)
            self.assertEqual(len(store.rows()), 3)

    def test_parallel_waits_are_not_added_as_elapsed_time(self):
        self.assertEqual(interval_union([(0, 60), (20, 80), (90, 100)]), 90)

    def test_persistent_reuse_is_not_finite_job_failure(self):
        report = summarize([self.row("queued", 1, job="request", persistent=1),
                            self.row("claim", 2, job="request", resource="server", persistent=1)])
        self.assertEqual(report["jobs"]["started"], 0)
        self.assertEqual(report["persistent_resources"]["reuse_claims"], 1)

    def test_regression_negative_control_and_missing_cohorts(self):
        baseline = [self.row("completed", i, job=str(i), runtime_ms=1000,
                             queue_wait_ms=0, exit_code=0, family="test") for i in range(1, 11)]
        slow = [dict(r, runtime_ms=10000, build="e" * 64) for r in baseline]
        self.assertTrue(compare(baseline, slow)["regression"])
        self.assertFalse(compare(baseline, baseline)["regression"])
        self.assertEqual(compare([], slow)["verdict"], "insufficient_evidence")

    def test_comparison_joins_admission_wait_and_ignores_feedback_duplicates(self):
        before, after = [], []
        for i in range(1, 11):
            end = self.row("completed", i * 2, job=str(i), runtime_ms=1000, exit_code=0)
            admission = self.row("admitted", i * 2 - 1, job=str(i), queue_wait_ms=0)
            before.extend([admission, end])
            after.extend([{**admission, "queue_wait_ms": 100000}, end])
        self.assertTrue(compare(before, after)["regression"])
        start = self.row("hook", 1, operation="o", phase="PreToolUse")
        end = self.row("hook", 2, operation="o", phase="PostToolUse")
        feedback = {**end, "event": "feedback", "seq": 3}
        report = summarize([start, end, feedback, self.row("hook", 4, phase="Stop"),
                            self.row("stop_wait", 5, phase="Stop", blocked=1)])
        self.assertEqual(report["tool_elapsed_ms"]["n"], 1)
        self.assertEqual(report["stop_attempts"], 1)

    def test_retention_is_visible_and_bounded(self):
        with Store(self.root) as store:
            store.insert(self.row("hook", phase="PreToolUse"))
            store.maintain(now=1000 + 15 * 86400)
            self.assertEqual(store.rows(), [])
            self.assertGreater(store.health()["evicted_events"], 0)

    def test_clock_changes_and_reboots_do_not_fabricate_tool_time(self):
        start = self.row("hook", 1, session="s", operation="o", phase="PreToolUse")
        end = self.row("hook", 2, session="s", operation="o", phase="PostToolUse")
        self.assertEqual(summarize([start, end])["tool_elapsed_ms"]["median"], 1000)
        for change in ({"boot": "e" * 32}, {"mono": 99}, {"wall": end["wall"] + 3600}):
            self.assertEqual(summarize([start, {**end, **change}])["tool_elapsed_ms"]["n"], 0)

    def test_stop_and_background_ack_are_not_accepted_work(self):
        rows = [self.row("hook", 1, phase="Stop", session="s"),
                self.row("hook", 2, phase="PostToolUse", session="s", tool_response={"status": "background"}),
                self.row("queued", 3, job="pending")]
        report = summarize(rows)
        self.assertEqual(report["stop_attempts"], 1)
        self.assertEqual(report["jobs"]["unfinished"], 1)
        self.assertEqual(report["jobs"]["succeeded"], 0)

    def test_legacy_and_missing_paging_are_explicit(self):
        report = summarize([self.row("completed", job="old", legacy=1, runtime_ms=100)])
        self.assertEqual(report["coverage"]["legacy_events"], 1)
        self.assertIsNone(report["machine"]["red_seconds"])
        self.assertIsNone(report["native_api"]["input_tokens"])

    def test_otlp_secrets_not_forwarded_and_ids_match_hooks(self):
        attrs = {"event.name": "api_request", "session.id": "session",
                 "input_tokens": "123", "duration_ms": "10", "cost_usd": "0.001",
                 "prompt": "SECRET", "account.uuid": "ACCOUNT", "api_request": "BODY"}
        body = {"resourceLogs": [{"scopeLogs": [{"logRecords": [{"attributes": [
            {"key": k, "value": {"stringValue": v}} for k, v in attrs.items()]}]}]}]}
        decoded = decode_logs(body)
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0][1]["input_tokens"], 123)
        encoded = json.dumps(decoded)
        for secret in ("SECRET", "ACCOUNT", "BODY"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(decoded[0][1]["session"], events.hook_fields({"session_id": "session"})["session"])

    def test_native_delivery_retry_deduplicates_without_dropping_distinct_events(self):
        with Store(self.root) as store:
            store.insert(self.row("api", 1, delivery="same", input_tokens=10))
            store.insert(self.row("api", 2, delivery="same", input_tokens=10))
            store.insert(self.row("api", 3, delivery="different", input_tokens=20))
            self.assertEqual(summarize(store.rows())["native_api"]["input_tokens"], 30)

    def test_profile_install_is_idempotent_and_preserves_exporters(self):
        first, second = Path(self.temp.name) / "claude", Path(self.temp.name) / "personal"
        first.mkdir()
        second.mkdir()
        original = {"permissions": {"allow": ["Read"]}, "hooks": {"Stop": []}}
        (first / "settings.json").write_text(json.dumps(original))
        foreign = {"env": {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://owner.example"}}
        (second / "settings.json").write_text(json.dumps(foreign))
        configure_profiles([first, second], "token", 43190)
        configured = (first / "settings.json").read_bytes()
        before = (first / "settings.json").stat().st_mtime_ns
        self.assertEqual(json.loads(configured)["hooks"], original["hooks"])
        self.assertEqual(json.loads((second / "settings.json").read_text()), foreign)
        configure_profiles([first, second], "token", 43190)
        self.assertEqual((first / "settings.json").stat().st_mtime_ns, before)
        self.assertEqual(len(list(first.glob(".settings.json.memcap-backup-*"))), 1)

    def test_malformed_later_profile_prevents_all_writes(self):
        first, second = Path(self.temp.name) / "first", Path(self.temp.name) / "second"
        first.mkdir()
        second.mkdir()
        (first / "settings.json").write_text("{}")
        (second / "settings.json").write_text("{broken")
        with self.assertRaises(Exception):
            configure_profiles([first, second], "token", 43190)
        self.assertEqual((first / "settings.json").read_text(), "{}")

    def test_nonblocking_full_receiver_and_symlink_state(self):
        receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        receiver.bind(str(self.root / "events.sock"))
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        self.addCleanup(receiver.close)
        producer = events.Producer(self.root)
        self.addCleanup(producer.close)
        began = time.monotonic()
        for _ in range(1000):
            producer.emit("hook", phase="PreToolUse")
        self.assertGreater(producer.dropped, 0)
        self.assertLess(time.monotonic() - began, 2)
        link = Path(self.temp.name) / "link"
        link.symlink_to(self.root)
        self.assertFalse(events.Producer(link).emit("hook"))

    def test_loaded_provenance_survives_install_update(self):
        with patch.object(events, "build_digest", return_value="a" * 64):
            old = events.Producer(self.root)
        with patch.object(events, "build_digest", return_value="b" * 64):
            new = events.Producer(self.root)
        self.addCleanup(old.close)
        self.addCleanup(new.close)
        self.assertNotEqual(old.build, new.build)
        self.assertEqual(old.build, "a" * 64)

    def test_homebrew_wrapper_does_not_change_code_digest(self):
        digests = []
        for layout in ("source", "installed"):
            base = Path(self.temp.name) / layout
            (base / "libexec").mkdir(parents=True)
            (base / "bin").mkdir()
            (base / "libexec/analytics_events.py").write_text("# identical module\n")
            (base / "bin/memcap").write_text("# actual dispatcher\n" if layout == "source" else "# brew wrapper\n")
            if layout == "installed":
                (base / "bin/memcap-real").write_text("# actual dispatcher\n")
            events.build_digest.cache_clear()
            with patch.object(events, "__file__", str(base / "libexec/analytics_events.py")):
                digests.append(events.build_digest())
        events.build_digest.cache_clear()
        self.assertEqual(digests[0], digests[1])

    def test_service_install_is_separate_and_preserves_pause(self):
        from analytics_install import install_service, LABEL
        pause = self.root.parent / "paused"
        pause.write_text("owner pause")
        with patch.dict(os.environ, {"HOME": self.temp.name}), patch("analytics_install.subprocess.run") as run:
            run.side_effect = [SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)]
            target = install_service(self.root, "/opt/homebrew/opt/memcap/bin/memcap", 43190)
        self.assertEqual(pause.read_text(), "owner pause")
        self.assertIn(LABEL, target)
        self.assertEqual(run.call_args_list[1].args[0][:2], ["launchctl", "bootstrap"])
        self.assertNotIn("homebrew.mxcl.memcap", Path(target).read_text())

    def test_disk_full_error_does_not_affect_producer(self):
        producer = events.Producer(self.root)
        self.addCleanup(producer.close)
        with patch.object(events.socket, "socket") as fake:
            fake.return_value.sendto.side_effect = OSError("full")
            producer.socket.close()
            producer.socket = fake.return_value
            self.assertFalse(producer.emit("queued", job="j"))

    def test_actual_database_budget_refuses_growth_and_keeps_prior_rows(self):
        with Store(self.root, limit=1024 * 1024) as store:
            for i in range(1, 51):
                store.insert(self.row("hook", i, phase="PreToolUse"))
            store.db.commit()
            with self.assertRaises(sqlite3.OperationalError):
                for i in range(51, 10001):
                    store.insert(self.row("hook", i, phase="PreToolUse", session=str(i)))
            store.db.rollback()
            self.assertGreaterEqual(len(store.rows()), 50)
            self.assertLess(store.health()["disk_bytes"], store.limit)

    def test_otlp_http_authentication_and_documented_wire_format(self):
        from analytics_otlp import server
        inbox = queue.Queue(maxsize=1)
        http, thread = server(0, "private-token", inbox)
        def request(headers):
            connection = HTTPConnection("127.0.0.1", http.server_port, timeout=3)
            try:
                body = {"resourceLogs": [{"scopeLogs": [{"logRecords": [{"timeUnixNano": "123000000000",
                    "attributes": [{"key": "event.name", "value": {"stringValue": "api_request"}},
                                   {"key": "input_tokens", "value": {"intValue": "7"}}]}]}]}]}
                connection.request("POST", "/v1/logs", json.dumps(body), headers={"Content-Type": "application/json", **headers})
                response = connection.getresponse()
                response.read()
                return response.status
            finally:
                connection.close()
        try:
            self.assertEqual(request({}), 401)
            self.assertEqual(request({"Authorization": "Bearer private-token"}), 200)
            self.assertEqual(request({"Authorization": "Bearer private-token"}), 429)
            event, fields = inbox.get_nowait()[0]
            self.assertEqual(event, "api")
            self.assertEqual(fields["input_tokens"], 7)
            self.assertIn("delivery", fields)
        finally:
            http.shutdown()
            http.server_close()
            thread.join(timeout=3)

    def test_collector_round_trip_and_paused_hook_no_reservation(self):
        repo = Path(__file__).resolve().parents[1]
        state = Path(self.temp.name) / "state"
        root = state / "memcap/analytics"
        initialize(root)
        (root.parent / "paused").touch()
        env = {**os.environ, "MEMCAP_ROOT": str(repo), "MEMCAP_STATE_HOME": str(state),
               "MEMCAP_CONFIG_HOME": str(Path(self.temp.name) / "config"),
               "MC_DRY_RUN": "1", "HOME": self.temp.name}
        code = """
import faulthandler
import sys
import traceback
faulthandler.dump_traceback_later(7)
sys.path.insert(0, sys.argv[1])
import analytics_collector as c
c.host_sample = lambda d, p: ({'pressure': 2, 'available_kb': 123}, {})
def diagnostic(frame, event, arg):
    if event == 'exception' and frame.f_code.co_filename == c.__file__:
        if isinstance(arg[1], (c.sqlite3.Error, OSError)) and not isinstance(arg[1], BlockingIOError):
            traceback.print_exception(*arg)
    return diagnostic
sys.settrace(diagnostic)
c.collect(__import__('pathlib').Path(sys.argv[2]), 0)
"""
        child = subprocess.Popen([sys.executable, "-c", code, str(repo / "libexec"), str(root)], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 8
            while not (root / "heartbeat.json").exists() and time.monotonic() < deadline:
                if child.poll() is not None:
                    self.fail(child.communicate()[1].decode())
                time.sleep(.05)
            if not (root / "heartbeat.json").exists():
                child.terminate()
                _, errors = child.communicate(timeout=8)
                self.fail("collector heartbeat deadline expired:\n" + errors.decode())
            payload = dict(hook_event_name="PreToolUse", session_id="private", tool_use_id="call",
                           cwd=self.temp.name, tool_name="Bash", tool_input={"command": "cat SECRET"})
            result = subprocess.run([str(repo / "bin/memcap"), "feedback"], input=json.dumps(payload),
                                    text=True, capture_output=True, env=env, timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root.parent / "queue/jobs.json").exists())
            from analytics_store import read_rows
            rows = []
            while time.monotonic() < deadline:
                rows, _ = read_rows(root)
                if any(r.get("route") == "paused" for r in rows):
                    break
                time.sleep(.05)
            self.assertTrue(any(r.get("route") == "paused" for r in rows), (rows, result.stderr))
            self.assertNotIn("SECRET", json.dumps(rows))
        finally:
            child.terminate()
            child.communicate(timeout=8)


if __name__ == "__main__":
    unittest.main()
