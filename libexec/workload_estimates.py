"""Private, bounded whole-run memory estimates. No probes, launches or signals."""

import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import time

GIB = 1048576


def source_identity(cwd, max_files=4096, max_bytes=8 * 1024 * 1024):
    """Bounded source identity for estimates that may shrink admission.

    Incomplete enumeration produces a nonreusable key, never a falsely matching
    low-memory profile. No target imports or commands are executed.
    """
    digest, count, size = hashlib.sha256(), 0, 0
    deadline = time.monotonic() + .15
    try:
        for directory, dirs, files in os.walk(cwd, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in {'.git', 'node_modules', 'vendor', 'target', 'build', '.build', '.venv', 'venv', '__pycache__', '.next'})
            for name in sorted(files):
                if time.monotonic() > deadline:
                    raise ValueError('source scan budget')
                path = Path(directory) / name
                if path.suffix not in {'.go', '.mod', '.sum', '.py', '.js', '.jsx', '.ts', '.tsx', '.json', '.sh', '.rs', '.toml'}:
                    continue
                count += 1
                if path.is_symlink() or count > max_files:
                    raise ValueError('incomplete source identity')
                length = path.stat().st_size
                size += length
                if size > max_bytes:
                    raise ValueError('source byte budget')
                data = path.read_bytes()
                if len(data) != length:
                    raise ValueError('source changed')
                digest.update(str(path.relative_to(cwd)).encode() + b'\0' + data + b'\0')
        return digest.hexdigest()
    except (OSError, ValueError):
        return 'incomplete-' + os.urandom(16).hex()


def estimate(prior_kb: int, peaks_kb: list, complete_runs: int) -> int:
    if (
        not peaks_kb
        or type(complete_runs) is not int
        or complete_runs <= 0
        or any(type(p) is not int or p < 0 for p in peaks_kb)
    ):
        return prior_kb
    peaks = sorted(peaks_kb[-50:])
    peak = peaks[-1] if complete_runs < 20 else peaks[math.ceil(len(peaks) * 0.95) - 1]
    return max(GIB // 2, math.ceil(peak * 1.25))


def record(row: dict, peak_kb: int, *, complete: bool) -> dict:
    if type(peak_kb) is not int or peak_kb < 0:
        return dict(row)
    if not complete:
        # A missed sample cannot justify lowering a prediction, but it also
        # cannot erase a large peak we actually observed. Keep complete-run
        # counts/distributions untouched and retain the new high-water floor.
        return {**row, "estimate_kb": max(row.get("estimate_kb", GIB), math.ceil(peak_kb * 1.25))}
    peaks = (row.get("peaks_kb", []) + [peak_kb])[-50:]
    count = min(1000000, row.get("complete_runs", 0) + 1)
    prior = row.get("estimate_kb", GIB)
    target = estimate(prior, peaks, count)
    # A newly observed large peak is never discarded as a percentile outlier.
    target = max(target, math.ceil(peak_kb * 1.25), math.ceil(prior * 0.9))
    return dict(estimate_kb=target, peaks_kb=peaks, complete_runs=count)


def fingerprint(description: dict, key: bytes) -> str:
    payload = json.dumps(description, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()
