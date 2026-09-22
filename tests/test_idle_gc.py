"""Synthetic process trees: no test signals a real process or uses live state."""

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
import idle_gc


def row(parent, command, cpu=0, start="start", uid=None):
    return dict(
        ppid=parent,
        command=command,
        cpu=cpu,
        start=start,
        uid=os.getuid() if uid is None else uid,
    )


def fixture():
    return {
        "1": row(0, "/sbin/launchd"),
        "10": row(1, "claude --dangerously-skip-permissions"),
        "11": row(10, "node /tools/playwright-mcp"),
        "12": row(
            11,
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
            "--user-data-dir=/tmp/ms-playwright-mcp/browser",
        ),
        "13": row(
            12,
            "/Applications/Google Chrome.app/Contents/Frameworks/Chrome Helper "
            "--type=renderer",
        ),
        "14": row(10, "gopls"),
        "20": row(1, "claude"),
        "21": row(20, "go test ./..."),
        "90": row(10, "/bin/bash /memcap feedback"),
    }


class IdleGCTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gc = idle_gc.Collector(Path(self.tmp.name), grace=600)
        self.table = fixture()
        self.net = dict(known=True, connected=[], listeners=[])

    def event(self, event, session="a", caller="90", **extra):
        self.gc.event(
            dict(hook_event_name=event, session_id=session, cwd=self.tmp.name, **extra),
            caller,
            self.table,
            100,
        )

    def scan(self, now):
        table = copy.deepcopy(self.table)
        table.pop("90", None)  # the completed hook is not a background job
        return self.gc.scan(table, self.net, True, now)

    def idle(self):
        self.event("SessionStart")
        self.event("Stop")

    def test_unknown_session_is_never_treated_as_idle(self):
        self.scan(100)
        self.assertEqual(self.scan(700), [])

    def test_active_session_stays_protected_across_full_idle_grace(self):
        self.event("SessionStart")
        self.scan(100)
        self.assertEqual(self.scan(700), [])

    def test_unrelated_transient_hook_does_not_erase_idle_history(self):
        self.idle()
        self.scan(100)
        pending = self.gc.directory.parent / "gc-activity-pending"
        pending.mkdir()
        marker = pending / "99"
        marker.write_text("pending")
        self.assertEqual(self.scan(600), [])
        marker.unlink()
        self.assertEqual(len(self.scan(700)), 1)

    def test_sessions_in_one_host_only_wait_for_their_own_jobs(self):
        self.table["30"] = row(10, "python3 /memcap/scheduler.py run")
        q = self.gc.directory.parent / "queue"
        q.mkdir()
        (q / "jobs.json").write_text(
            json.dumps(
                {
                    "jobs": [
                        dict(
                            id="a-job",
                            owner=30,
                            owner_start="start",
                            status="waiting",
                            resource="",
                            session_key=idle_gc.hashlib.sha256(b"a").hexdigest(),
                        )
                    ]
                }
            )
        )
        self.assertEqual(self.gc.continuation("90", self.table, "b"), {})
        self.assertEqual(
            self.gc.continuation("90", self.table, "a")["decision"], "block"
        )

    def test_stop_wait_spends_a_minute_locally_without_model_or_state_writes(self):
        now = [0]

        def sleep(seconds):
            self.assertGreater(seconds, 0)
            now[0] += seconds

        response = {"decision": "block", "reason": "pending"}
        with patch.object(self.gc, "continuation", return_value=response) as pending:
            result = idle_gc.wait_for_pending(
                self.gc,
                "90",
                "a",
                clock=lambda: now[0],
                sleep=sleep,
                table_reader=lambda: self.table,
            )
        self.assertEqual(now[0], 60)
        self.assertEqual(result, response)
        self.assertEqual(pending.call_count, 31)
        self.assertFalse(self.gc.activity_pending())

    def test_stop_wait_returns_promptly_on_completion_or_no_pending_work(self):
        now = [0]

        def sleep(seconds):
            now[0] += seconds

        for responses, expected_seconds in (
            ([{}], 0),
            ([{"decision": "block"}, {}], 2),
        ):
            now[0] = 0
            with patch.object(self.gc, "continuation", side_effect=responses):
                result = idle_gc.wait_for_pending(
                    self.gc,
                    "90",
                    "a",
                    clock=lambda: now[0],
                    sleep=sleep,
                    table_reader=lambda: self.table,
                )
            self.assertEqual(now[0], expected_seconds)
            if expected_seconds:
                self.assertIn("final output and exit status", result["reason"])
            else:
                self.assertEqual(result, {})

    def test_runtime_path_with_spaces_is_collected(self):
        self.table = {
            "1": row(0, "/sbin/launchd"),
            "30": row(
                1,
                "/Library/Developer/CoreSimulator/Volumes/iOS_23F77/Library/Developer/CoreSimulator/Profiles/Runtimes/iOS 26.5.simruntime/Contents/Resources/RuntimeRoot/usr/libexec/assistantd",
            ),
        }
        self.scan(100)
        self.assertEqual(len(self.scan(700)), 1)

    def test_failed_or_inflight_hook_invalidates_authorizations(self):
        self.idle()
        self.scan(100)
        self.scan(700)
        pending = self.gc.directory.parent / "gc-activity-pending"
        pending.mkdir()
        (pending / "123").write_text("pending")
        self.assertEqual(self.scan(701), [])
        self.assertFalse(self.gc.authorize("12", self.table, self.net, True, 701))

    def test_registered_resource_supervisor_is_not_mistaken_for_a_build(self):
        self.table["12"] = row(31, "node /project/node_modules/.bin/vite")
        self.table.pop("13")
        self.table["30"] = row(
            10, "python3 /memcap/libexec/scheduler.py run --resource vite"
        )
        self.table["31"] = row(30, "/bin/bash -c vite")
        q = self.gc.directory.parent / "queue"
        q.mkdir()
        (q / "jobs.json").write_text(
            json.dumps(
                {
                    "jobs": [
                        dict(
                            owner=30,
                            owner_start="start",
                            status="running",
                            resource="vite",
                            members={"31": "start", "12": "start"},
                        )
                    ]
                }
            )
        )
        self.net["listeners"] = ["12"]
        self.idle()
        self.scan(100)
        self.assertEqual(len(self.scan(700)), 1)

    def test_fake_registered_resource_identity_cannot_exempt_a_build(self):
        self.table["30"] = row(
            10, "python3 /memcap/libexec/scheduler.py run --resource vite"
        )
        q = self.gc.directory.parent / "queue"
        q.mkdir()
        (q / "jobs.json").write_text(
            json.dumps(
                {
                    "jobs": [
                        dict(
                            owner=30,
                            owner_start="reused",
                            status="running",
                            resource="vite",
                            members={},
                        )
                    ]
                }
            )
        )
        self.idle()
        self.scan(100)
        self.assertEqual(self.scan(700), [])

    def test_child_command_cannot_claim_to_be_a_renderer(self):
        self.idle()
        self.scan(100)
        self.table["13"]["command"] = "python train.py --type=renderer"
        self.assertEqual(self.scan(700), [])

    def test_last_child_completion_releases_finished_parent(self):
        self.event("SubagentStart", agent_id="child")
        self.event("Stop")
        self.event("SubagentStop", agent_id="child")
        self.scan(100)
        self.assertEqual(len(self.scan(700)), 1)

    def test_stop_keeps_polling_own_finite_jobs_but_not_other_sessions(self):
        self.table["30"] = row(10, "python3 /memcap/scheduler.py run")
        self.table["40"] = row(20, "python3 /memcap/scheduler.py run")
        q = self.gc.directory.parent / "queue"
        q.mkdir()
        jobs = [
            dict(
                id="mine", owner=30, owner_start="start", status="waiting", resource=""
            ),
            dict(
                id="other", owner=40, owner_start="start", status="waiting", resource=""
            ),
        ]
        (q / "jobs.json").write_text(json.dumps({"jobs": jobs}))
        response = self.gc.continuation("90", self.table)
        self.assertEqual(response["decision"], "block")
        self.assertIn("mine", response["reason"])
        self.assertNotIn("other", response["reason"])
        jobs[0]["resource"] = "dev-server"
        (q / "jobs.json").write_text(json.dumps({"jobs": jobs}))
        self.assertEqual(self.gc.continuation("90", self.table), {})

    def test_cancelled_or_dead_job_does_not_force_continuation(self):
        q = self.gc.directory.parent / "queue"
        q.mkdir()
        (q / "jobs.json").write_text(
            json.dumps(
                {
                    "jobs": [
                        dict(
                            id="old",
                            owner=30,
                            owner_start="old",
                            status="waiting",
                            resource="",
                            cancel=True,
                        )
                    ]
                }
            )
        )
        self.assertEqual(self.gc.continuation("90", self.table), {})

    def test_native_background_work_keeps_session_active(self):
        self.gc.event(
            dict(
                hook_event_name="Stop",
                session_id="a",
                background_tasks=[dict(id="t", type="subagent", status="running")],
            ),
            "90",
            self.table,
            100,
        )
        self.scan(100)
        self.assertEqual(self.scan(700), [])

    def test_active_session_in_same_project_protects_shared_server(self):
        self.table["12"] = row(10, "node /project/node_modules/.bin/vite")
        self.table.pop("13")
        self.net["listeners"] = ["12"]
        self.idle()
        self.scan(100)
        self.table["91"] = row(20, "bash /memcap feedback")
        self.gc.event(
            dict(hook_event_name="UserPromptSubmit", session_id="b", cwd=self.tmp.name),
            "91",
            self.table,
            500,
        )
        self.table.pop("91")
        self.assertEqual(self.scan(700), [])

    def test_sourcekit_language_server_is_not_a_booted_simulator(self):
        table = {
            "10": row(
                1,
                "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/sourcekit-lsp",
            )
        }
        with patch.object(
            idle_gc.subprocess,
            "run",
            return_value=type(
                "Result", (), dict(returncode=0, stdout='{"devices":{"iOS":[]}}')
            )(),
        ):
            self.assertTrue(idle_gc.simulators_clear(table))

    def test_completed_session_browser_reclaimed_after_full_grace(self):
        self.idle()
        self.assertEqual(self.scan(100), [])
        self.assertEqual(self.scan(699), [])
        ready = self.scan(700)
        self.assertEqual([r["pid"] for r in ready], ["12"])
        self.assertEqual(set(ready[0]["members"]), {"12", "13"})
        self.assertNotIn("10", ready[0]["members"])
        self.assertNotIn("11", ready[0]["members"])
        self.assertNotIn("14", ready[0]["members"])

    def test_new_prompt_or_tool_cancels_idle_cleanup(self):
        for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse"):
            with self.subTest(event=event):
                self.idle()
                self.scan(100)
                self.event(event)
                self.assertEqual(self.scan(700), [])

    def test_subagent_outlives_parent_turn(self):
        self.event("SubagentStart", agent_id="child")
        self.event("Stop")
        self.scan(100)
        self.assertEqual(self.scan(700), [])
        self.event("SubagentStop", agent_id="child")
        self.event("Stop")
        self.assertEqual(self.scan(1100), [])
        self.assertEqual(len(self.scan(1700)), 1)

    def test_missing_subagent_identity_fails_closed(self):
        self.event("SubagentStart")
        self.event("Stop")
        self.scan(100)
        self.assertEqual(self.scan(700), [])

    def test_all_sessions_sharing_agent_pid_must_be_idle(self):
        self.idle()
        self.event("UserPromptSubmit", session="b")
        self.scan(100)
        self.assertEqual(self.scan(700), [])

    def test_child_build_vetoes_even_when_cpu_is_flat(self):
        self.idle()
        self.scan(100)
        self.table["15"] = row(10, "xcodebuild archive")
        self.assertEqual(self.scan(700), [])

    def test_queued_job_is_not_idle(self):
        self.idle()
        self.scan(100)
        self.table["15"] = row(
            10, "python3 /memcap/scheduler.py run --shell-command make"
        )
        self.assertEqual(self.scan(700), [])

    def test_new_child_cpu_or_pid_reuse_restarts_grace(self):
        for change in ("cpu", "start", "child"):
            with self.subTest(change=change):
                self.table = fixture()
                self.idle()
                self.scan(100)
                if change == "cpu":
                    self.table["13"]["cpu"] = 5
                elif change == "start":
                    self.table["12"]["start"] = "reused"
                else:
                    self.table["16"] = row(
                        12, "/Applications/Chrome Helper --type=utility"
                    )
                self.assertEqual(self.scan(700), [])
                self.assertEqual(len(self.scan(1300)), 1)

    def test_active_connections_or_unknown_network_state_block(self):
        self.idle()
        self.scan(100)
        self.net["connected"] = ["13"]
        self.assertEqual(self.scan(700), [])
        self.net["connected"] = []
        self.net["known"] = False
        self.assertEqual(self.scan(2000), [])

    def test_unrelated_active_agent_does_not_pin_idle_browser(self):
        self.idle()
        self.scan(100)
        self.assertEqual(len(self.scan(700)), 1)

    def test_same_user_required_for_every_member(self):
        self.idle()
        self.scan(100)
        self.table["13"]["uid"] = os.getuid() + 1
        self.assertEqual(self.scan(700), [])

    def test_dev_server_requires_listener_and_completed_owner(self):
        self.table["12"] = row(10, "node /project/node_modules/.bin/vite")
        self.table.pop("13")
        self.idle()
        self.scan(100)
        self.assertEqual(self.scan(700), [])
        self.net["listeners"] = ["12"]
        self.assertEqual(self.scan(1100), [])
        self.assertEqual(self.scan(1700)[0]["kind"], "dev-server")

    def test_shell_mention_and_regular_chrome_are_not_targets(self):
        for cmd in (
            '/bin/bash -c "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/tmp/ms-playwright-mcp/browser"',
            "node /project/train.js --output=/project/node_modules/.bin/vite",
            'rg "ms-playwright-mcp" file',
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ):
            with self.subTest(command=cmd):
                self.table["12"]["command"] = cmd
                self.idle()
                self.scan(100)
                self.assertEqual(self.scan(700), [])

    def test_detached_sim_runtime_requires_successful_empty_device_query(self):
        self.table = {
            "1": row(0, "/sbin/launchd"),
            "30": row(
                1,
                "/Library/Developer/CoreSimulator/Volumes/iOS/Library/Developer/CoreSimulator/Profiles/Runtimes/iOS.simruntime/Contents/Resources/RuntimeRoot/usr/libexec/assistantd",
            ),
        }
        self.scan(100)
        self.assertEqual(len(self.scan(700)), 1)
        for clear in (False, None):
            self.assertEqual(self.gc.scan(self.table, self.net, clear, 800), [])

    def test_simulator_child_or_foreign_user_is_not_abandoned(self):
        self.table["30"] = row(
            20,
            "/Library/Developer/CoreSimulator/x.simruntime/Contents/Resources/RuntimeRoot/usr/libexec/assistantd",
        )
        self.scan(100)
        self.assertEqual(self.scan(700), [])

    def test_authorization_rechecks_activity_identity_and_expiry(self):
        self.idle()
        self.scan(100)
        self.scan(700)
        table = copy.deepcopy(self.table)
        table.pop("90")
        self.assertTrue(self.gc.authorize("12", table, self.net, True, 701))
        self.assertFalse(self.gc.authorize("12", table, self.net, True, 800))
        table["12"]["start"] = "new"
        self.assertFalse(self.gc.authorize("12", table, self.net, True, 701))
        self.event("UserPromptSubmit")
        self.assertFalse(self.gc.authorize("13", self.table, self.net, True, 701))

    def test_missing_corrupt_state_never_authorizes(self):
        self.assertFalse(self.gc.authorize("12", self.table, self.net, True, 700))
        (self.gc.directory / "state.json").write_text('{"sessions":"bad"}')
        with self.assertRaises(idle_gc.GCError):
            self.scan(700)

    def test_state_private_bounded_and_does_not_store_prompt(self):
        self.event(
            "UserPromptSubmit", prompt="SECRET", tool_input={"command": "SECRET"}
        )
        data = (self.gc.directory / "state.json").read_text()
        self.assertNotIn("SECRET", data)
        self.assertEqual(
            (self.gc.directory / "state.json").stat().st_mode & 0o777, 0o600
        )
        self.assertEqual(self.gc.directory.stat().st_mode & 0o777, 0o700)
        self.assertIsInstance(json.loads(data)["sessions"], dict)


if __name__ == "__main__":
    unittest.main()
