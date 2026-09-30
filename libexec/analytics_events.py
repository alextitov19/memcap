"""Private, best-effort analytics. This module has no enforcement authority.

The same allowlist is applied before transport and before persistence. Unavailable
telemetry is dropped; producers never create state, lock, retry, or wait for ACKs.
"""
import functools
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import time
import uuid

EVENTS = frozenset(("hook", "route", "queued", "admitted", "completed", "cancelled",
                    "stalled", "reservation", "sample", "stop_wait", "action",
                    "api", "native_tool", "native_hooks", "work", "observer", "experiment", "claim", "feedback"))
ENUMS = {
    "source": {"hook", "scheduler", "collector", "claude", "codex", "legacy", "owner", "enforcement", "benchmark"},
    "agent": {"claude", "codex", "unknown"},
    "phase": {"PreToolUse", "PostToolUse", "PostToolUseFailure", "SessionStart", "SessionEnd",
              "UserPromptSubmit", "Stop", "StopFailure", "SubagentStart", "SubagentStop", "begin", "end"},
    "route": {"native", "guarded", "managed", "paused", "denied", "unknown"},
    "family": {"read", "search", "ssm", "remote", "test", "build", "browser", "server", "wait", "unknown"},
    "outcome": {"accepted", "rejected", "abandoned", "cancelled", "failed", "unknown", "started", "completed", "timeout"},
    "scope": {"full", "sims", "oversized", "scheduled", "idle-gc", "boot-timeout", "poll-cleanup", "orphan-recovery"},
    "query_source": {"main", "subagent", "auxiliary", "unknown"},
    "tool": {"Bash", "exec_command", "shell_command", "Read", "Grep", "Glob", "Write", "Edit", "Agent", "Task", "TaskOutput", "unknown"},
    "cache_state": {"warm", "cold", "unknown"},
}
IDS = {"session", "parent", "turn", "operation", "job", "project", "work", "action", "model", "agent_version", "workload", "delivery", "resource"}
NUMBERS = {
    "duration_ms", "hook_ms", "guard_ms", "queue_wait_ms", "runtime_ms", "exit_code", "signal",
    "completion_kind", "pressure", "available_kb", "tracked_kb", "request_kb", "peak_kb",
    "workers", "running", "waiting", "swap_in_kbps", "swap_out_kbps", "compressor_kb",
    "sample_age_ms", "sample_duration_ms", "measurement_fault", "measurement_complete",
    "learning_complete", "reservation_kb", "measured_kb", "reservation_source", "classification_code",
    "estimate_source", "estimate_complete_runs", "reason_code", "runner_version", "paused",
    "feedback_bytes", "blocked", "success", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_creation_tokens", "api_cost_usd", "producer_dropped", "disk_free_kb", "observer_cpu_ms",
    "observer_peak_kb", "wake_delay_ms", "interval_ms", "accepted", "legacy", "clock_uncertain",
    "delivery_gaps", "evicted_events", "database_errors", "count", "signal_result", "target_count",
    "budget_gb", "max_jobs", "headroom_gb", "worker_cap", "adaptive", "max_pressure", "explicit_memory", "source_wall", "agent_hooks_ms", "persistent",
} | {"blocked_" + x + "_ms" for x in ("unknown", "budget", "headroom", "slots", "pressure_or_measurement", "measurement", "fairness", "startup", "stabilizing", "paging", "sampling")}
HEX = re.compile(r"^[a-f0-9]{16,64}$")
PACKET_LIMIT = 8192


def root():
    return Path(os.environ.get("MEMCAP_STATE_HOME", str(Path.home() / ".local/state"))) / "memcap/analytics"


def opaque(key, value):
    return hmac.new(key, str(value).encode(), hashlib.sha256).hexdigest()[:32]


def numeric(value):
    return type(value) in (float, int) and math.isfinite(value) and 0 <= value <= 2**63 - 1


def sanitize(row):
    if not isinstance(row, dict) or row.get("event") not in EVENTS or row.get("schema") != 1:
        return None
    clean = {"event": row["event"], "schema": 1}
    for name in ("producer", "boot", "build", "policy"):
        value = row.get(name)
        if not isinstance(value, str) or not HEX.fullmatch(value):
            return None
        clean[name] = value
    for name in ("seq", "wall", "mono"):
        if not numeric(row.get(name)):
            return None
        clean[name] = row[name]
    if type(clean["seq"]) is not int:
        return None
    for name, value in row.items():
        if name in NUMBERS and numeric(value):
            clean[name] = value
        elif name in ENUMS and isinstance(value, str) and value in ENUMS[name]:
            clean[name] = value
        elif name in IDS and isinstance(value, str) and HEX.fullmatch(value):
            clean[name] = value
    return clean


def make_event(event, fields, key, producer, seq, *, build, policy, boot, wall=None, mono=None):
    row = {k: opaque(key, v) if k in IDS else v for k, v in fields.items()
           if k not in IDS or v not in (None, "")}
    row.update(schema=1, event=event, producer=producer, seq=seq, build=build, policy=policy,
               boot=boot, wall=time.time() if wall is None else wall,
               mono=time.monotonic() if mono is None else mono)
    return sanitize(row)


@functools.lru_cache(maxsize=1)
def build_digest():
    digest = hashlib.sha256()
    directory = Path(__file__).parent
    for file in sorted(list(directory.glob("*.py")) + list(directory.glob("*.sh"))):
        digest.update(file.name.encode())
        digest.update(file.read_bytes())
    dispatcher = directory.parent / "bin/memcap-real"
    if not dispatcher.is_file():
        dispatcher = directory.parent / "bin/memcap"
    digest.update(dispatcher.read_bytes())
    return digest.hexdigest()


def policy_fields():
    result = {}
    for env, field in (("TOTAL_BUDGET_GB", "budget_gb"), ("QUEUE_MAX_JOBS", "max_jobs"),
                       ("QUEUE_HEADROOM_GB", "headroom_gb"), ("QUEUE_WORKERS", "worker_cap")):
        try:
            value = float(os.environ[env])
            if numeric(value):
                result[field] = value
        except (KeyError, ValueError):
            pass
    if os.environ.get("QUEUE_POLICY") in {"adaptive", "strict"}:
        result["adaptive"] = int(os.environ["QUEUE_POLICY"] == "adaptive")
    if os.environ.get("QUEUE_MAX_PRESSURE") in {"green", "yellow"}:
        result["max_pressure"] = {"green": 1, "yellow": 2}[os.environ["QUEUE_MAX_PRESSURE"]]
    return result


class Producer:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else root()
        self.seq = self.dropped = 0
        self.producer = uuid.uuid4().hex
        self.key = None
        self.socket = None
        self.policy_fields = policy_fields()
        self.policy = hashlib.sha256(json.dumps(self.policy_fields, sort_keys=True).encode()).hexdigest()
        self.build = "0" * 64
        self.boot = "0" * 32
        try:
            info = self.directory.stat()
            if self.directory.is_symlink() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                return
            if not (self.directory / "enabled").is_file():
                return
            fd = os.open(self.directory / "key", os.O_RDONLY | os.O_NOFOLLOW)
            try:
                if os.fstat(fd).st_uid != os.getuid():
                    return
                self.key = os.read(fd, 33)
            finally:
                os.close(fd)
            if len(self.key) != 32:
                self.key = None
                return
            self.build = build_digest()
            # Recorder supplies boot identity once; never sysctl per tool.
            boot = (self.directory / "boot").read_text().strip() if (self.directory / "boot").is_file() else "0" * 32
            self.boot = boot if HEX.fullmatch(boot) else "0" * 32
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.socket.setblocking(False)
        except (OSError, ValueError):
            self.key = None

    def emit(self, event, **fields):
        if not self.key or not self.socket:
            return False
        self.seq += 1
        try:
            fields = {**self.policy_fields, **fields, "producer_dropped": self.dropped,
                      "paused": int((self.directory.parent / "paused").is_file())}
            row = make_event(event, fields, self.key, self.producer, self.seq,
                             build=self.build, policy=self.policy, boot=self.boot)
            if row is None:
                return False
            packet = json.dumps(row, separators=(",", ":")).encode()
            if len(packet) > PACKET_LIMIT:
                self.dropped += 1
                return False
            self.socket.sendto(packet, str(self.directory / "events.sock"))
            return True
        except (OSError, ValueError, TypeError):
            self.dropped += 1
            return False

    def close(self):
        if self.socket:
            self.socket.close()


@functools.lru_cache(maxsize=1)
def producer():
    return Producer()


def emit(event, **fields):
    try:
        return producer().emit(event, **fields)
    except Exception:
        # Telemetry is never allowed to change command results or enforcement.
        return False


def family(command):
    """A descriptive tag, NEVER an admission decision or ground-truth label."""
    import shlex
    try:
        words = shlex.split(command)
    except ValueError:
        return "unknown"
    if not words:
        return "unknown"
    name = Path(words[0]).name
    if name == "aws" and "ssm" in words and any(x in words for x in ("get-parameter", "get-parameters", "get-parameters-by-path", "describe-parameters", "get-parameter-history")):
        return "ssm"
    if name in {"cat", "head", "tail", "sed", "ls", "stat", "wc"}:
        return "read"
    if name in {"rg", "grep", "find"}:
        return "search"
    if name in {"gh", "aws", "curl", "git"}:
        return "remote"
    if name == "memcap" and words[1:2] == ["wait"]:
        return "wait"
    if name in {"pytest", "bats"} or "test" in words:
        return "test"
    if name in {"make", "xcodebuild"} or "build" in words:
        return "build"
    return "unknown"


def hook_fields(payload):
    from session_identity import key
    if not isinstance(payload, dict):
        return {}
    original = payload.get("tool_input")
    original = original if isinstance(original, dict) else {}
    command = original.get("command", original.get("cmd", ""))
    result = dict(session=key(payload), turn=payload.get("prompt_id"),
                  operation=payload.get("tool_use_id"), project=payload.get("cwd"),
                  phase=payload.get("hook_event_name"), tool=payload.get("tool_name", "unknown"),
                  source="hook", family=family(command) if isinstance(command, str) else "unknown")
    if payload.get("agent_id") and payload.get("session_id"):
        result["parent"] = hashlib.sha256(str(payload["session_id"]).encode()).hexdigest()
    if numeric(payload.get("duration_ms")):
        result["duration_ms"] = payload["duration_ms"]
    return {k: v for k, v in result.items() if v not in (None, "")}


def hook(payload, **extra):
    try:
        emit("hook", **{**hook_fields(payload), **extra})
    except Exception:
        pass
