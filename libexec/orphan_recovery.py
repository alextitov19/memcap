"""Watchdog observation leases and narrow orphan plans. Never sends signals.

Observation is not cancellation ownership: only an unchanged registered group
can be measured as recovered. Cleanup separately proves every process disposable.
"""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

from boot_timeout import table_now
from idle_gc import (
    AGENT,
    Collector,
    GCError,
    descendants,
    dev_server,
    network_state,
    owner,
)

GRACE = 120
LEASE = 90


def agent_identity():
    try:
        table = table_now()
        agent = owner(str(os.getpid()), table)
        if agent and table[agent]["uid"] == os.getuid():
            return dict(agent_owner=int(agent), agent_start=table[agent]["start"])
    except (OSError, ValueError, GCError, subprocess.SubprocessError):
        pass
    return {}


def bound(job, table):
    """No new identities, foreign users or escaped descendants may be adopted."""
    supervisor = table.get(str(job.get("owner")))
    supervisor_alive = supervisor and supervisor.get("start") == job.get("owner_start")
    agent = table.get(str(job.get("agent_owner")))
    agent_gone = bool(
        job.get("agent_owner")
        and job.get("agent_start")
        and (not agent or agent.get("start") != job["agent_start"])
    )
    if job.get("status") != "running" or (supervisor_alive and not agent_gone):
        return None
    group = {
        p: r
        for p, r in table.items()
        if job.get("group") and r.get("group") == job["group"]
    }
    if not group or len(group) > 128:
        return None
    for p, row in group.items():
        if row.get("uid") != os.getuid() or job.get("members", {}).get(p) != row.get(
            "start"
        ):
            return None
        if not descendants(p, table) <= set(group):
            return None
    # Remembered descendants outside the group are still work, even reparented.
    for p, start in job.get("footprint_members", {}).items():
        if p in table and table[p].get("start") == start and p not in group:
            return None
    return group


def recovered(job, table, now):
    lease = job.get("recovery", {})
    group = bound(job, table)
    return bool(
        group
        and 0 <= now - lease.get("at", -LEASE) <= LEASE
        and lease.get("members") == {p: r["start"] for p, r in group.items()}
    )


def protected_claims(jobs, table):
    protected = set()
    for job in jobs:
        claims = job.get("claims", {})
        live = any(
            p in table and table[p]["start"] == start and table[p]["uid"] == os.getuid()
            for p, start in claims.items()
        )
        if not job.get("pinned") and not live:
            continue
        for p, start in job.get("members", {}).items():
            if (
                p in table
                and table[p]["start"] == start
                and table[p]["uid"] == os.getuid()
            ):
                protected |= descendants(p, table)
    return sorted(protected)


def helper(command):
    # Intentionally narrower than the idle collector: no arbitrary server scripts
    # or shell wrappers which could continue executing after a child is stopped.
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if not words:
        return False
    name = Path(words[0]).name
    if name == "uv" and words[1:2] == ["run"]:
        return helper(shlex.join(words[2:]))
    if name in {"python", "python3"}:
        return (
            len(words) >= 3
            and Path(words[1]).name == "manage.py"
            and words[2] == "runserver"
            and "--noreload" in words[3:]
            and all(w == "--noreload" or w.startswith("127.0.0.1:") for w in words[3:])
        )
    return (
        dev_server(command)
        and not any("server/index." in w for w in words)
        and not any(w in {"build", "test", "lint", "--watch"} for w in words[1:])
    )


def cleanup_reason(job, table, net, sessions, jobs=()):
    group = bound(job, table)
    if not group:
        return "unverified-group"
    if not job.get("agent_owner") or not job.get("agent_start"):
        return "origin-unverified"
    origin = table.get(str(job["agent_owner"]))
    if origin and origin["start"] == job["agent_start"]:
        return "live-origin"
    if any(
        other.get("id") != job["id"]
        and other.get("status") == "running"
        and any(
            other.get("members", {}).get(p) == row["start"] for p, row in group.items()
        )
        for other in jobs
    ):
        return "shared-job"
    if job.get("pinned"):
        return "pinned"
    for p, start in job.get("claims", {}).items():
        if p in table and table[p]["start"] == start and table[p]["uid"] == os.getuid():
            return "live-claim"
    if sessions is None:
        return "lifecycle-unavailable"
    # A live related session protects its server even when idle or out of tokens.
    cwd = Path(job["cwd"]).resolve()
    for s in sessions.values():
        row = table.get(s.get("owner"))
        if row and row["start"] == s.get("start"):
            if not s.get("cwd"):
                return "session-project-unknown"
            other = Path(s["cwd"]).resolve()
            if other == cwd or other in cwd.parents or cwd in other.parents:
                return "live-session"
    # An agent without usable hooks must not be interpreted as an absent client.
    known_agents = {(s.get("owner"), s.get("start")) for s in sessions.values()}
    if any(
        AGENT.match(r.get("command", ""))
        and r["uid"] == os.getuid()
        and (p, r["start"]) not in known_agents
        for p, r in table.items()
    ):
        return "lifecycle-unavailable"
    if any(
        owner(p, table) or not helper(r.get("command", "")) for p, r in group.items()
    ):
        return "unknown-work"
    if not net.get("known"):
        return "network-unavailable"
    if set(group) & set(net.get("connected", [])):
        return "active-clients"
    if not set(group) & set(net.get("listeners", [])):
        return "no-server-listener"
    return ""


def observe(job, table, net, sessions, now, jobs=()):
    group = bound(job, table)
    old = job.get("recovery", {})
    if not group:
        job.pop("recovery", None)
        job["cleanup_blocker"] = "unverified-group"
        return None
    members = {p: r["start"] for p, r in group.items()}
    commands = {p: r.get("command", "") for p, r in group.items()}
    continuous = (
        old.get("members") == members
        and old.get("commands") == commands
        and 0 <= now - old.get("at", -LEASE) <= LEASE
    )
    job["recovery"] = dict(
        at=now,
        since=old["since"] if continuous else now,
        members=members,
        commands=commands,
    )
    reason = cleanup_reason(job, table, net, sessions, jobs)
    if reason:
        # A reconnect or new claim restarts the disposal grace as well.
        job["recovery"]["clear_since"] = now
    else:
        job["recovery"]["clear_since"] = (
            old.get("clear_since", now) if continuous else now
        )
    since = max(job["recovery"]["since"], job["recovery"]["clear_since"])
    job["cleanup_blocker"] = reason or (
        "grace-period" if now - since < GRACE else "eligible"
    )
    if job["cleanup_blocker"] != "eligible":
        return None
    return dict(
        job_id=job["id"],
        owner=job["owner"],
        owner_start=job["owner_start"],
        group=job["group"],
        at=now,
        members={
            p: dict(start=r["start"], command=r["command"]) for p, r in group.items()
        },
    )


def authorize(
    proposal, job, table, net, sessions, now, escalating, paused=False, jobs=()
):
    if paused or not 0 <= now - proposal.get("at", -31) <= 30:
        return []
    if not job or any(
        job.get(k) != proposal.get(k) for k in ("owner", "owner_start", "group")
    ):
        return []
    group = bound(job, table)
    if not group or not recovered(job, table, now):
        # During escalation only original survivors may remain in the lease.
        lease = job.get("recovery", {})
        if (
            not escalating
            or not group
            or not 0 <= now - lease.get("at", -LEASE) <= LEASE
        ):
            return []
    if cleanup_reason(job, table, net, sessions, jobs):
        return []
    if not set(group) <= set(proposal["members"]):
        return []
    if not escalating and set(group) != set(proposal["members"]):
        return []
    if any(
        any(r[k] != proposal["members"][p][k] for k in ("start", "command"))
        for p, r in group.items()
    ):
        return []
    lease = job.get("recovery", {})
    if now - max(lease.get("since", now), lease.get("clear_since", now)) < GRACE:
        return []
    return sorted(group)


def sessions_now(state):
    if not (state / "idle-gc/state.json").is_file():
        return None
    try:
        with Collector(state).locked() as data:
            return data["sessions"]
    except (OSError, GCError):
        return None


def claim(scheduler, ident, pin=None, release=False):
    from scheduler import QueueError

    with scheduler.locked(timeout=2) as data:
        table = table_now()
        agent = owner(str(os.getpid()), table)
        matches = (
            [j for j in data["jobs"] if j["id"].startswith(ident)]
            if len(ident) >= 8
            else []
        )
        if len(matches) != 1:
            raise QueueError(
                "claim requires an unambiguous job ID of at least eight characters"
            )
        job = matches[0]
        if pin is not None:
            job["pinned"] = pin
        else:
            if not agent or table[agent]["uid"] != os.getuid():
                raise QueueError(
                    "session claims require a verified agent ancestor; use --pin for a human-owned server"
                )
            claims = job.setdefault("claims", {})
            if release:
                claims.pop(agent, None)
            else:
                claims[agent] = table[agent]["start"]
        # Never transfer supervisor/cancellation ownership or change memory.
        job.pop("recovery", None)
        job["cleanup_blocker"] = "claim-changed"
        scheduler.save(data)


def main():
    from scheduler import Scheduler, QueueError

    state = (
        Path(os.environ.get("MEMCAP_STATE_HOME", str(Path.home() / ".local/state")))
        / "memcap"
    )
    if (state / "paused").exists() or not (state / "queue/jobs.json").exists():
        return
    scheduler = Scheduler(state / "queue")
    if sys.argv[1] == "protected":
        with scheduler.locked(timeout=2) as data:
            if not any(j.get("pinned") or j.get("claims") for j in data["jobs"]):
                return
            print(" ".join(protected_claims(data["jobs"], table_now())))
        return
    net = network_state()
    net_at = time.monotonic()
    # Measurement and network probes must not hold the admission lock.
    with scheduler.locked(timeout=2) as data:
        table = table_now()
        # Session claims must be reread after waiting for the registry. Neither
        # collector events nor claim commands hold this lock during a wait.
        sessions = sessions_now(state)
        if time.monotonic() - net_at > 5:
            net = dict(known=False)
        now = time.time()
        if (state / "paused").exists():
            return
        if sys.argv[1] == "scan":
            scheduler.refresh(data, table)
            plans = [
                p
                for j in data["jobs"]
                if (p := observe(j, table, net, sessions, now, data["jobs"]))
            ]
            scheduler.save(data)
            for p in plans:
                print(json.dumps(p, separators=(",", ":")))
        elif sys.argv[1] == "authorize":
            proposal = json.load(sys.stdin)
            job = next((j for j in data["jobs"] if j["id"] == proposal["job_id"]), None)
            print(
                " ".join(
                    authorize(
                        proposal,
                        job,
                        table,
                        net,
                        sessions,
                        now,
                        sys.argv[2] == "escalate",
                        jobs=data["jobs"],
                    )
                )
            )
        else:
            raise QueueError("unknown recovery action")


if __name__ == "__main__":
    from scheduler import QueueError

    try:
        main()
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        GCError,
        QueueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"memcap orphan recovery: {exc}; retaining work", file=sys.stderr)
        sys.exit(1)
