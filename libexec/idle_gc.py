"""Lifecycle-based helper collection. Plans and authorizes; never sends signals.

Unknown ownership, missing hooks, active jobs, failed measurements and malformed
state all retain resources. Only the shell's mc_kill_pids can act on a plan.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time


class GCError(Exception):
    pass


AGENT = re.compile(r"^(?:\S*/)?(?:claude|codex)(?:\s|$)")
MCP = re.compile(r"(?:^|[ /@_-])mcp(?:[ /@_.-]|$)|modelcontextprotocol")
SIM = re.compile(
    r"^/Library/Developer/CoreSimulator/[^\n]*?\.simruntime/Contents/Resources/RuntimeRoot/\S+"
)


def executable(command):
    return Path(command.split()[0]).name if command.split() else ""


def browser(command):
    # argv[0] must be a browser; a mention in rg/bash arguments never qualifies.
    chrome = re.match(
        r"^/(?:Applications|Users/[^ /]+/Applications)/Google Chrome\.app/Contents/MacOS/Google Chrome(?:\s|$)",
        command,
    )
    headless = re.match(
        r"^\S*/(?:chrome-headless-shell|headless_shell|chrome)(?:\s|$)", command
    )
    return bool(
        (chrome or headless)
        and re.search(
            r"--user-data-dir=\S*(?:ms-playwright|playwright|pw-browser)", command
        )
    )


def dev_server(command):
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if not words:
        return False
    name = Path(words[0]).name
    if name in {"vite", "next-server"}:
        return True
    if name != "node":
        return False
    i = 1
    while i < len(words) and words[i].startswith("-"):
        arg = words[i]
        if arg in {"--require", "-r", "--import"}:
            i += 2
        elif arg.startswith(
            ("--require=", "--import=", "--inspect", "--max-old-space-size=")
        ):
            i += 1
        else:
            return False
    if i >= len(words):
        return False
    script = words[i]
    return (
        Path(script).name == "vite"
        or (Path(script).name == "next" and words[i + 1 : i + 2] == ["dev"])
        or bool(re.search(r"(?:^|/)server/index\.[cm]?[jt]s$", script))
    )


def browser_helper(command):
    return bool(
        re.match(
            r"^/Applications/(?:Google Chrome\.app/[^\n]*?|Chrome Helper)(?:[ /]|$)",
            command,
        )
        and re.search(r"(?:^|\s)--type=", command)
    )


def disposable_task(task):
    if task.get("type") != "shell":
        return False
    cmd = task.get("command", "")
    if dev_server(cmd):
        return True
    try:
        words = shlex.split(cmd)
    except ValueError:
        return False
    return (
        len(words) > 2
        and Path(words[0]).name == "memcap"
        and words[1] == "run"
        and "--resource"
        in words[
            : words.index("--shell-command")
            if "--shell-command" in words
            else len(words)
        ]
    )


def resource_transport(pid, root, table, jobs):
    """Only verified resource supervisors and their direct launch ancestry."""
    ancestors = set()
    current = root
    while current in table and current not in ancestors and len(ancestors) < 64:
        ancestors.add(current)
        current = str(table[current]["ppid"])
    if pid not in ancestors:
        return False
    for job in jobs:
        supervisor = str(job.get("owner", ""))
        if (
            job.get("status") == "running"
            and job.get("resource")
            and supervisor in ancestors
            and supervisor in table
            and table[supervisor]["uid"] == os.getuid()
            and table[supervisor]["start"] == job.get("owner_start")
            and job.get("members", {}).get(root) == table[root]["start"]
        ):
            return executable(table[pid]["command"]) in {
                "python",
                "python3",
                "bash",
                "zsh",
                "sh",
                "npm",
                "pnpm",
                "yarn",
                "node",
            }
    return False


def descendants(pid, table):
    found = {pid}
    for _ in range(64):
        more = {p for p, row in table.items() if str(row["ppid"]) in found}
        if more <= found:
            return found
        found |= more
    raise GCError("process tree too deep")


def owner(pid, table):
    seen = set()
    while pid in table and pid not in seen and len(seen) < 64:
        seen.add(pid)
        if AGENT.match(table[pid]["command"]):
            return pid
        pid = str(table[pid]["ppid"])
    return None


def process_table():
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,uid=,lstart=,time=,stat=,command="],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode or not result.stdout.strip():
        raise GCError("process measurement unavailable")
    table = {}
    for line in result.stdout.splitlines():
        p = line.split(None, 10)
        if len(p) != 11:
            raise GCError("incomplete process measurement")
        if "Z" in p[9]:
            continue
        cpu = sum(float(v) * 60**i for i, v in enumerate(reversed(p[8].split(":"))))
        if not math.isfinite(cpu) or cpu < 0:
            raise GCError("invalid CPU measurement")
        table[p[0]] = dict(
            ppid=int(p[1]),
            uid=int(p[2]),
            start=" ".join(p[3:8]),
            cpu=cpu,
            command=p[10],
        )
    return table


def network_state():
    # One read for the host. A diagnostic or partial result is uncertainty, not
    # proof that there are no clients. TCP connections include browser helpers.
    p = subprocess.run(
        ["lsof", "-nP", "-iTCP", "-FpnT"], capture_output=True, text=True, timeout=10
    )
    if (
        p.returncode not in (0, 1)
        or p.stderr.strip()
        or (p.returncode == 1 and p.stdout.strip())
    ):
        return dict(known=False, connected=[], listeners=[])
    connected, listeners, pid = set(), set(), None
    for line in p.stdout.splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("TST=") and pid:
            if line == "TST=LISTEN":
                listeners.add(pid)
            elif line != "TST=CLOSED":
                connected.add(pid)
    return dict(known=True, connected=sorted(connected), listeners=sorted(listeners))


def simulators_clear(table):
    for row in table.values():
        cmd = row["command"]
        if executable(cmd) in {
            "xcodebuild",
            "xcrun",
            "simctl",
            "launchd_sim",
        } or re.match(
            r"^/Applications/(?:Xcode\.app/Contents/MacOS/Xcode|(?:Xcode\.app/Contents/Developer/Applications/)?Simulator\.app/Contents/MacOS/Simulator)(?:\s|$)",
            cmd,
        ):
            return False
        if executable(cmd) in {"java", "maestro"} and (
            "maestro" in cmd or executable(cmd) == "maestro"
        ):
            return False
    try:
        p = subprocess.run(
            [
                os.environ.get("MC_XCRUN_BIN", "xcrun"),
                "simctl",
                "list",
                "devices",
                "-j",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if p.returncode:
            return False
        devices = json.loads(p.stdout)["devices"]
        if not isinstance(devices, dict) or not all(
            isinstance(v, list) for v in devices.values()
        ):
            return False
        return all(
            d.get("state") == "Shutdown" for group in devices.values() for d in group
        )
    except (OSError, ValueError, KeyError, AttributeError, subprocess.SubprocessError):
        return False


class Collector:
    def __init__(self, state_root, grace=600):
        self.directory = Path(state_root) / "idle-gc"
        self.grace = grace
        self.resources = []
        if not isinstance(grace, int) or not 300 <= grace <= 86400:
            raise GCError("GC_IDLE_SEC must be between 300 and 86400 seconds")

    @contextmanager
    def locked(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or self.directory.stat().st_uid != os.getuid():
            raise GCError("GC directory must be owned by this user, without symlinks")
        os.chmod(self.directory, 0o700)
        fd = os.open(
            self.directory / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            deadline = time.monotonic() + 2
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise GCError("GC state lock unavailable")
                    time.sleep(0.02)
            path = self.directory / "state.json"
            try:
                if path.is_symlink():
                    raise GCError("GC state cannot be a symlink")
                data = json.loads(path.read_text())
                if set(data) != {"sessions", "candidates"} or not all(
                    isinstance(v, dict) for v in data.values()
                ):
                    raise ValueError("invalid GC state")
                for s in data["sessions"].values():
                    if (
                        not isinstance(s, dict)
                        or not isinstance(s.get("owner"), str)
                        or not isinstance(s.get("start"), str)
                        or not isinstance(s.get("at"), (int, float))
                        or s.get("phase") not in {"active", "idle"}
                        or not isinstance(s.get("children"), list)
                    ):
                        raise ValueError("invalid session")
            except FileNotFoundError:
                data = dict(sessions={}, candidates={})
            except (ValueError, TypeError) as e:
                raise GCError("GC state damaged; retaining all helpers") from e
            yield data
        finally:
            os.close(fd)

    def save(self, data):
        fd, name = tempfile.mkstemp(prefix=".state-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.directory / "state.json")
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def event(self, payload, caller, table, now):
        event = payload.get("hook_event_name")
        if event not in {
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "PostToolUseFailure",
            "Stop",
            "SessionEnd",
            "SubagentStart",
            "SubagentStop",
        }:
            return
        session = payload.get("session_id")
        agent = owner(caller, table)
        if (
            not isinstance(session, str)
            or not session
            or not agent
            or table[agent]["uid"] != os.getuid()
        ):
            return  # Never attribute by cwd or accept a PID supplied in JSON.
        key = hashlib.sha256((agent + ":" + session).encode()).hexdigest()
        cwd = payload.get("cwd", "")
        cwd = (
            str(Path(cwd).resolve())
            if isinstance(cwd, str) and Path(cwd).is_absolute()
            else ""
        )
        with self.locked() as data:
            data["sessions"] = {
                k: s
                for k, s in data["sessions"].items()
                if s["owner"] in table and table[s["owner"]]["start"] == s["start"]
            }
            old = data["sessions"].get(key, {})
            children = (
                set(old.get("children", []))
                if old.get("start") == table[agent]["start"]
                else set()
            )
            if event == "SubagentStart":
                children.add(str(payload.get("agent_id") or "unknown"))
            elif event == "SubagentStop" and payload.get("agent_id"):
                children.discard(str(payload["agent_id"]))
            ended = event in {"Stop", "SessionEnd"} or (
                event == "SubagentStop" and old.get("ended") is True
            )
            phase = "idle" if ended and not children else "active"
            tasks = payload.get("background_tasks", [])
            if not isinstance(tasks, list) or any(
                not isinstance(t, dict) or not disposable_task(t) for t in tasks
            ):
                phase = "active"
            if payload.get("agent_id") and event == "Stop":
                phase = "active"  # A child's Stop is not the parent's completion.
            data["sessions"][key] = dict(
                owner=agent,
                start=table[agent]["start"],
                phase=phase,
                children=sorted(children),
                at=now,
                ended=ended,
                cwd=cwd,
            )
            if len(data["sessions"]) > 512:
                raise GCError("too many live sessions; retaining helpers")
            # Every lifecycle event invalidates old authorizations, even if the
            # timestamp is identical or a second Stop arrives in the same second.
            data["candidates"] = {
                k: c for k, c in data["candidates"].items() if c.get("owner") != agent
            }
            self.save(data)

    def eligible(self, pid, table, net, sim_clear, sessions, now):
        row = table[pid]
        if row["uid"] != os.getuid() or not net["known"]:
            return None
        cmd = row["command"]
        abandoned = bool(SIM.match(cmd) and row["ppid"] == 1 and sim_clear is True)
        kind = "browser" if browser(cmd) else "dev-server" if dev_server(cmd) else ""
        if not abandoned and not kind:
            return None
        members = descendants(pid, table)
        if any(
            table[p]["uid"] != os.getuid() or AGENT.match(table[p]["command"])
            for p in members
        ):
            return None
        if members & set(net["connected"]):
            return None
        if abandoned:
            # Only abandoned runtime leaves, not launchd_sim, simulator services
            # or any process that still owns a child.
            if members == {pid}:
                return dict(
                    kind="abandoned-simulator",
                    owner="",
                    members=members,
                    observed=members,
                )
            return None
        if not kind or (kind == "dev-server" and not members & set(net["listeners"])):
            return None
        agent = owner(str(row["ppid"]), table)
        if not agent:
            return None  # Existing orphan policy owns dead-agent helpers.
        owned = [
            s
            for s in sessions.values()
            if s["owner"] == agent and s["start"] == table[agent]["start"]
        ]
        if not owned or any(
            s["phase"] != "idle" or s["children"] or not 0 <= now - s["at"] <= 7 * 86400
            for s in owned
        ):
            return None
        if kind == "dev-server":
            projects = [s.get("cwd", "") for s in owned]
            if not all(projects):
                return None
            for s in sessions.values():
                other = s.get("cwd", "")
                if (
                    s["owner"] in table
                    and s["start"] == table[s["owner"]]["start"]
                    and s["phase"] != "idle"
                    and other
                    and any(
                        other == p
                        or other.startswith(p + "/")
                        or p.startswith(other + "/")
                        for p in projects
                    )
                ):
                    return None
        observed = descendants(agent, table)
        # Unknown descendants are work, even while blocked on I/O or a queue.
        # Only recognized disposable tools and their transport may remain.
        for p in observed - {agent}:
            c = table[p]["command"]
            exe = executable(c)
            if p in members:
                if (
                    p != pid
                    and not (browser_helper(c) and kind == "browser")
                    and not (kind == "dev-server" and exe == "esbuild")
                ):
                    return None
                continue
            if (
                browser(c)
                or dev_server(c)
                or exe in {"gopls", "sourcekit-lsp", "esbuild"}
            ):
                continue
            if browser_helper(c) and any(
                browser(table[a]["command"]) and p in descendants(a, table)
                for a in observed
            ):
                continue
            if exe in {"node", "npm", "npx"} and MCP.search(c):
                continue
            if exe == "node" and re.search(r"/tsx(?:\s|$)", c):
                continue
            if exe == "npm" and re.search(r"\bexec tsx server/index\.[jt]s", c):
                continue
            if resource_transport(p, pid, table, self.resources):
                continue
            return None
        return dict(kind=kind, owner=agent, members=members, observed=observed)

    @staticmethod
    def signature(ids, table):
        return {
            p: [table[p]["start"], table[p]["command"], table[p]["cpu"]] for p in ids
        }

    @staticmethod
    def quiet(before, after):
        return (
            before.keys() == after.keys()
            and all(
                before[p][:2] == after[p][:2] and 0 <= after[p][2] - before[p][2] <= 2
                for p in before
            )
            and sum(after[p][2] - before[p][2] for p in before) <= 2
        )

    def scan(self, table, net, sim_clear, now):
        self.load_resources()
        with self.locked() as data:
            if self.activity_pending():
                # Hold action while a hook is in flight. Its successful event
                # invalidates that owner's clocks; do not reset unrelated idle
                # projects on every tool call in a busy session.
                return []
            candidates, ready = {}, []
            for pid in table:
                match = self.eligible(pid, table, net, sim_clear, data["sessions"], now)
                if not match:
                    continue
                sig = self.signature(match.pop("observed"), table)
                old = data["candidates"].get(pid, {})
                same = (
                    old.get("owner") == match["owner"]
                    and old.get("kind") == match["kind"]
                    and self.quiet(old.get("signature", {}), sig)
                    and 0 <= now - old.get("last", now) <= self.grace + 60
                )
                first = old["first"] if same else now
                match["members"] = sorted(match["members"])
                c = dict(
                    pid=pid,
                    **match,
                    signature=old["signature"] if same else sig,
                    first=first,
                    last=now,
                )
                candidates[pid] = c
                if now - first >= self.grace:
                    ready.append(c)
            data["candidates"] = candidates
            data["sessions"] = {
                k: s
                for k, s in data["sessions"].items()
                if s["owner"] in table and table[s["owner"]]["start"] == s["start"]
            }
            self.save(data)
            return ready

    def authorize(self, pid, table, net, sim_clear, now):
        self.load_resources()
        with self.locked() as data:
            if self.activity_pending():
                return False
            for root, c in data["candidates"].items():
                if (
                    pid not in c["members"]
                    or root not in table
                    or not 0 <= now - c["last"] <= 30
                    or now - c["first"] < self.grace
                ):
                    continue
                match = self.eligible(
                    root, table, net, sim_clear, data["sessions"], now
                )
                if match and self.quiet(
                    c["signature"], self.signature(match["observed"], table)
                ):
                    return True
        return False

    def activity_pending(self):
        path = self.directory.parent / "gc-activity-pending"
        return path.exists() and any(path.iterdir())

    def load_resources(self):
        # Read an atomic registry without changing its leases or reservations.
        path = self.directory.parent / "queue/jobs.json"
        try:
            data = json.loads(path.read_text())
            if not isinstance(data.get("jobs"), list) or not all(
                isinstance(j, dict) for j in data["jobs"]
            ):
                raise ValueError("invalid registry")
            self.resources = data["jobs"]
        except FileNotFoundError:
            self.resources = []
        except (ValueError, AttributeError) as e:
            raise GCError("queue registry unavailable; retaining helpers") from e

    def continuation(self, caller, table, session=""):
        self.load_resources()
        agent = owner(caller, table)
        if not agent:
            return {}
        session_key = hashlib.sha256(session.encode()).hexdigest() if session else ""
        pending = [
            j
            for j in self.resources
            if not j.get("resource")
            and (not j.get("session_key") or j["session_key"] == session_key)
            and not j.get("cancel")
            and j.get("status") in {"waiting", "running"}
            and str(j.get("owner")) in table
            and table[str(j["owner"])]["start"] == j.get("owner_start")
            and owner(str(j["owner"]), table) == agent
        ]
        if not pending:
            return {}
        ids = ", ".join(str(j.get("id", ""))[:8] for j in pending)
        return {
            "decision": "block",
            "reason": f"memcap still owns pending work for this session: {ids}. "
            "Use TaskOutput block=true timeout=60000, or a tool-session blocking poll of up to 60000ms. "
            f"If those tools are unavailable, run memcap wait {pending[0]['id'][:8]} --timeout 60; this creates no new job or reservation. Never invent a drain tick or Bash sleep loop. "
            "While pending, poll once per minute; do not repeatedly read output files, emit holding messages, or attempt to stop. "
            "The local scheduler keeps checking capacity without model calls. Read its final output "
            "and exit status before finishing. Queue waiting is not a task failure. "
            "Do not resubmit duplicate jobs or disable/change memcap to get around the queue. "
            "Respect an explicit user cancellation.",
        }


COMPLETED_GUIDANCE = {
    "decision": "block",
    "reason": "memcap's pending work finished or was canceled while this hook waited. "
    "Read the existing task's final output and exit status before finishing or "
    "starting dependent work. Respect explicit cancellation; do not resubmit it.",
}


def wait_for_pending(
    gc,
    caller,
    session,
    duration=60,
    clock=time.monotonic,
    sleep=time.sleep,
    table_reader=process_table,
):
    """Wait in the hook process, with no model calls or lifecycle lock held."""
    deadline = clock() + duration
    response = gc.continuation(caller, table_reader(), session)
    if not response:
        return {}
    while clock() < deadline:
        sleep(min(2, deadline - clock()))
        updated = gc.continuation(caller, table_reader(), session)
        if not updated:
            return dict(COMPLETED_GUIDANCE)
        response = updated
    return response


def main():
    root = (
        Path(os.environ.get("MEMCAP_STATE_HOME", str(Path.home() / ".local/state")))
        / "memcap"
    )
    gc = Collector(root, int(os.environ.get("GC_IDLE_SEC", "600")))
    action = sys.argv[1]
    if action == "event":
        payload = json.load(sys.stdin)
        table = process_table()
        gc.event(payload, str(os.getpid()), table, time.time())
        if payload.get("hook_event_name") == "Stop":
            response = gc.continuation(
                str(os.getpid()), table, payload.get("session_id", "")
            )
            if response:
                print(json.dumps(response))
        return 0
    if action == "wait":
        payload = json.load(sys.stdin)
        if payload.get("hook_event_name") == "Stop":
            # The shell invokes wait only after event observed pending work.
            # It may have completed between interpreters; still request its result.
            response = wait_for_pending(
                gc, str(os.getpid()), payload.get("session_id", "")
            ) or dict(COMPLETED_GUIDANCE)
            if response:
                print(json.dumps(response))
        return 0
    if action == "status":
        with gc.locked() as data:
            print(
                f"Tracking {len(data['sessions'])} sessions and {len(data['candidates'])} idle helper groups."
            )
            if gc.activity_pending():
                print(
                    "Cleanup held: incomplete lifecycle hook marker in gc-activity-pending."
                )
            for pid, c in data["candidates"].items():
                print(
                    f"  pid={pid} {c['kind']} owner={c['owner'] or 'departed simulator'} idle={int(max(0, time.time() - c['first']))}s"
                )
        return 0
    table = process_table()
    net = network_state()
    clear = (
        simulators_clear(table)
        if any(SIM.match(r["command"]) for r in table.values())
        else False
    )
    if action == "scan":
        for c in gc.scan(table, net, clear, time.time()):
            print(c["pid"], c["kind"], ",".join(c["members"]))
        return 0
    if action == "authorize":
        allowed = [
            p
            for p in sys.argv[2:]
            if p.isdigit() and gc.authorize(p, table, net, clear, time.time())
        ]
        if allowed and not gc.activity_pending():
            print(" ".join(allowed))
            return 0
        return 1
    raise GCError("unknown garbage collector action")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        GCError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as e:
        print(f"memcap garbage collector: {e}", file=sys.stderr)
        sys.exit(1)
