"""Cancel only registered, positively identified waiting-only loops. Never signal."""

import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
import subprocess

from boot_timeout import table_now
from idle_gc import owner, descendants
from scheduler_policy import polling_loop


def supervisor_poll(command):
    prefix, separator, script = command.partition(" --shell-command ")
    if not separator:
        return False
    try:
        words = shlex.split(prefix)
    except ValueError:
        return False
    if len(words) < 3 or not re.fullmatch(r"python3(?:\.[0-9]+)?", Path(words[0]).name):
        return False
    installed = re.fullmatch(
        r"/(?:opt/homebrew|usr/local)/Cellar/memcap/[0-9]+\.[0-9]+\.[0-9]+/libexec/scheduler\.py",
        words[1],
    )
    if not installed and words[1] != str(Path(__file__).with_name("scheduler.py")):
        return False
    if words[2] != "run":
        return False
    i = 3
    while i < len(words):
        if words[i] in ("--wait-forever", "--login"):
            i += 1
        elif words[i] in (
            "--shell",
            "--cwd",
            "--session-key",
            "--wait",
        ) and i + 1 < len(words):
            if words[i] == "--shell" and words[i + 1] not in (
                "/bin/bash",
                "/bin/zsh",
                "/bin/sh",
            ):
                return False
            i += 2
        else:
            return False
    # macOS ps renders embedded newlines as backslash-012.
    return polling_loop(script.replace("\\012", "\n"))


def plan(job, table, now):
    try:
        if job["resource"]:
            return None
        supervisor = table.get(str(job["owner"]))
        live = supervisor and supervisor["start"] == job["owner_start"]
        if job["status"] == "waiting":
            agent = owner(str(job["owner"]), table)
            if (
                not live
                or supervisor["uid"] != os.getuid()
                or not agent
                or table[agent]["uid"] != os.getuid()
                or supervisor["age"] < 60
                or job["group"]
                or job["members"]
                or not supervisor_poll(supervisor["command"])
                or descendants(str(job["owner"]), table) != {str(job["owner"])}
            ):
                return None
            members = {str(job["owner"]): supervisor}
        elif job["status"] == "running":
            root = str(job["group"])
            leader = table.get(root)
            if (
                live
                or not leader
                or leader["ppid"] != 1
                or leader["age"] < 300
                or job["members"].get(root) != leader["start"]
            ):
                return None
            match = re.fullmatch(
                r"/bin/(?:bash|zsh|sh) -c ([\s\S]+)", leader["command"]
            )
            if not match or not polling_loop(match[1].replace("\\012", "\n")):
                return None
            members = {p: r for p, r in table.items() if r["group"] == job["group"]}
            if (
                not members
                or len(members) > 8
                or descendants(root, table) != set(members)
            ):
                return None
            for p, row in members.items():
                if row["uid"] != os.getuid():
                    return None
                if p != root and (
                    row["ppid"] != int(root)
                    or not re.fullmatch(r"(?:/bin/)?sleep [1-9][0-9]?", row["command"])
                ):
                    return None
        else:
            return None
        return dict(
            job_id=job["id"],
            owner=job["owner"],
            owner_start=job["owner_start"],
            group=job["group"],
            status=job["status"],
            cwd=job["cwd"],
            at=now,
            members={
                p: dict(start=r["start"], command=r["command"], group=r["group"])
                for p, r in members.items()
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
            job[k] != proposal[k] for k in ("owner", "owner_start", "group", "status")
        ):
            return []
        if not escalating:
            fresh = plan(job, table, now)
            return (
                list(fresh["members"])
                if fresh and fresh["members"] == proposal["members"]
                else []
            )
        survivors = {p: r for p, r in table.items() if p in proposal["members"]}
        for p, r in survivors.items():
            if (
                r["uid"] != os.getuid()
                or any(
                    r[k] != proposal["members"][p][k]
                    for k in ("start", "command", "group")
                )
                or not descendants(p, table) <= set(survivors)
            ):
                return []
        if job["status"] == "running" and any(
            r["group"] == job["group"] and p not in survivors for p, r in table.items()
        ):
            return []
        return list(survivors)
    except (KeyError, TypeError, ValueError):
        return []


def main():
    state = (
        Path(os.environ.get("MEMCAP_STATE_HOME", str(Path.home() / ".local/state")))
        / "memcap"
    )
    if (state / "paused").exists() or not (state / "queue/jobs.json").exists():
        return
    jobs = json.loads((state / "queue/jobs.json").read_text())["jobs"]
    table = table_now()
    if sys.argv[1] == "scan":
        for job in jobs:
            proposal = plan(job, table, time.time())
            if proposal:
                print(json.dumps(proposal, separators=(",", ":")))
    elif sys.argv[1] == "authorize":
        print(
            " ".join(
                authorize(
                    json.load(sys.stdin),
                    jobs,
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
    ) as error:
        print(f"memcap polling cleanup: {error}; retaining job", file=sys.stderr)
        sys.exit(1)
