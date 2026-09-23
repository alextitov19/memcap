"""Bounded, read-only context for agents. Output mentions are clues, not verdicts.

Never persists tool output, changes queue records, boots devices or signals PIDs.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from report import PERFORMANCE_GUIDANCE

ROOT = Path(__file__).resolve().parents[1]
SESSION_GUIDANCE = (
    "memcap manages shared memory across agent sessions. Keep polling existing queued "
    "tasks with TaskOutput block=true timeout=60000 or a blocking tool-session poll of up to 60000ms. "
    "If TaskOutput/tool polling is unavailable, run memcap wait JOB_ID --timeout 60 using the existing ID from memcap queue; do not create drain ticks or Bash sleep loops. "
    "Use memcap wait --session --timeout 60 to wait on this agent process's finite jobs without any job-ID lookup pipeline. Cancel an owned obsolete background task using native task cancellation; never cancel still-needed work or other sessions' tasks. "
    "Poll once per minute while pending; avoid repeated output-file reads and holding messages. Wait for final output and "
    "exit status; continue independent work while waiting. Do not submit duplicates "
    "or bypass/change memcap protection. Respect explicit cancellation. A simulator "
    "boot timeout is not proof of memory starvation or a failed test assertion. "
    "Check memcap status and device readiness before retrying; keep tests unverified "
    "until they execute. Never reset a device used by another session."
    " Docker's VM subtotal alone does not explain a queue delay; use the runner's "
    "admission reason. Do not use memcap off or killall Docker to unblock work. "
    "Inspect docker stats --no-stream and docker buildx ls, and establish ownership "
    "before cleanup; preserve other sessions' containers and deployments."
    " For suspected memcap defects, use memcap report CATEGORY once: queue-lock, measurement, "
    "integration, queue-stall, unexpected-termination, missing-task-poll, lightweight-queued, or polling-overhead. Reporting saves a "
    "sanitized local draft and publishes only after the user's one-time reporting opt-in. "
    "Reports include available machine, memory/load and queue age/blocker metrics; "
    "Never enable reporting on the user's behalf or upload raw logs. "
    + PERFORMANCE_GUIDANCE
)
BOOT = re.compile(
    r"timed out trying to boot simulator|failed to prepare device.{0,180}impending launch|"
    r"simulator.{0,100}(?:boot|launch).{0,80}(?:timed out|timeout)",
    re.I | re.S,
)
MEMORY = re.compile(
    r"\bENOMEM\b|out of memory|cannot allocate memory|memory starvation", re.I
)
QUEUE = re.compile(
    r"memcap: (?:queued |admitted [a-f0-9]+; command started|queue wait expired|queue lock unavailable|memory sampling failed|memory measurement unavailable|memory .*not admitted)",
    re.I,
)
FIELDS = {
    "stdout",
    "stderr",
    "output",
    "text",
    "content",
    "error",
    "message",
    "task",
    "result",
}


def excerpt(text):
    # Build tools put the useful error at the end of a long compile log.
    return text if len(text) <= 65536 else text[:32768] + "\n" + text[-32768:]


def output_text(value, depth=0):
    if depth > 5:
        return ""
    if isinstance(value, str):
        return excerpt(value)
    if isinstance(value, dict):
        return excerpt(
            "\n".join(
                output_text(v, depth + 1) for k, v in value.items() if k in FIELDS
            )
        )
    if isinstance(value, list):
        return excerpt("\n".join(output_text(v, depth + 1) for v in value[:16]))
    return ""


def probe(argv, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=remaining,
            env={**os.environ, "LC_ALL": "C"},
        )
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def read_json(path):
    with path.open() as stream:
        return json.loads(stream.read(1048577))


def measure(state, mobile):
    # Leave room in the five-second hook budget for existing lifecycle handling.
    # Share a deadline: a hung probe cannot multiply it by the number of probes.
    deadline = time.monotonic() + 1.5
    facts = []
    raw = probe([str(ROOT / "bin/memcap"), "_queue-sample"], deadline)
    try:
        cap, tracked, available, pressure, fault = map(int, raw.splitlines()[0].split())
        if (
            fault
            or pressure not in (1, 2, 4)
            or cap <= 0
            or min(tracked, available) < 0
        ):
            raise ValueError("unreliable sample")
        facts.append(
            f"pressure now: { {1: 'green', 2: 'yellow', 4: 'red'}[pressure] }; "
            f"tracked usage/cap: {tracked / 1048576:.2f}/{cap / 1048576:.2f} GB; "
            f"estimated available memory: {available / 1048576:.2f} GB "
            "(reservations and configured headroom still apply)"
        )
    except (AttributeError, IndexError, ValueError):
        facts.append(
            "memory measurement: unavailable or unreliable; do not assume healthy pressure"
        )
    try:
        registry = state / "queue/jobs.json"
        data = read_json(registry)
        table = probe(["ps", "-axo", "pid=,lstart="], deadline)
        if not table or not isinstance(data["jobs"], list):
            raise ValueError("identities unavailable")
        identities = {}
        for line in table.splitlines():
            parts = line.split()
            if len(parts) != 6 or not parts[0].isdigit():
                raise ValueError("invalid identity")
            identities[int(parts[0])] = " ".join(parts[1:])
        counts = {"waiting": 0, "running": 0}
        for job in data["jobs"]:
            if (
                job["status"] not in counts
                or not isinstance(job["owner"], int)
                or not isinstance(job["owner_start"], str)
            ):
                raise ValueError("invalid job")
            if identities.get(job["owner"]) == job["owner_start"]:
                counts[job["status"]] += 1
        age = max(0, int(time.time() - registry.stat().st_mtime))
        facts.append(
            f"queue: {counts['waiting']} waiting, {counts['running']} running with live supervisors; "
            f"registry age {age}s; excludes unmanaged work and unsupervised groups"
        )
    except (OSError, ValueError, KeyError, TypeError):
        facts.append(
            "queue: unavailable or no registry; this does not prove no work is running"
        )
    if mobile:
        raw = probe(["xcrun", "simctl", "list", "devices", "--json"], deadline)
        try:
            devices = json.loads(raw)["devices"]
            counts = {}
            known = {"Shutdown", "Booted", "Booting", "Shutting Down", "Creating"}
            for group in devices.values():
                for device in group:
                    status = device["state"]
                    if status not in known:
                        raise ValueError("unknown state")
                    counts[status] = counts.get(status, 0) + 1
            facts.append(
                "simulators now: "
                + (
                    ", ".join(f"{s}={n}" for s, n in sorted(counts.items()))
                    or "none listed"
                )
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            facts.append(
                "simulator states: unavailable; inspect device readiness explicitly"
            )
    return facts


def recent_terminations(state, cwd):
    """Repeat only numeric, project-scoped evidence; never echo argv or tool text."""
    if not isinstance(cwd, str) or not cwd:
        return []
    try:
        project = str(Path(cwd).resolve(strict=True))
        if project == "/":
            return []
        notices = []
        for path in (state / "job-feedback").glob("[0-9]*.json"):
            if not path.stem.isdigit():
                continue
            try:
                row = read_json(path)
                at = int(row["at"])
                if not 0 <= time.time() - at <= 86400:
                    continue
                if row["cwd"] != project and not row["cwd"].startswith(project + "/"):
                    continue
                pid, kb, limit = (
                    int(row["pid"]),
                    int(row["footprint_kb"]),
                    int(row["limit_gb"]),
                )
                notices.append(
                    (
                        at,
                        f"project termination request at {datetime.fromtimestamp(at, timezone.utc).isoformat()}: "
                        f"PID {pid}, footprint {kb / 1048576:.2f} GB, limit {limit} GB; "
                        "may belong to another session; not proof this command was terminated",
                    )
                )
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                OverflowError,
            ):
                continue
        return [line for _, line in sorted(notices, reverse=True)[:3]]
    except OSError:
        return []


def guidance(payload, state, refresh=False):
    if not isinstance(payload, dict):
        return ""
    event = payload.get("hook_event_name")
    if event in {"SessionStart", "UserPromptSubmit"} or (
        refresh and event == "PreToolUse"
    ):
        return SESSION_GUIDANCE
    if event not in {"PostToolUse", "PostToolUseFailure"}:
        return ""
    text = "\n".join(
        output_text(payload.get(k)) for k in ("tool_response", "tool_result", "error")
    )
    mobile = bool(BOOT.search(text))
    if not (mobile or MEMORY.search(text) or QUEUE.search(text)):
        return ""
    lines = [
        "memcap diagnostic context at "
        + datetime.now(timezone.utc).isoformat()
        + ". Tool output mentions a resource/preparation problem; this does not establish its cause. "
        "These observations are from NOW, not necessarily from the failure time."
    ]
    lines.extend(measure(state, mobile))
    lines.extend(recent_terminations(state, payload.get("cwd")))
    if QUEUE.search(text):
        # Do not infer workload weight from copied output or require an error:
        # the agent knows its task, including after a background command exits 0.
        lines.append(PERFORMANCE_GUIDANCE)
    category = None
    if "memcap: queue lock unavailable" in text.lower():
        category = "queue-lock"
    elif re.search(
        r"memcap: memory (?:sampling failed|measurement unavailable)", text, re.I
    ):
        category = "measurement"
    if category:
        lines.append(
            f"For this suspected memcap defect, run memcap report {category} once. "
            "It uses sanitized diagnostics and the user's existing reporting consent; "
            "otherwise it keeps a local draft. Do not enable reporting yourself, upload raw "
            "logs, or keep retrying a deferred report. Reporting does not fix or restart the task."
        )
    if mobile:
        lines.append(
            "A reported simulator boot timeout is not proof of memory starvation or a failed assertion. "
            "Inspect the result bundle to establish which tests actually executed; keep the remainder unverified. "
            "Check the target device with xcrun simctl list devices --json. Booting or Shutting Down is not ready. "
            "Check whether another session owns mobile work before recovery; do not reset/erase its device or shutdown all. "
            "Wait for the required device state to change before retrying through memcap. "
            "If it remains stuck, report an infrastructure blocker with evidence; do not edit app code based on this timeout."
        )
    if "queue wait expired" in text.lower():
        lines.append(
            "The output reports a queue deadline: confirm its final exit status; an exited waiter cannot resume. "
            "After checking memcap status/queue and confirming no existing live task, retry once through the normal "
            "memcap queue with background waiting. Do not loop on the same expired deadline."
        )
    else:
        lines.append(
            "For an existing live queued task, use TaskOutput block=true timeout=60000 or a tool-session blocking poll of up to 60000ms once per minute; if unavailable, use memcap wait JOB_ID --timeout 60. "
            "do not resubmit it. Read final output and exit status before dependent work. "
            "If the command already finished, inspect its result instead of polling a dead task."
        )
    lines.append(
        "Continue independent work. Do not bypass/change memcap protection, delete reservations, or blindly retry. "
        "Use memcap status and memcap diagnostics for further evidence; correlate timestamps before attributing a failure to memcap."
        " Docker's VM subtotal alone does not explain a queue delay; read the runner's admission reason. "
        "Inspect docker stats --no-stream, docker ps and docker buildx ls before attributing usage to a builder. "
        "Do not use memcap off or killall Docker; preserve other sessions' containers and deployments."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        state = (
            Path(os.environ.get("MEMCAP_STATE_HOME", str(Path.home() / ".local/state")))
            / "memcap"
        )
        print(
            guidance(
                json.load(sys.stdin),
                state,
                refresh=sys.argv[1:] == ["--session-guidance"],
            ),
            end="",
        )
    except (OSError, ValueError, TypeError):
        pass  # Optional context must not break normal hook permission semantics.
