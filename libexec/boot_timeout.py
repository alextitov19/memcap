"""Plan narrow timeouts for registered standalone simulator boots; never signal.

A live, owned queue supervisor and an unchanged, fully registered group are
required. Builds, simulator services, unknown children and other sessions veto.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from idle_gc import owner, descendants

LIMIT = 180
BOOT = re.compile(
    r"^(?:/Library/Developer/PrivateFrameworks/CoreSimulator\.framework/Versions/(?:A|Current)/Resources/bin/simctl|"
    r"/Applications/Xcode[^/]*\.app/Contents/Developer/usr/bin/simctl|"
    r"/usr/bin/xcrun simctl) boot [0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}$"
)
SHELL = re.compile(r"^/bin/(?:ba|z|)sh -(?:l?c) ")
HELPER = re.compile(
    r"^(?:(?:/usr/bin/)?head -(?:[0-9]+|n [0-9]+)|(?:/bin/)?sleep [0-9]+(?:\.[0-9]+)?)$"
)


def table_now():
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,uid=,lstart=,etime=,stat=,command="],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode or not result.stdout.strip():
        raise ValueError("process table unavailable")
    table = {}
    for line in result.stdout.splitlines():
        p = line.split(None, 11)
        if len(p) != 12:
            raise ValueError("incomplete process table")
        if "Z" in p[10]:
            continue
        elapsed = p[9].split("-")
        parts = [int(n) for n in elapsed[-1].split(":")]
        age = sum(n * 60**i for i, n in enumerate(reversed(parts)))
        if len(elapsed) == 2:
            age += int(elapsed[0]) * 86400
        table[p[0]] = dict(
            ppid=int(p[1]),
            group=int(p[2]),
            uid=int(p[3]),
            start=" ".join(p[4:9]),
            age=age,
            command=p[11],
        )
    return table


def bound(job, table):
    supervisor = table.get(str(job["owner"]))
    agent = owner(str(job["owner"]), table)
    if (
        job["status"] != "running"
        or job["resource"]
        or not supervisor
        or supervisor["uid"] != os.getuid()
        or supervisor["start"] != job["owner_start"]
        or not agent
        or table[agent]["uid"] != os.getuid()
    ):
        return None
    group = {p: r for p, r in table.items() if r["group"] == job["group"]}
    if not group or len(group) > 16:
        return None
    for p, r in group.items():
        if r["uid"] != os.getuid() or job["members"].get(p) != r["start"]:
            return None
    return group


def plan(job, table, now):
    try:
        group = bound(job, table)
        root = str(job["group"])
        if not group or root not in group or group[root]["ppid"] != job["owner"]:
            return None
        if descendants(root, table) != set(group):
            return None
        boots = [
            p
            for p, r in group.items()
            if BOOT.fullmatch(r["command"]) and r["age"] >= LIMIT
        ]
        if len(boots) != 1:
            return None
        for p, r in group.items():
            if p == boots[0]:
                continue
            if not (SHELL.match(r["command"]) or HELPER.fullmatch(r["command"])):
                return None
        return dict(
            job_id=job["id"],
            owner=job["owner"],
            owner_start=job["owner_start"],
            group=job["group"],
            boot=boots[0],
            at=now,
            cwd=job["cwd"],
            members={
                p: dict(start=r["start"], command=r["command"])
                for p, r in group.items()
            },
        )
    except (KeyError, TypeError, ValueError):
        return None


def authorize(proposal, jobs, table, now, escalating):
    try:
        if not 0 <= now - proposal["at"] <= 30:
            return []
        job = next((j for j in jobs if j["id"] == proposal["job_id"]), None)
        if not job or any(
            job[k] != proposal[k] for k in ("owner", "owner_start", "group")
        ):
            return []
        group = bound(job, table)
        if not group or not set(group) <= set(proposal["members"]):
            return []
        for p, r in group.items():
            if any(r[k] != proposal["members"][p][k] for k in ("start", "command")):
                return []
            if not descendants(p, table) <= set(group):
                return []
        if not escalating:
            fresh = plan(job, table, now)
            if (
                not fresh
                or fresh["members"] != proposal["members"]
                or fresh["boot"] != proposal["boot"]
            ):
                return []
        return list(group)
    except (KeyError, TypeError, ValueError):
        return []


def main():
    state = (
        Path(os.environ.get("MEMCAP_STATE_HOME", str(Path.home() / ".local/state")))
        / "memcap"
    )
    if (state / "paused").exists():
        return
    registry = state / "queue/jobs.json"
    if not registry.exists():
        return
    data = json.loads(registry.read_text())
    table = table_now()
    if sys.argv[1] == "scan":
        for job in data["jobs"]:
            proposal = plan(job, table, time.time())
            if proposal:
                print(json.dumps(proposal, separators=(",", ":")))
    elif sys.argv[1] == "authorize":
        proposal = json.load(sys.stdin)
        print(
            " ".join(
                authorize(
                    proposal,
                    data["jobs"],
                    table,
                    time.time(),
                    sys.argv[2] == "escalate",
                )
            )
        )
    else:
        raise ValueError("unknown action")


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"memcap boot timeout: {exc}; retaining job", file=sys.stderr)
        sys.exit(1)
