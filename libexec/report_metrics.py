"""Bounded, read-only public diagnostics. No process identities or raw text escape."""

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

BLOCKERS = {
    "budget",
    "headroom",
    "slots",
    "pressure_or_measurement",
    "measurement",
    "fairness",
    "unknown",
    "startup",
    "stabilizing",
    "paging",
}
FACTS = {
    "queue_policy",
    "sample_age_ms",
    "swap_in_kbps",
    "swap_out_kbps",
    "compressor_kb",
    "legacy_supervisors",
    "cap_kb",
    "tracked_kb",
    "available_kb",
    "pressure",
    "measurement_fault",
    "physical_memory_kb",
    "logical_cpus",
    "physical_cpus",
    "architecture",
    "macos_major",
    "macos_minor",
    "macos_patch",
    "swap_used_kb",
    "disk_available_kb",
    "load_1m_milli",
    "load_5m_milli",
    "load_15m_milli",
    "enforcement_paused",
    "agents_including_simulators_kb",
    "docker_kb",
    "simulators_browsers_kb",
    "queue_max_jobs",
    "queue_headroom_kb",
    "queue_max_pressure",
    "queue_workers",
    "waiting",
    "running",
    "registry_age_seconds",
    "waiting_oldest_seconds",
    "running_oldest_seconds",
    "waiting_requested_kb",
    "running_requested_kb",
    "waiting_sessions",
    "running_sessions",
    "running_resources",
    "admission_oldest_seconds",
    "admission_observed_waiters",
} | {"blocked_" + reason for reason in BLOCKERS}


def sanitize(raw):
    facts = {
        k: v
        for k, v in raw.items()
        if k in FACTS and type(v) is int and 0 <= v <= 2**63 - 1
    }
    for key, allowed in {
        "pressure": (1, 2, 4),
        "queue_max_pressure": (1, 2),
        "architecture": (1, 2),
        "enforcement_paused": (0, 1),
        "measurement_fault": (0, 1),
    }.items():
        if facts.get(key) not in allowed:
            facts.pop(key, None)
    return facts


def read_json(path):
    if path.is_symlink():
        raise ValueError("symlinked report state")
    with path.open() as stream:
        raw = stream.read(1048577)
    if len(raw) > 1048576:
        raise ValueError("oversized report state")
    return json.loads(raw)


def probe(argv, timeout=0.5):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def age(value, now):
    if type(value) in (int, float) and 0 < value <= now and math.isfinite(value):
        return int(now - value)
    return None


def queue_facts(state):
    """Atomic registry read without a lock. Aggregates do not prove job liveness."""
    facts = {}
    try:
        path = state / "queue/jobs.json"
        # Read bytes and mtime from the same inode across atomic runner updates.
        if path.is_symlink():
            return facts
        with path.open() as stream:
            raw = stream.read(1048577)
            modified = os.fstat(stream.fileno()).st_mtime
        if len(raw) > 1048576:
            return facts
        jobs = json.loads(raw)["jobs"]
        if not isinstance(jobs, list) or not all(
            isinstance(j, dict) and j.get("status") in ("waiting", "running")
            for j in jobs
        ):
            return facts
        now = time.time()
        registry_age = age(modified, now)
        if registry_age is not None:
            facts["registry_age_seconds"] = registry_age
        for status, timestamp in (("waiting", "enqueued"), ("running", "started")):
            selected = [j for j in jobs if j["status"] == status]
            facts[status] = len(selected)
            ages = [age(j.get(timestamp), now) for j in selected]
            if ages and all(a is not None for a in ages):
                facts[status + "_oldest_seconds"] = max(ages)
            sizes = [j.get("memory_kb") for j in selected]
            if all(type(s) is int and 0 < s <= 2**63 - 1 for s in sizes):
                facts[status + "_requested_kb"] = sum(sizes)
            sessions = [j.get("session_key") or j.get("cwd") for j in selected]
            if all(isinstance(s, str) and s for s in sessions):
                facts[status + "_sessions"] = len(set(sessions))
            if status == "running":
                if all(isinstance(j.get("resource"), str) for j in selected):
                    facts["running_resources"] = sum(
                        bool(j["resource"]) for j in selected
                    )
                continue
            facts.update({"blocked_" + reason: 0 for reason in BLOCKERS})
            decision_ages = []
            for job in selected:
                decision = job.get("admission")
                reason, decision_age = "unknown", None
                if isinstance(decision, dict):
                    decision_age = age(decision.get("at"), now)
                    candidate = decision.get("reason")
                    if (
                        isinstance(candidate, str)
                        and candidate in BLOCKERS
                        and decision_age is not None
                    ):
                        reason = candidate
                facts["blocked_" + reason] += 1
                if decision_age is not None:
                    decision_ages.append(decision_age)
            facts["admission_observed_waiters"] = len(decision_ages)
            if decision_ages:
                facts["admission_oldest_seconds"] = max(decision_ages)
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return sanitize(facts)


def capture(state):
    facts = {"enforcement_paused": int((state / "paused").is_file())}
    root = Path(__file__).resolve().parents[1]
    lines = probe([str(root / "bin/memcap"), "_report-sample"], timeout=2).splitlines()
    facts["measurement_fault"] = 1
    try:
        cap, tracked, available, pressure, fault = map(int, lines[0].split())
        if pressure in (1, 2, 4):
            facts["pressure"] = pressure
        if fault == 0 and min(cap, tracked, available) >= 0 and pressure in (1, 2, 4):
            facts.update(
                cap_kb=cap,
                tracked_kb=tracked,
                available_kb=available,
                measurement_fault=0,
            )
            agents, docker, sims = map(int, lines[1].split())
            if (
                min(agents, docker, sims) >= 0
                and agents + docker == tracked
                and sims <= agents
            ):
                facts.update(
                    agents_including_simulators_kb=agents,
                    docker_kb=docker,
                    simulators_browsers_kb=sims,
                )
    except (ValueError, IndexError):
        pass
    try:
        slots, headroom, pressure, workers = map(int, lines[2].split())
        if (
            1 <= slots <= 64
            and headroom > 0
            and 1 <= workers <= 64
            and pressure in (1, 2)
        ):
            facts.update(
                queue_max_jobs=slots,
                queue_headroom_kb=headroom,
                queue_max_pressure=pressure,
                queue_workers=workers,
            )
    except (ValueError, IndexError):
        pass
    try:
        ram, logical, physical, arm = map(
            int,
            probe(
                [
                    "sysctl",
                    "-n",
                    "hw.memsize",
                    "hw.logicalcpu",
                    "hw.physicalcpu",
                    "hw.optional.arm64",
                ]
            ).split(),
        )
        if ram > 0 and 0 < physical <= logical and arm in (0, 1):
            facts.update(
                physical_memory_kb=ram // 1024,
                logical_cpus=logical,
                physical_cpus=physical,
                architecture=1 if arm else 2,
            )
    except ValueError:
        pass
    version = probe(["sw_vers", "-productVersion"])
    if re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}(?:\.[0-9]{1,3})?", version):
        parts = list(map(int, version.split(".")))
        facts.update(
            macos_major=parts[0],
            macos_minor=parts[1],
            macos_patch=parts[2] if len(parts) == 3 else 0,
        )
    swap = re.search(
        r"\bused\s*=\s*([0-9]{1,12}(?:\.[0-9]{1,3})?)([KMGT])\b",
        probe(["sysctl", "-n", "vm.swapusage"]),
    )
    if swap:
        facts["swap_used_kb"] = int(
            float(swap[1]) * {"K": 1, "M": 1024, "G": 1048576, "T": 1073741824}[swap[2]]
        )
    try:
        facts["disk_available_kb"] = shutil.disk_usage(Path.home()).free // 1024
    except OSError:
        pass
    try:
        for window, value in zip((1, 5, 15), os.getloadavg()):
            if math.isfinite(value) and value >= 0:
                facts[f"load_{window}m_milli"] = round(value * 1000)
    except OSError:
        pass
    try:
        sample = read_json(state / "queue/sample.json")["sample"]
        stamp = sample.get("monotonic")
        if type(stamp) in (int, float) and 0 <= time.monotonic() - stamp < 60:
            facts["sample_age_ms"] = int((time.monotonic() - stamp) * 1000)
            for key in ("swap_in_kbps", "swap_out_kbps", "compressor_kb"):
                value = sample.get(key)
                if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                    facts[key] = int(value)
        data = read_json(state / "queue/jobs.json")
        facts["queue_policy"] = int(data.get("policy") == "adaptive")
        facts["legacy_supervisors"] = sum(
            j.get("scheduler_version", 1) < 2 for j in data["jobs"]
        )
    except (OSError, ValueError, KeyError, TypeError):
        pass
    facts.update(queue_facts(state))
    return sanitize(facts)
