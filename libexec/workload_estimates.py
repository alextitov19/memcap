"""Private, bounded whole-run memory estimates. No probes, launches or signals."""

import hashlib
import hmac
import json
import math

GIB = 1048576


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
    if not complete or type(peak_kb) is not int or peak_kb < 0:
        return dict(row)
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
