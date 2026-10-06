"""Shared scheduler telemetry. KiB, monotonic intervals, fixed public vocabulary."""

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from functools import lru_cache
from job_timing import FIELDS as TIMING_FIELDS

BLOCKERS = ("unknown", "budget", "headroom", "slots", "pressure_or_measurement", "measurement", "fairness", "startup", "stabilizing", "paging", "sampling")
EVENTS = {"sample", "sampling", "queued", "admitted", "completed", "cancelled", "stalled", "reservation"}
FIELDS = {
    'sampling_path', 'sample_call_ms', 'sample_ready_age_ms', 'sampler_retry_ms',
    'sampler_ready_to_retry_ms',
    'sampling_decisions', 'sampling_handoff_count', 'sampling_handoff_ms',
    'observation_pending_seen', 'observation_pending_resolved',
    'compiler_scope_reason', 'exact_profile_used', 'observation_terminal_empty',
    'observation_anchor', 'observation_members', 'observation_usage', 'observation_identity',
    'observation_refresh', 'observation_gap', 'observation_fault',
    "compiler_complete_runs",
    "estimate_reuse_reason", "estimate_prior_kb", "compiler_profile_used",
    "learning_protocol", "observation_probe_ms", "sampling_busy_count",
    "sampling_expired_count", "sample_cache_mismatch_count", "sampling_reason",
    "learning_unverified_scope",
    "learning_samples", "learning_fault_samples", "learning_missing_samples", "learning_detached_samples",
    "capacity_stalled",
    "worker_control_version", "node_worker_limit",
    "lane_code",
    "runner_version",
    "job_ref",
    "signal",
    "completion_kind",
    "outstanding_kb",
    "headroom_kb",
    "headroom_deficit_kb",
    "measurement_fault",
    "measurement_busy",
    "reason_code",
    "queue_wait_ms",
    "runtime_ms",
    "pressure",
    "tracked_kb",
    "available_kb",
    "request_kb",
    "peak_kb",
    "running",
    "waiting",
    "workers",
    "exit_code",
    "swap_in_kbps",
    "swap_out_kbps",
    "policy",
    "reason",
    "sample_age_ms",
    "sample_duration_ms",
    "classification_code",
    "estimate_source",
    "estimate_complete_runs",
    "reservation_kb",
    "measured_kb",
    "reservation_source",
    "measurement_complete",
    "learning_complete",
} | {"blocked_" + reason + "_ms" for reason in BLOCKERS} | TIMING_FIELDS
MAX_SEGMENT = 16 * 1024 * 1024
ANALYTICS_CONTEXT = {}


def analytics_context(**fields):
    """Loaded runner metadata only; never used for admission or ownership."""
    ANALYTICS_CONTEXT.clear()
    ANALYTICS_CONTEXT.update(fields)


def account_blocker(job, reason, now):
    """Attribute elapsed observations to the previous decision, not root cause.

    Monotonic times are local to a live supervisor; this is never used to admit.
    """
    prior = job.get("blocker_clock")
    if isinstance(prior, list) and len(prior) == 2 and prior[0] in BLOCKERS and number(prior[1]) and now >= prior[1]:
        totals = job.get("blocked_ms", {})
        if not isinstance(totals, dict):
            totals = {}
        previous = totals.get(prior[0], 0)
        totals[prior[0]] = (previous if number(previous) else 0) + int((now - prior[1]) * 1000)
        job["blocked_ms"] = totals
    job["blocker_clock"] = [reason, now] if reason in BLOCKERS else None


def blocker_fields(job):
    totals = job.get("blocked_ms", {})
    if not isinstance(totals, dict):
        return {}
    return {"blocked_" + reason + "_ms": value for reason, value in totals.items() if reason in BLOCKERS and number(value)}


def completion_fields(result, cancelled=0):
    """Signal termination is distinct from an application returning the same number.

    A signal alone never establishes who sent it. Kind 3 means this supervisor
    received cancellation; it does not attribute other signals to enforcement.
    """
    return dict(
        exit_code=result if result >= 0 else 128 - result,
        signal=-result if result < 0 else 0,
        completion_kind=3 if cancelled else (2 if result < 0 else 1),
    )


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def rate(previous: dict, current: dict, counter: str):
    try:
        if (
            not previous["boot_id"]
            or previous["boot_id"] != current["boot_id"]
            or previous["page_bytes"] != current["page_bytes"]
        ):
            return None
        values = [
            previous["monotonic"],
            current["monotonic"],
            previous[counter],
            current[counter],
            current["page_bytes"],
        ]
        if not all(number(v) for v in values):
            return None
        elapsed = current["monotonic"] - previous["monotonic"]
        delta = current[counter] - previous[counter]
        if elapsed <= 0 or delta < 0 or current["page_bytes"] not in (4096, 16384):
            return None
        return delta * current["page_bytes"] / 1024 / elapsed
    except (KeyError, TypeError):
        return None


def parse_vm(output: str) -> dict:
    page = re.search(r"page size of (\d+) bytes", output)
    if not page or int(page[1]) not in (4096, 16384):
        return {}
    counters = {
        m[1]: int(m[2]) for m in re.finditer(r"^([^:\n]+):\s+(\d+)\.", output, re.M)
    }
    if not all(
        k in counters for k in ("Swapins", "Swapouts", "Pages occupied by compressor")
    ):
        return {}
    size = int(page[1])
    result = dict(
        page_bytes=size,
        swapins=counters["Swapins"],
        swapouts=counters["Swapouts"],
        compressor_kb=counters["Pages occupied by compressor"] * size // 1024,
    )
    if "Pages wired down" in counters:
        result["wired_kb"] = counters["Pages wired down"] * size // 1024
    return result


def vm_sample() -> dict:
    """Optional supplementary counters; absence never means zero paging."""
    try:
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2)
        boot = subprocess.run(
            ["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=2
        )
        match = re.search(r"sec\s*=\s*(\d+)", boot.stdout)
        if vm.returncode or boot.returncode or not match:
            return {}
        values = parse_vm(vm.stdout)
        if not values:
            return {}
        return {**values, "monotonic": time.monotonic(), "boot_id": match[1]}
    except (OSError, subprocess.SubprocessError):
        return {}


def measurement_signature(config, environ, source):
    """Only known non-measurement identities are excluded; future controls remain."""
    metadata = {'MEMCAP_QUEUE_LEASE', 'MEMCAP_SESSION_KEY', 'MEMCAP_AGENT_PID',
                'MEMCAP_PARENT_PID', 'MEMCAP_TOOL_CALL_ID', 'MEMCAP_NODE_WORKERS', 'MEMCAP_HOOK_PID'}
    context = sorted((k,v) for k,v in environ.items()
                     if k.startswith(('MC_', 'MEMCAP_', 'QUEUE_')) and k not in metadata)
    config = Path(config)
    return hashlib.sha256(str(config.resolve()).encode()
                          + (config.read_bytes() if config.exists() else b'')
                          + json.dumps(context).encode() + source.encode()).hexdigest()


def shared_sample(directory: Path, key: str, sampler) -> dict:
    """Elect one sampler without holding or waiting for the admission lock.

    A busy sampler returns an unavailable observation. Waiters stay registered
    and quietly retry; status, cancellation and completed jobs remain responsive.
    """
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise OSError("unsafe measurement directory")
    path = directory / "sample.json"

    def read():
        try:
            value = json.loads(path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def fresh(value, maximum=2):
        stamp = value.get("sample", {}).get("monotonic")
        return (
            value.get("key") == key
            and number(stamp)
            and 0 <= time.monotonic() - stamp < maximum
        )

    cached = read()
    if fresh(cached, 1):
        return {**cached["sample"], 'sampling_path': 1}
    fd = os.open(
        directory / "sample.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Refresh begins before expiry. Other supervisors can still use a
            # valid sample while the elected sampler works; never extend TTL.
            # The winner may have published between our read and lock attempt.
            # Re-read atomically replaced JSON; keep key and two-second checks.
            began = time.monotonic()
            # Bounded attempts also terminate with a frozen/broken test clock.
            # This waits outside the registry lock and never starts a probe.
            for attempt in range(6):
                cached = read()
                if fresh(cached):
                    return {**cached['sample'],
                            'sampling_path': 3,
                            'sampling_handoff_count': int(attempt > 0),
                            'sampling_handoff_ms': max(0, time.monotonic()-began)*1000}
                if attempt == 5 or time.monotonic()-began >= .25:
                    break
                time.sleep(.05)
            return {
                "fault": True,
                "busy": True,
                "sampling_path": 4,
                "pressure": 0,
                "monotonic": time.monotonic(),
                "sample_cache_mismatch": int(bool(cached) and cached.get('key') != key),
            }
        cached = read()
        if fresh(cached, 1):
            return {**cached["sample"], 'sampling_path': 1}
        began = time.monotonic()
        sample = sampler()
        vm = vm_sample()
        sample.update(vm)
        sample["monotonic"] = time.monotonic()
        sample["sample_duration_ms"] = int((time.monotonic() - began) * 1000)
        previous = cached.get("sample", {})
        for counter, field in (
            ("swapins", "swap_in_kbps"),
            ("swapouts", "swap_out_kbps"),
        ):
            sample[field] = rate(previous, sample, counter)
        tmp_fd, tmp = tempfile.mkstemp(prefix=".sample-", dir=directory)
        try:
            with os.fdopen(tmp_fd, "w") as stream:
                json.dump(dict(key=key, sample=sample), stream)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        append_event(
            directory,
            {
                "event": "sample",
                **sample,
                "measurement_fault": int(bool(sample.get("fault"))),
            },
        )
        return {**sample, 'sampling_path': 2}
    finally:
        os.close(fd)


@lru_cache(maxsize=1)
def runner_version():
    try:
        version = re.search(r'^MEMCAP_VERSION="([0-9]+)\.([0-9]+)\.([0-9]+)"',
                            Path(__file__).with_name("common.sh").read_text(), re.M)
        if version:
            major, minor, patch = map(int, version.groups())
            return major * 1000000 + minor * 1000 + patch
    except OSError:
        pass
    return 0


def append_event(directory: Path, event: dict) -> None:
    """Best effort, bounded private events; failure never changes admission."""
    if event.get("event") not in EVENTS:
        return
    try:
        from analytics_events import emit, root
        fields = {**ANALYTICS_CONTEXT, **event, "source": "scheduler"}
        fields["runner_version"] = runner_version()
        fields.pop("event", None)
        if "job_ref" in event:
            fields["job"] = str(event["job_ref"])
        # Synthetic Scheduler directories must never leak fixture observations
        # into an enabled live recorder when unit tests run outside Bats.
        if Path(directory) == root().parent / "queue":
            emit(event["event"], **fields)
    except Exception:
        pass
    row = {
        k: v for k, v in event.items() if k in FIELDS and number(v) and v <= 2**63 - 1
    }
    reasons = BLOCKERS
    if event.get("reason") in reasons:
        row["reason_code"] = reasons.index(event["reason"])
    row.update(event=event["event"], timestamp=time.time(), runner_version=runner_version())
    directory = Path(directory)
    lock = None
    fd = None
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_uid != os.getuid():
            return
        lock = os.open(
            directory / "events.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = directory / "events.jsonl"
        previous = directory / "events.previous.jsonl"

        def first_timestamp(candidate):
            try:
                with candidate.open() as stream:
                    stamp = json.loads(stream.readline(2048)).get("timestamp")
                return stamp if number(stamp) else candidate.stat().st_mtime
            except (OSError, ValueError, AttributeError):
                return time.time()

        for candidate in (path, previous):
            if candidate.is_symlink():
                return
            if candidate.exists() and first_timestamp(candidate) < time.time() - 86400:
                candidate.unlink()
        if path.exists() and (
            path.stat().st_size >= MAX_SEGMENT
            or first_timestamp(path) < time.time() - 43200
        ):
            os.replace(path, previous)
        fd = os.open(
            path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600
        )
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return
        os.fchmod(fd, 0o600)
        os.write(fd, (json.dumps(row, separators=(",", ":")) + "\n").encode())
    except (OSError, ValueError):
        pass
    finally:
        if fd is not None:
            os.close(fd)
        if lock is not None:
            os.close(lock)
