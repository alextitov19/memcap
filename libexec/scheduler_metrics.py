"""Shared scheduler telemetry. KiB, monotonic intervals, fixed public vocabulary."""

import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

EVENTS = {"sample", "queued", "admitted", "completed", "cancelled", "stalled"}
FIELDS = {
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
}
MAX_SEGMENT = 16 * 1024 * 1024


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
    return dict(
        page_bytes=size,
        swapins=counters["Swapins"],
        swapouts=counters["Swapouts"],
        compressor_kb=counters["Pages occupied by compressor"] * size // 1024,
    )


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

    def fresh(value):
        stamp = value.get("sample", {}).get("monotonic")
        return (
            value.get("key") == key
            and number(stamp)
            and 0 <= time.monotonic() - stamp < 2
        )

    cached = read()
    if fresh(cached):
        return cached["sample"]
    fd = os.open(
        directory / "sample.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                "fault": True,
                "busy": True,
                "pressure": 0,
                "monotonic": time.monotonic(),
            }
        cached = read()
        if fresh(cached):
            return cached["sample"]
        sample = sampler()
        vm = vm_sample()
        sample.update(vm)
        sample["monotonic"] = time.monotonic()
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
        return sample
    finally:
        os.close(fd)


def append_event(directory: Path, event: dict) -> None:
    """Best effort, bounded private events; failure never changes admission."""
    if event.get("event") not in EVENTS:
        return
    row = {
        k: v for k, v in event.items() if k in FIELDS and number(v) and v <= 2**63 - 1
    }
    reasons = (
        "unknown",
        "budget",
        "headroom",
        "slots",
        "pressure_or_measurement",
        "measurement",
        "fairness",
        "startup",
        "stabilizing",
        "paging",
        "sampling",
    )
    if event.get("reason") in reasons:
        row["reason_code"] = reasons.index(event["reason"])
    row.update(event=event["event"], timestamp=time.time())
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
