"""Per-user workload admission. No sockets, service, third-party packages or kills.

The lock covers both reservation and a gated child launch. Signals are requested
through the existing shell choke point, never sent by this module.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import shlex
import math
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from admission import advance, decide
from queue_deadlines import WAIT_SECONDS
from scheduler_metrics import append_event, shared_sample, blocker_fields, BLOCKERS
from workload_estimates import fingerprint, record as record_estimate
from workload_members import footprint_members, refresh_footprint_members
from scheduler_lanes import lane as job_lane, lane_code, candidate as small_candidate, script_index, SMALL_BURST

from scheduler_policy import (
    hook_response,
    polling_loop,
    POLL_GUIDANCE,
    simple_words,
    worker_argv,
    worker_environment,
)

GIB = 1048576
ROOT = Path(__file__).resolve().parents[1]
EXPIRED_GUIDANCE = (
    " This waiter has exited and cannot resume. Check memcap status/queue and confirm "
    "no existing live task before retrying through memcap with background waiting. "
    "Do not blindly repeat an expired deadline or bypass protection."
)


class QueueError(Exception):
    pass


class QueueLockBusy(QueueError):
    pass


def processes() -> dict:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,uid=,lstart=,stat="],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode or not result.stdout.strip():
        raise QueueError("process identities unavailable")
    rows = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 10:
            raise QueueError("incomplete process identity sample")
        pid, ppid, group, uid = map(int, parts[:4])
        if "Z" not in parts[9]:
            rows[str(pid)] = {
                "ppid": ppid,
                "group": group,
                "uid": uid,
                "start": " ".join(parts[4:9]),
            }
    return rows


def wait_targets(jobs, caller, table):
    """Never let a legacy managed wait await its own verified process ancestry."""
    ancestry = {}
    pid = str(caller)
    for _ in range(128):
        row = table.get(pid)
        if not row or pid in ancestry:
            break
        ancestry[pid] = row.get("start")
        pid = str(row["ppid"])
    return [
        j
        for j in jobs
        if not any(
            start is not None and j.get("members", {}).get(pid) == start
            for pid, start in ancestry.items()
        )
    ]


def sample_host() -> dict:
    result = subprocess.run(
        [str(ROOT / "bin/memcap"), "_queue-sample"],
        capture_output=True,
        text=True,
        timeout=25,
    )
    if result.returncode:
        raise QueueError("memory measurement unavailable; no work admitted")
    lines = result.stdout.splitlines()
    try:
        cap, tracked, available, pressure, fault = map(int, lines[0].split())
        sample = {
            "cap_kb": cap,
            "tracked_kb": tracked,
            "available_kb": available,
            "pressure": pressure,
            "fault": bool(fault),
            "footprints": {},
            "tracked_pids": [],
        }
        for line in lines[1:]:
            pid, kb, counted = map(int, line.split())
            if kb < 0:
                raise ValueError("negative footprint")
            sample["footprints"][str(pid)] = kb
            if counted:
                sample["tracked_pids"].append(pid)
        return sample
    except (IndexError, ValueError) as exc:
        raise QueueError("invalid memory sample; no work admitted") from exc


def current_pressure():
    result = subprocess.run(
        ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
        capture_output=True,
        text=True,
        timeout=3,
    )
    return int(result.stdout.strip()) if result.returncode == 0 else 0


def pressure_allows(allowed, reader=None):
    try:
        return (reader or current_pressure)() in allowed
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


class Scheduler:
    lane = staticmethod(job_lane)

    def __init__(
        self,
        directory,
        sampler=sample_host,
        max_jobs=2,
        workers=2,
        memory_gb=2,
        headroom_gb=3,
        poll=2,
        max_pressure="green",
        policy="strict",
    ):
        if policy not in ("strict", "adaptive"):
            raise QueueError("QUEUE_POLICY must be strict or adaptive")
        self.policy = policy
        self.controller = {}
        self.directory = Path(directory)
        self.sampler = sampler
        self.max_jobs = max_jobs
        self.workers = workers
        self.memory_kb = int(memory_gb * GIB)
        self.headroom_kb = int(headroom_gb * GIB)
        self.poll = poll
        self.cancelled = 0
        if max_pressure not in ("green", "yellow"):
            raise QueueError("QUEUE_MAX_PRESSURE must be green or yellow")
        self.allowed_pressure = (1, 2) if max_pressure == "yellow" else (1,)
        for value in (max_jobs, workers, memory_gb, headroom_gb, poll):
            if (
                not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise QueueError("queue settings must be positive finite numbers")
        if max_jobs > 64 or workers > 64:
            raise QueueError("queue concurrency/worker settings must be at most 64")

    @contextmanager
    def locked(self, timeout=35):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or self.directory.stat().st_uid != os.getuid():
            raise QueueError(
                "queue directory must be owned by this user and not a symlink"
            )
        os.chmod(self.directory, 0o700)
        fd = os.open(
            self.directory / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise QueueLockBusy("queue lock unavailable")
                    time.sleep(0.05)
            path = self.directory / "jobs.json"
            try:
                data = json.loads(path.read_text())
                if not isinstance(data, dict) or not isinstance(data["jobs"], list):
                    raise ValueError("invalid registry")
                turns = data.get("session_turns", {})
                if not isinstance(turns, dict) or any(
                    not isinstance(k, str) or type(v) is not int or v < 0
                    for k, v in turns.items()
                ):
                    raise ValueError("invalid session rotation history")
                for job in data["jobs"]:
                    if (
                        not isinstance(job, dict)
                        or job.get("status") not in {"waiting", "running"}
                        or not isinstance(job.get("members"), dict)
                        or not isinstance(job.get("footprint_members", {}), dict)
                        or not isinstance(job.get("owner"), int)
                        or not isinstance(job.get("group"), int)
                        or not isinstance(job.get("memory_kb"), int)
                        or job["memory_kb"] <= 0
                        or not all(
                            isinstance(job.get(k), str)
                            for k in ("id", "owner_start", "resource", "cwd", "label")
                        )
                    ):
                        raise ValueError("invalid job record")
            except FileNotFoundError:
                data = {"jobs": []}
            except (ValueError, KeyError) as exc:
                raise QueueError(
                    "queue registry is damaged; refusing to discard reservations"
                ) from exc
            yield data
        finally:
            os.close(fd)

    def save(self, data):
        fd, filename = tempfile.mkstemp(prefix=".jobs-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(data, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(filename, self.directory / "jobs.json")
        finally:
            if os.path.exists(filename):
                os.unlink(filename)

    def measure(self, data=None):
        # Never called under the admission lock. The independent nonblocking
        # sample lock elects one probe for all supervisors, including running jobs.
        if self.sampler is not sample_host:
            return self.sampler()
        config = (
            Path(os.environ.get("MEMCAP_CONFIG_HOME", str(Path.home() / ".config")))
            / "memcap/memcap.conf"
        )
        signature = hashlib.sha256(
            str(config).encode()
            + (config.read_bytes() if config.exists() else b"")
            + json.dumps(
                sorted(
                    (k, v)
                    for k, v in os.environ.items()
                    if k.startswith(("MC_", "MEMCAP_", "QUEUE_"))
                    and k != "MEMCAP_QUEUE_LEASE"
                )
            ).encode()
        ).hexdigest()
        try:
            return shared_sample(self.directory, signature, self.sampler)
        except (QueueError, OSError, ValueError, subprocess.SubprocessError):
            return {"fault": True, "pressure": 0, "monotonic": time.monotonic()}

    def observe(self, data, sample):
        if not sample or sample.get("busy"):
            self.controller = {**data.get("controller", {}), "now": time.monotonic()}
            return
        self.controller = advance(data.get("controller", {}), sample, time.monotonic())
        data["controller"] = self.controller
        data["policy"] = self.policy
        for job in data["jobs"]:
            if job["status"] != "running" or not footprint_members(job):
                continue
            stamp = sample.get("monotonic", time.monotonic())
            if stamp <= job.get("last_observation", job.get("start_monotonic", 0)):
                continue
            job["last_observation"] = stamp
            footprints = sample.get("footprints", {})
            complete = not sample.get("fault") and all(
                p in footprints for p in footprint_members(job)
            )
            job["measurement_complete"] = complete
            measured = sum(footprints.get(p, 0) for p in footprint_members(job))
            if complete:
                job["measured_kb"] = measured
                job["measured_at"] = time.time()
            previous_reservation = job.get("reservation_kb", job["memory_kb"])
            job["reservation_kb"] = self.reservation(
                job, sample, measured, adaptive=self.policy == "adaptive"
            )
            if job["reservation_kb"] != previous_reservation and re.fullmatch(r"[a-f0-9]{32}", str(job.get("id", ""))):
                append_event(self.directory, dict(
                    event="reservation", job_ref=int(job["id"][:13], 16),
                    session=job.get("session_key", ""), family="unknown",
                    reservation_kb=job["reservation_kb"], measured_kb=measured,
                    measurement_complete=int(complete),
                    reservation_source=job.get("reservation_source", 0),
                    lane_code=lane_code(job),
                ))
            if not job["members"]:
                # The foreground group finished while attributed work remains.
                # Its future peak is unknown; never train a lower estimate.
                job["learning_incomplete"] = True
            if not sample.get("fault"):
                # A verified partial measurement is a lower bound on the peak.
                # Preserve it for upward-only learning even when siblings were
                # missing or the command exits before a second observation.
                job["observed_peak_kb"] = max(job.get("observed_peak_kb", 0), measured)
                # Persist observed growth immediately. A supervisor killed by a
                # session limit may never reach the completion-learning path.
                key = job.get("estimate_key")
                if key and measured > job.get("learned_peak_kb", 0):
                    history = data.setdefault("estimates", {})
                    for learned_key in {key, job.get("estimate_family_key", "")} - {""}:
                        history[learned_key] = record_estimate(history.pop(learned_key, {"estimate_kb": job["memory_kb"]}), measured, complete=False)
                    job["learned_peak_kb"] = measured
                    while len(history) > 256:
                        del history[next(iter(history))]
            if sample.get("fault") or not all(p in footprints for p in footprint_members(job)):
                job["learning_incomplete"] = True
                continue
            job["sample_count"] = job.get("sample_count", 0) + 1
        if self.policy == "adaptive":
            history = data.get("estimates", {})
            for job in data["jobs"]:
                if job["status"] != "waiting" or job.get("elastic") is not True:
                    continue
                exact = history.get(job.get("estimate_key", ""), {})
                row = exact or history.get(job.get("estimate_family_key", ""), {})
                if row.get("estimate_kb", 0) > job["memory_kb"]:
                    job["memory_kb"] = row["estimate_kb"]
                    job["estimate_source"] = 3 if exact else 4
                    job["estimate_complete_runs"] = row.get("complete_runs", 0)

    def allocation(self, jobs, sample=None):
        if self.policy == "strict":
            return self.workers
        contenders = max(1, len({self.session_bucket(j) for j in jobs}))
        pool = max(1, (os.cpu_count() or 2) - 2)
        occupied = sum(
            j.get("workers", self.workers)
            for j in jobs
            if j["status"] == "running" and not j["resource"]
        )
        workers = max(1, min(self.workers, pool // contenders, pool - occupied))
        if sample and not sample.get('fault') and not sample.get('busy'):
            available = sample.get('available_kb')
            if type(available) in (int, float) and math.isfinite(available) and available >= 0:
                # CPU capacity is not RAM capacity. Allocate at most one worker
                # per GiB of startup headroom; one is the execution minimum,
                # not permission to admit. The full admission checks still run.
                spare = available - min(self.headroom_kb, GIB // 2)
                for job in jobs:
                    if job['status'] == 'running':
                        measured = job.get('measured_kb', 0) if job.get('measurement_complete') else 0
                        spare -= max(0, job.get('reservation_kb', job.get('memory_kb', self.memory_kb)) - measured)
                workers = min(workers, max(1, int(spare // GIB)))
        return workers

    def demand(self, argv, cwd, workers, data):
        # Executable identity plus dependency manifests invalidate estimates on
        # tool/dependency changes; worker count conditions the learned peak.
        import shutil

        words = argv
        if (
            len(argv) >= 3
            and Path(argv[0]).name in {"bash", "zsh", "sh"}
            and argv[1] in {"-c", "-lc"}
        ):
            words = simple_words(argv[2]) or argv
        executable = shutil.which(words[0]) or words[0]
        try:
            info = Path(executable).stat()
            version = [info.st_size, info.st_mtime_ns]
        except OSError:
            version = []
        manifests = {}
        for name in (
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "go.sum",
            "Cargo.lock",
        ):
            path = cwd / name
            if path.is_file():
                manifests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        secret = data.setdefault("estimate_secret", os.urandom(32).hex())
        description = dict(
                argv=argv,
                cwd=str(cwd),
                executable=executable,
                version=version,
                workers=workers,
                worker_control_version=1,
                node_worker_limit=int(worker_environment(dict(os.environ), workers)['MEMCAP_NODE_WORKERS']),
                manifests=manifests,
                cache_state={
                    name: (cwd / name).exists()
                    for name in ("node_modules", "target", ".build", "build")
                },
        )
        # A timestamp/tag argument must not erase evidence from the same script.
        # Exact profiles still win. The fallback only raises first-use estimates
        # and is isolated by script bytes, cwd, executable, workers and manifests.
        self.estimate_family_key = ""
        self.small_candidate = small_candidate(words)
        index = script_index(words)
        if len(words) > index and not words[index].startswith("-"):
            script = cwd / words[index]
            try:
                if script.suffix in {".sh", ".py"} and script.is_file() and script.stat().st_size <= 1048576:
                    description.update(script=str(script.resolve()), script_sha256=hashlib.sha256(script.read_bytes()).hexdigest())
                    self.estimate_family_key = fingerprint(
                        {**description, "argv": words[:index+1]}, bytes.fromhex(secret))
                elif index:
                    self.small_candidate = False
            except OSError:
                self.small_candidate = False
        elif index and (len(words) < 2 or words[1] != "-c"):
            self.small_candidate = False
        key = fingerprint(description, bytes.fromhex(secret))
        # Opaque commands retain the configured prior. Recognized small tool
        # families get a smaller startup allowance, never a zero-cost bypass.
        tool = Path(words[0]).name
        prior = (
            min(self.memory_kb, GIB)
            if tool
            in {
                "go",
                "tsc",
                "eslint",
                "prettier",
                "jest",
                "vitest",
                "python",
                "python3",
            }
            else self.memory_kb
        )
        if tool in {"xcodebuild", "swift"}:
            prior = max(prior, 4 * GIB)
        history = data.get("estimates", {})
        row = history.get(key, {}) or history.get(self.estimate_family_key, {})
        return key, row.get("estimate_kb", prior)

    def refresh(self, data, table):
        live = []
        for job in data["jobs"]:
            try:
                owner = table.get(str(job["owner"]))
                owner_alive = owner is not None and owner["start"] == job["owner_start"]
                members = {
                    pid: row["start"]
                    for pid, row in table.items()
                    if job["group"]
                    and row["group"] == job["group"]
                    and row["uid"] == os.getuid()
                }
                if job["status"] == "waiting":
                    if owner_alive:
                        live.append(job)
                    continue
                if owner_alive or members:
                    # With a dead supervisor retain uncertain groups rather than
                    # reuse capacity or authorize signalling a recycled PID.
                    if owner_alive:
                        job.setdefault("footprint_members", dict(job["members"]))
                        job["members"] = members
                    job["orphaned"] = not owner_alive
                    from orphan_recovery import recovered
                    job["recovery_active"] = not owner_alive and recovered(job, table, time.time())
                    live.append(job)
            except (KeyError, TypeError) as exc:
                raise QueueError(
                    "invalid job record; refusing to discard reservations"
                ) from exc
        data["jobs"] = live
        refresh_footprint_members(live, table, os.getuid())

    @staticmethod
    def reservation(job, sample, measured, adaptive=False):
        # Automatic estimates cover the startup burst, then follow observed demand.
        # Explicit requests and uncertain/departed owners keep their full allowance.
        reserve = job.get("reservation_kb", job["memory_kb"])
        uncertain = job.get("orphaned") and not job.get("recovery_active")
        if not adaptive or job.get("elastic") is not True or uncertain:
            reserve = max(job["memory_kb"], reserve)
        admission_peak = job.get("admission_peak_kb")
        if adaptive and job.get("elastic") is True and type(admission_peak) is int and admission_peak >= 0:
            # Once a complete window retires an old peak, a sampling gap must
            # retain the current allowance, not resurrect the lifetime learning
            # peak. Carry forward partial growth and an uncertain owner's floor
            # until a new complete window can safely reduce them.
            admission_peak = max(admission_peak, measured, (reserve * 4 + 4) // 5)
            job["admission_peak_kb"] = admission_peak
        else:
            admission_peak = None
        # Automatic adaptive allowances may already have shrunk from the startup
        # estimate. A busy/faulty/incomplete observation retains that established
        # allowance; it must not silently reinstate the original request. Fresh
        # complete observations alone can shrink, and known growth still raises it.
        if (
            job.get("elastic") is True
            and not uncertain
            and footprint_members(job)
            and not sample.get("fault", False)
            and all(p in sample.get("footprints", {}) for p in footprint_members(job))
        ):
            peak = max(measured, job.get("observed_peak_kb", 0))
            job["observed_peak_kb"] = peak
            if admission_peak is not None:
                peak = max(measured, admission_peak)
            stamp = sample.get("monotonic", 0)
            if adaptive and 0 <= time.monotonic() - stamp <= 2:
                history = job.get("reservation_window", [])
                if not history or not 0 <= stamp - history[-1][0] <= 5:
                    history = [[stamp, peak]]
                    job["reservation_window_since"] = stamp
                elif stamp > history[-1][0]:
                    history.append([stamp, measured])
                else:
                    history[-1][1] = max(history[-1][1], measured)
                history = [row for row in history if row[0] >= stamp - 60][-128:]
                job["reservation_window"] = history
                if stamp - job.get("reservation_window_since", stamp) > 60:
                    peak = max(row[1] for row in history)
                    job["admission_peak_kb"] = peak
                job["reservation_source"] = 2  # complete adaptive window
            else:
                job["reservation_source"] = 1  # lifetime peak / strict policy
                if adaptive:
                    # Multiple waiters can revisit a cached sample after its
                    # launch freshness deadline. Retain both allowance and prior
                    # history, without advancing or shrinking either. The next
                    # fresh observation still resets any gap above five seconds.
                    return max(
                        job.get("reservation_kb", job["memory_kb"]),
                        int(measured * 1.25),
                    )
                job.pop("reservation_window", None)
            if 30 <= time.time() - job.get("started", time.time()):
                reserve = max(GIB // 2, int(peak * 1.25))
            else:
                reserve = max(reserve, int(peak * 1.25))
        else:
            if not sample.get("busy"):
                job.pop("reservation_window", None)
            job["reservation_source"] = 3  # explicit or incomplete: retain
        return max(reserve, measured)

    def admissible(self, jobs, memory, resource, sample, lane="heavy"):
        for job in jobs:
            if job["status"] == "running":
                measured = sum(
                    sample.get("footprints", {}).get(p, 0) for p in footprint_members(job)
                )
                job["reservation_kb"] = self.reservation(
                    job, sample, measured, adaptive=self.policy == "adaptive"
                )
                if not sample.get("fault") and all(p in sample.get("footprints", {}) for p in footprint_members(job)):
                    job["measured_kb"] = measured
                    job["measured_at"] = time.time()
        decision = decide(
            dict(
                mode=self.policy,
                max_jobs=self.max_jobs,
                headroom_kb=self.headroom_kb,
                allowed_pressure=self.allowed_pressure,
            ),
            sample,
            self.controller,
            jobs,
            dict(memory_kb=memory, resource=resource),
        )
        self.last_decision = decision
        reasons = {
            "available": "capacity available",
            "budget": "combined budget reserved or in use",
            "headroom": "preserving host memory headroom",
            "slots": "all finite-job slots occupied",
            "pressure_or_measurement": "host pressure or unreliable memory measurement",
            "measurement": "invalid memory measurement",
            "sampling": "waiting for the shared memory sampler; no measurement failure established",
            "paging": "sustained paging recovery",
            "stabilizing": "observing pressure recovery",
            "startup": "observing previous startup",
        }
        return decision["allow"], reasons[decision["reason"]]

    @staticmethod
    def session_bucket(job):
        # Older runners have no session key; keep their project's work together.
        # Completion scopes distinguish siblings without granting their parent
        # extra turns or extra worker shares by spawning more subagents.
        return job.get("session_key", "").split("/", 1)[0] or "project:" + job.get("cwd", "")

    def next_waiter(self, jobs, resource, sample, turns=None, now=None, lane_streak=0):
        # One rotation across finite jobs and resources, shared under the queue lock.
        turns = turns or {}
        now = time.time() if now is None else now
        waiting = [j for j in jobs if j["status"] == "waiting"]
        waiting.sort(
            key=lambda j: (turns.get(self.session_bucket(j), 0), j.get("enqueued", now))
        )
        fitting = {}
        for job in waiting:
            fitting[job["id"]] = self.admissible(jobs, job["memory_kb"], job["resource"], sample, lane=self.lane(job))[0]
        heavy = [j for j in waiting if self.lane(j) == "heavy"]
        small = [j for j in waiting if self.lane(j) == "small"]
        for job in heavy:
            # Give a large, aging request a chance to accumulate capacity. This
            # gates new admissions only; running jobs and reservations stay intact.
            age = now - job.get("enqueued", now)
            if (
                age >= 60
                and not fitting[job["id"]]
                and (self.policy == "strict" or age % 30 < 6)
                and any(j["status"] == "running" and not j["resource"] for j in jobs)
            ):
                return job["id"]
        if type(lane_streak) is not int:
            lane_streak = 0
        ordered = heavy + small if lane_streak >= SMALL_BURST else small + heavy
        if any(j.get('lane_version') != 1 for j in waiting):
            ordered = waiting  # Pre-lane supervisors use the original session order.
        # Lane preference breaks ties within a session rotation. It must not
        # give a busy project repeated turns ahead of fitting peers. Count
        # finite work by parent session so spawning subagents cannot buy turns.
        active = {}
        for job in jobs:
            if job['status'] == 'running' and not job['resource']:
                bucket = self.session_bucket(job)
                active[bucket] = active.get(bucket, 0) + 1
        # Already-waiting supervisors retain loaded code after an upgrade.
        # Two selectors disagreeing can each wait for the other indefinitely.
        # Keep their lane ordering until those legacy waiters have launched;
        # running legacy jobs do not prevent the new rotation taking effect.
        if all(j.get('fairness_version') == 2 for j in waiting):
            ordered.sort(key=lambda job: (
                active.get(self.session_bucket(job), 0),
                turns.get(self.session_bucket(job), 0),
            ))
        for job in ordered:
            if fitting[job["id"]]:
                return job["id"]
        return None

    def record_turn(self, data, job):
        streak = data.get("small_streak", 0)
        data["small_streak"] = min(SMALL_BURST, max(0, streak if type(streak) is int else 0) + 1) if self.lane(job) == "small" else 0
        turns = data.setdefault("session_turns", {})
        turns[self.session_bucket(job)] = max(turns.values(), default=0) + 1
        present = {self.session_bucket(j) for j in data["jobs"]}
        # Retain live contenders and a bounded recent history across completed jobs.
        recent = set(sorted(turns, key=turns.get, reverse=True)[:128])
        data["session_turns"] = {
            k: v for k, v in turns.items() if k in present or k in recent
        }

    def nested(self, data, table):
        token = os.environ.get("MEMCAP_QUEUE_LEASE")
        if not token:
            return False
        job = next(
            (j for j in data["jobs"] if j["id"] == token and j["status"] == "running"),
            None,
        )
        if not job:
            return False
        pid = str(os.getpid())
        for _ in range(128):
            row = table.get(pid)
            if not row:
                return False
            if pid in job["members"] and row["start"] == job["members"][pid]:
                return True
            pid = str(row["ppid"])
        return False

    def run(
        self, argv, cwd=None, resource="", memory_gb=None, wait=WAIT_SECONDS, session_key=""
    ):
        cwd = Path(cwd or os.getcwd()).resolve(strict=True)
        from analytics_events import producer, family
        from scheduler_metrics import analytics_context
        producer()  # Capture code/policy before this supervisor can outlive an upgrade.
        analytics_command = argv[-1] if argv and Path(argv[0]).name in {"bash", "zsh", "sh"} else " ".join(argv)
        analytics_context(session=session_key, family=family(analytics_command),
                          explicit_memory=int(memory_gb is not None),
                          persistent=int(bool(resource)),
                          **getattr(self, "analytics_metadata", {}))
        if (self.directory.parent / "paused").is_file():
            # Pause restores native execution before registry locks, sampling,
            # reservations or worker rewriting. Do not manufacture a queued task.
            if not argv:
                raise QueueError("a command is required")
            os.chdir(cwd)
            os.execvpe(argv[0], argv, dict(os.environ))
        memory = self.memory_kb if memory_gb is None else int(memory_gb * GIB)
        if (
            not argv
            or memory <= 0
            or (wait is not None and (not math.isfinite(wait) or wait < 0))
        ):
            raise QueueError(
                "command, positive reservation and nonnegative wait are required"
            )
        resource = (
            hashlib.sha256((str(cwd) + "\0" + resource).encode()).hexdigest()
            if resource
            else ""
        )
        ident = uuid.uuid4().hex
        began = time.monotonic()
        old_handlers = {}
        child = None
        analytics_claimed = False
        registered = False
        estimate_key = ""
        from orphan_recovery import agent_identity
        originating_agent = agent_identity()
        try:
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                old_handlers[sig] = signal.signal(sig, self.handle_signal)
            with self.locked() as data:
                table = processes()
                self.refresh(data, table)
                if (self.directory.parent / "paused").is_file():
                    for sig, handler in old_handlers.items():
                        signal.signal(sig, handler)
                    os.chdir(cwd)
                    os.execvpe(argv[0], argv, dict(os.environ))
                if self.nested(data, table):
                    # No second reservation; preserve nested command semantics.
                    # exec also preserves native signal and exit status behavior.
                    for sig, handler in old_handlers.items():
                        signal.signal(sig, handler)
                    os.chdir(cwd)
                    argv = worker_argv(argv, cwd, self.workers)
                    os.execvpe(
                        argv[0],
                        argv,
                        worker_environment(dict(os.environ), self.workers),
                    )
                owner = table.get(str(os.getpid()))
                if not owner:
                    raise QueueError("cannot identify queue supervisor")
                if sum(j["status"] == "waiting" for j in data["jobs"]) >= 64:
                    raise QueueError(
                        "64 jobs already waiting; batch submissions instead of adding more runners"
                    )
                workers = self.allocation(data["jobs"])
                if self.policy == "adaptive" and memory_gb is None:
                    estimate_key, memory = self.demand(argv, cwd, workers, data)
                estimate_row = data.get("estimates", {}).get(estimate_key, {})
                family_row = data.get("estimates", {}).get(getattr(self, "estimate_family_key", ""), {}) if estimate_key else {}
                data["jobs"].append(
                    {
                        "id": ident,
                        "owner": os.getpid(),
                        "owner_start": owner["start"],
                        **originating_agent,
                        "group": 0,
                        "members": {},
                        "status": "waiting",
                        "resource": resource,
                        "cwd": str(cwd),
                        "memory_kb": memory,
                        "workers": workers,
                        "estimate_key": estimate_key,
                        "estimate_family_key": getattr(self, "estimate_family_key", "") if estimate_key else "",
                        "estimate_source": 1 if memory_gb is not None else (3 if estimate_row else 4 if family_row else 2),
                        "estimate_complete_runs": estimate_row.get("complete_runs", 0),
                        "lane_version": 1,
                        "demand_version": 1,
                        "fairness_version": 2,
                        "small_candidate": False,  # Old selectors must also see new managed work as heavy.
                        "classification_code": getattr(self, "classification_code", 0),
                        "scheduler_version": 2,
                        "elastic": memory_gb is None,
                        "enqueued": time.time(),
                        "enqueued_monotonic": time.monotonic(),
                        "label": Path(argv[0]).name,
                        "cancel": False,
                        "session_key": session_key,
                    }
                )
                self.save(data)
                registered = True
            append_event(self.directory, dict(event="queued", job_ref=int(ident[:13], 16),
                                              request_kb=memory, workers=workers,
                                              lane_code=lane_code(data["jobs"][-1])))
            from command_trace import job as trace_job
            trace_job(self.directory, 'queued', job=ident, argv=argv, cwd=str(cwd),
                      session=session_key, request_kb=memory, workers=workers)
            last_notice = 0.0
            waited = False
            while child is None:
                if self.cancelled:
                    return 128 + self.cancelled
                if waited and wait is not None and time.monotonic() - began >= wait:
                    print(
                        "memcap: queue wait expired; command not started."
                        + EXPIRED_GUIDANCE,
                        file=sys.stderr,
                    )
                    return 75
                sample = (
                    self.measure()
                    if not (self.directory.parent / "paused").exists()
                    else {}
                )
                with self.observed_registry(retry=True, pending=True,
                                            deadline=None if wait is None else began + wait) as (data, table):
                    if data is None:
                        waited = True
                        continue  # Top-of-loop owns deadline/cancellation results.
                    self.refresh(data, table)
                    self.observe(data, sample)
                    job = next(j for j in data["jobs"] if j["id"] == ident)
                    # Revalidate learned priority for the worker allocation that
                    # will actually launch, inside the same admission lock.
                    workers = min(job.get("workers", self.workers), self.allocation(data["jobs"], sample))
                    if job.get("estimate_key") and (
                        workers != job.get("workers") or
                        (self.lane(job) == "small" and not sample.get("fault") and not sample.get("busy"))
                    ):
                        key, updated_memory = self.demand(argv, cwd, workers, data)
                        exact = data.get("estimates", {}).get(key, {})
                        job.update(estimate_key=key, estimate_family_key=self.estimate_family_key,
                                   estimate_source=3 if exact else 4 if data.get("estimates", {}).get(self.estimate_family_key) else 2,
                                   estimate_complete_runs=exact.get("complete_runs", 0),
                                   small_candidate=self.small_candidate if job.get('demand_version') != 1 else False, workers=workers,
                                   memory_kb=max(job["memory_kb"], updated_memory))
                    job['workers'] = workers
                    memory = job["memory_kb"]
                    active_resource = next(
                        (
                            j
                            for j in data["jobs"]
                            if resource
                            and j["resource"] == resource
                            and j["status"] == "running"
                        ),
                        None,
                    )
                    if active_resource:
                        from analytics_events import emit
                        emit("claim", source="scheduler", session=session_key, job=str(int(ident[:13], 16)),
                             resource=str(int(active_resource["id"][:13], 16)), persistent=1)
                        analytics_claimed = True
                        if originating_agent:
                            active_resource.setdefault("claims", {})[str(originating_agent["agent_owner"])] = originating_agent["agent_start"]
                            active_resource.pop("recovery", None)
                            self.save(data)
                        print(
                            f"memcap: resource already running as job {active_resource['id'][:8]} (pid {active_resource['group']}); reuse it",
                            file=sys.stderr,
                        )
                        return 0
                    allowed, reason = False, "waiting for earlier queued work"
                    if (self.directory.parent / "paused").exists():
                        allowed, reason = True, "memcap is paused"
                    else:
                        try:
                            if sample.get("cap_kb") and memory > sample["cap_kb"]:
                                raise QueueError(
                                    "job reservation exceeds the entire budget; split the job"
                                )
                            allowed, reason = self.admissible(
                                data["jobs"], memory, resource, sample, lane=self.lane(job)
                            )
                            if (
                                allowed
                                and self.next_waiter(
                                    data["jobs"],
                                    resource,
                                    sample,
                                    data.get("session_turns"),
                                    lane_streak=data.get("small_streak", 0),
                                )
                                != ident
                            ):
                                allowed, reason = (
                                    False,
                                    "waiting for another session’s turn or an aged job to fit",
                                )
                        except (
                            OSError,
                            KeyError,
                            ValueError,
                            TypeError,
                            subprocess.SubprocessError,
                        ) as exc:
                            raise QueueError(
                                "memory sampling failed; command was not admitted"
                            ) from exc
                    if self.cancelled:
                        return 128 + self.cancelled
                    if waited and wait is not None and time.monotonic() - began >= wait:
                        print(
                            "memcap: queue wait expired; command not started."
                            + EXPIRED_GUIDANCE,
                            file=sys.stderr,
                        )
                        return 75
                    if (
                        allowed
                        and self.sampler is sample_host
                        and not (self.directory.parent / "paused").exists()
                        and not pressure_allows(self.allowed_pressure)
                    ):
                        allowed, reason = (
                            False,
                            "host pressure or unreliable pressure measurement",
                        )
                    if allowed:
                        child = self.launch(argv, cwd, job, data)
                        trace_job(self.directory, 'admitted', job=ident,
                                  wait_seconds=time.monotonic() - began)
                        if waited:
                            print(
                                f"memcap: admitted {ident[:8]}; command started. "
                                "Admission confirms capacity at launch, not simulator readiness "
                                "or test success. Read final output and exit status.",
                                file=sys.stderr,
                            )
                    else:
                        waited = True
                        # Record only a fixed diagnostic code and wall-clock time.
                        # Reporters read this atomically without taking our lock.
                        job["admission"] = {
                            "at": time.time(),
                            "reason": {
                                "combined budget reserved or in use": "budget",
                                "preserving host memory headroom": "headroom",
                                "all finite-job slots occupied": "slots",
                                "small-job slots occupied": "slots",
                                "host pressure or unreliable memory measurement": "pressure_or_measurement",
                                "host pressure or unreliable pressure measurement": "pressure_or_measurement",
                                "invalid memory measurement": "measurement",
                                "waiting for the shared memory sampler; no measurement failure established": "sampling",
                                "sustained paging recovery": "paging",
                                "observing pressure recovery": "stabilizing",
                                "observing previous startup": "startup",
                                "waiting for earlier queued work": "fairness",
                                "waiting for another session’s turn or an aged job to fit": "fairness",
                            }.get(reason, "unknown"),
                        }
                        for field in (
                            "outstanding_kb",
                            "available_kb",
                            "request_kb",
                            "headroom_kb",
                            "headroom_deficit_kb",
                        ):
                            value = getattr(self, "last_decision", {}).get(field)
                            if type(value) is int and value >= 0:
                                job["admission"][field] = value
                        job["admission"]["measurement_busy"] = int(
                            bool(sample.get("busy"))
                        )
                        from scheduler_metrics import account_blocker

                        account_blocker(job, job["admission"]["reason"], time.monotonic())
                        self.save(data)
                        if wait is not None and time.monotonic() - began >= wait:
                            print(
                                f"memcap: queue wait expired: {reason}; command not started."
                                + EXPIRED_GUIDANCE,
                                file=sys.stderr,
                            )
                            return 75
                        if time.monotonic() - last_notice > 60:
                            trace_job(self.directory, 'waiting', job=ident,
                                      argv=argv, cwd=str(cwd),
                                      wait_seconds=time.monotonic() - began,
                                      admission=job['admission'])
                            details = ""
                            if job["admission"]["reason"] == "headroom":
                                d = job["admission"]
                                details = (
                                    f" Available {d.get('available_kb', 0) / GIB:.2f} GiB; "
                                    f"unused running reservations {d.get('outstanding_kb', 0) / GIB:.2f} GiB; "
                                    f"request {memory / GIB:.2f} GiB; "
                                    f"margin {d.get('headroom_kb', 0) / GIB:.2f} GiB."
                                )
                            append_event(
                                self.directory,
                                dict(
                                    event="stalled",
                                    job_ref=int(ident[:13], 16),
                                    classification_code=job.get("classification_code", 0),
                                    lane_code=lane_code(job),
                                    queue_wait_ms=int(
                                        (time.monotonic() - began) * 1000
                                    ),
                                    **blocker_fields(job),
                                    **{
                                        k: v
                                        for k, v in job["admission"].items()
                                        if k != "at"
                                    },
                                ),
                            )
                            print(
                                f"memcap: queued {ident[:8]} ({self.lane(job)} lane): {reason}.{details} Command has not started; "
                                f"await native completion notifications without polling when supported; otherwise poll this existing task once per minute (TaskOutput block=true timeout=60000 if available; otherwise memcap wait {ident[:8]} --timeout 60). "
                                "If Stop has blocked ending the turn, use the blocking wait instead of finishing for a notification. "
                                "Continue independent work; do not submit duplicates or bypass memcap.",
                                file=sys.stderr,
                            )
                            last_notice = time.monotonic()
                if child is None:
                    time.sleep(self.poll)
            last_cancel_notice = 0.0
            while True:
                result = child.poll()
                sample = (
                    self.measure()
                    if result is None or self.policy == "adaptive"
                    else {}
                )
                with self.observed_registry(retry=True) as (data, table):
                    self.refresh(data, table)
                    job = next(j for j in data["jobs"] if j["id"] == ident)
                    self.observe(data, sample)
                    job["cancel"] = bool(self.cancelled)
                    members = dict(job["members"])
                    if result is not None and not members:
                        trace_job(self.directory, 'completed', job=ident,
                                  exit_code=result if result >= 0 else 128 - result,
                                  signal=-result if result < 0 else 0)
                        key = job.get("estimate_key")
                        learning_complete = result == 0 and job.get("sample_count", 0) >= 2 and not job.get("learning_incomplete")
                        if (
                            key
                            and not self.cancelled
                            and (learning_complete or job.get("observed_peak_kb", 0) > 0)
                        ):
                            history = data.setdefault("estimates", {})
                            # Updating an old key must refresh its retention
                            # position; otherwise frequent expensive work loses
                            # its estimate when unrelated commands fill the cache.
                            history[key] = record_estimate(
                                history.pop(key, {"estimate_kb": job["memory_kb"]}),
                                job.get("observed_peak_kb", 0),
                                complete=learning_complete,
                            )
                            while len(history) > 256:
                                del history[next(iter(history))]
                        from scheduler_metrics import completion_fields

                        append_event(
                            self.directory,
                            dict(
                                event="completed",
                                job_ref=int(ident[:13], 16),
                                **completion_fields(result, self.cancelled),
                                runtime_ms=int(max(0, time.monotonic() - job.get("start_monotonic", time.monotonic())) * 1000),
                                peak_kb=job.get("observed_peak_kb", 0),
                                learning_complete=int(learning_complete),
                                lane_code=job.get("admission_lane_code", lane_code(job)),
                                **blocker_fields(job),
                            ),
                        )
                    self.save(data)
                if self.cancelled:
                    if not members:
                        return 128 + self.cancelled
                    try:
                        cancelled = (
                            subprocess.run(
                                [str(ROOT / "bin/memcap"), "_queue-cancel", ident],
                                check=False,
                                timeout=30,
                            ).returncode
                            == 0
                        )
                    except (OSError, subprocess.SubprocessError):
                        cancelled = False
                    if not cancelled:
                        if time.monotonic() - last_cancel_notice >= 60:
                            print(
                                "memcap: guarded cancellation unavailable; retaining supervision and reservations, retrying with fresh identities.",
                                file=sys.stderr,
                            )
                            last_cancel_notice = time.monotonic()
                        time.sleep(self.poll)
                        continue
                    return 128 + self.cancelled
                if result is not None and not members:
                    return result if result >= 0 else 128 - result
                time.sleep(self.poll)
        finally:
            if registered:
                if child is None and not analytics_claimed:
                    from command_trace import job as trace_job
                    trace_job(self.directory, 'not-started', job=ident,
                              wait_seconds=time.monotonic() - began,
                              outcome='cancelled' if self.cancelled else 'timeout'
                              if wait is not None and time.monotonic() - began >= wait else 'failed')
                    append_event(self.directory, dict(event="cancelled", job_ref=int(ident[:13], 16),
                                                      queue_wait_ms=int((time.monotonic() - began) * 1000),
                                                      signal=self.cancelled,
                                                      outcome="cancelled" if self.cancelled else "timeout" if wait is not None and time.monotonic() - began >= wait else "failed"))
                if child is None:
                    # This supervisor never launched a group. Retire only its
                    # own unchanged waiting entry; a failed ps query must not
                    # turn a deadline/cancellation into another probe failure.
                    try:
                        with self.locked() as data:
                            data['jobs'] = [j for j in data['jobs'] if not (
                                j['id'] == ident and j.get('owner') == os.getpid()
                                and j.get('status') == 'waiting' and not j.get('group') and not j.get('members'))]
                            self.save(data)
                    except QueueLockBusy:
                        print('memcap: waiter cleanup deferred; registry is busy.', file=sys.stderr)
                else:
                    # An already-started group keeps its reservation until fresh
                    # identities establish that it exited.
                    with self.observed_registry(retry=True) as (data, table):
                        self.refresh(data, table)
                        data['jobs'] = [j for j in data['jobs'] if j['id'] != ident
                                        or (j['status'] == 'running' and j['members'])]
                        self.save(data)
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)

    @contextmanager
    def observed_registry(self, retry=False, pending=False, deadline=None):
        """Pair a locked registry with fresh identities; release the lock to retry."""
        last_notice = 0.0
        failed = False
        while True:
            if pending and failed and (self.cancelled or (deadline is not None and time.monotonic() >= deadline)):
                yield None, None
                return
            with ExitStack() as stack:
                try:
                    data = stack.enter_context(self.locked())
                except QueueLockBusy:
                    if not retry:
                        raise
                else:
                    try:
                        table = processes()
                    except (QueueError, OSError, ValueError, subprocess.SubprocessError):
                        if not retry:
                            raise
                    else:
                        # Yield outside the acquisition error handlers: errors
                        # in the caller must propagate, never repeat its body.
                        if pending and failed and (self.cancelled or (deadline is not None and time.monotonic() >= deadline)):
                            yield None, None
                            return
                        yield data, table
                        return
            failed = True
            if time.monotonic() - last_notice >= 60:
                print(
                    'memcap: ' + ('pending' if pending else 'running') +
                    " job registry or identities unavailable; retaining supervision and reservations until a fresh query succeeds.",
                    file=sys.stderr,
                )
                last_notice = time.monotonic()
            # Never hold the registry lock or authorize termination from a
            # failed sample. The ordinary cancellation path rechecks each PID.
            time.sleep(self.poll)

    def launch(self, argv, cwd, job, data):
        from scheduler_metrics import account_blocker

        account_blocker(job, None, time.monotonic())
        if (self.directory.parent / "paused").is_file():
            # A registered waiter may be released by an owner pause. Preserve
            # supervision of its existing job, but impose no worker limits and
            # never train a limited-worker estimate from this unrestricted run.
            workers = 0  # no memcap worker limit applied
            job["workers"] = workers
            job["estimate_key"] = ""
            env = dict(os.environ)
        else:
            workers = min(
                job.get("workers", self.workers), self.allocation(data["jobs"])
            )
            # A smaller final allocation is safe, but do not teach a larger-worker
            # profile using the smaller run's peak.
            if workers != job.get("workers", workers):
                if job.get("estimate_key"):
                    job["estimate_key"], _ = self.demand(argv, cwd, workers, data)
                    job["estimate_family_key"] = self.estimate_family_key
            job["workers"] = workers
            if (
                len(argv) >= 3
                and Path(argv[0]).name in {"bash", "zsh", "sh"}
                and argv[1] in {"-c", "-lc"}
            ):
                words = simple_words(argv[2])
                if words:
                    import shlex

                    argv = (
                        argv[:2]
                        + [shlex.join(worker_argv(words, cwd, workers))]
                        + argv[3:]
                    )
            argv = worker_argv(argv, cwd, workers)
            env = worker_environment(dict(os.environ), workers)
        env["MEMCAP_QUEUE_LEASE"] = job["id"]
        read_fd, write_fd = os.pipe()
        try:
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "_exec",
                    str(read_fd),
                    json.dumps(argv),
                ],
                cwd=cwd,
                env=env,
                start_new_session=True,
                pass_fds=(read_fd,),
            )
            os.close(read_fd)
            read_fd = -1
            table = processes()
            leader = table.get(str(child.pid))
            if not leader or leader["group"] != child.pid:
                raise QueueError(
                    "cannot establish child process identity; launch withheld"
                )
            job.update(
                status="running",
                admission_lane_code=lane_code(job),
                started=time.time(),
                start_monotonic=time.monotonic(),
                group=child.pid,
                members={str(child.pid): leader["start"]},
            )
            self.record_turn(data, job)
            data.setdefault("controller", {})["last_start"] = time.monotonic()
            self.save(data)
            append_event(
                self.directory,
                dict(
                    event="admitted",
                    job_ref=int(job["id"][:13], 16),
                    workers=workers,
                    worker_control_version=int(workers > 0),
                    node_worker_limit=int(env.get('MEMCAP_NODE_WORKERS', workers)) if workers > 0 else 0,
                    request_kb=job["memory_kb"],
                    classification_code=job.get("classification_code", 0),
                    estimate_source=job.get("estimate_source", 0),
                    estimate_complete_runs=job.get("estimate_complete_runs", 0),
                    lane_code=job["admission_lane_code"],
                    **blocker_fields(job),
                    queue_wait_ms=(int(max(0, time.monotonic() - job["enqueued_monotonic"]) * 1000)
                                   if "enqueued_monotonic" in job else None),
                ),
            )
            os.write(write_fd, b"1")
            return child
        finally:
            if read_fd >= 0:
                os.close(read_fd)
            os.close(write_fd)

    def handle_signal(self, sig, _frame):
        self.cancelled = sig

    def status(self):
        if not self.directory.exists():
            return []
        try:
            data = json.loads((self.directory / "jobs.json").read_text())
            self.refresh(data, processes())
            return data["jobs"]
        except FileNotFoundError:
            return []
        except (ValueError, KeyError, TypeError) as exc:
            raise QueueError(
                "queue registry is damaged; refusing to discard reservations"
            ) from exc

    def wait_for(self, ident, timeout, session_key=None):
        if (
            (ident != "--session" and not re.fullmatch(r"[a-f0-9]{8,32}", ident))
            or not math.isfinite(timeout)
            or not 0 <= timeout <= 60
        ):
            raise QueueError(
                "usage: memcap wait JOB_ID [--timeout SECONDS (0..60)]; JOB_ID is a memcap hex ID from memcap queue, not a native tool task ID. Use memcap wait --session --timeout 60 or the native task's completion notification."
            )
        deadline = time.monotonic() + timeout
        while True:
            try:
                from idle_gc import process_table, GCError

                table = process_table()
                if ident == "--session":
                    from idle_gc import Collector, owner

                    try:
                        if not owner(str(os.getpid()), table):
                            raise QueueError(
                                "cannot identify this agent; use memcap wait JOB_ID --timeout 60"
                            )
                        matches = Collector(self.directory.parent).pending_jobs(
                            str(os.getpid()), table, session_key=session_key
                        )
                    except GCError as exc:
                        raise QueueError(
                            "queue state unreadable; task completion is unverified"
                        ) from exc
                else:
                    data = json.loads((self.directory / "jobs.json").read_text())
                    matches = [j for j in data["jobs"] if j["id"].startswith(ident)]
                matches = wait_targets(matches, str(os.getpid()), table)
            except FileNotFoundError:
                matches = []
            except (ValueError, KeyError, TypeError, GCError) as exc:
                raise QueueError(
                    "queue state unreadable; task completion is unverified"
                ) from exc
            if ident != "--session" and len(matches) > 1:
                raise QueueError(
                    "ambiguous job ID; use the full ID from memcap queue --json"
                )
            live = {"jobs": matches}
            self.refresh(live, processes())
            if not live["jobs"]:
                print(
                    f"memcap: {ident} no longer pending. Read the ORIGINAL task's final output and exit status; this does not certify success. Do not repeat memcap wait for an unmanaged native task; use its completion notification and final output/status. Do not resubmit a completed task."
                )
                return 0
            if time.monotonic() >= deadline:
                waiting = sum(j["status"] == "waiting" for j in live["jobs"])
                running = len(live["jobs"]) - waiting
                oldest = max(
                    max(
                        0,
                        time.time() - j.get("started", j.get("enqueued", time.time())),
                    )
                    for j in live["jobs"]
                )
                blockers = sorted({j.get('admission', {}).get('reason') for j in live['jobs']
                                   if j['status'] == 'waiting'
                                   and j.get('admission', {}).get('reason') in BLOCKERS})
                explanation = ('Last recorded admission blocker(s): ' + ', '.join(blockers) + '. '
                               if blockers else 'Admission blocker unavailable for this runner. ')
                print(
                    f"memcap: {ident} pending ({live['jobs'][0]['status']}); {running} running, {waiting} queued IN THIS WAIT SCOPE (not host totals); oldest current phase {int(oldest)}s. "
                    + explanation +
                    f"Repeat memcap wait {ident} --timeout 60 only if native completion notification/blocking task polling is unavailable; no job or reservation was created. Running work has already passed admission. Read original task output for workload progress."
                )
                return 0
            time.sleep(min(2, max(0, deadline - time.monotonic())))

    def authorize_cancel(self, ident, caller, pid=None):
        with self.locked() as data:
            table = processes()
            job = next((j for j in data["jobs"] if j["id"] == ident), None)
            owner = table.get(str(caller))
            if (
                not job
                or not job["cancel"]
                or job["owner"] != caller
                or not owner
                or owner["start"] != job["owner_start"]
            ):
                return []
            if (self.directory.parent / "paused").exists():
                return []
            return [
                p
                for p, start in job["members"].items()
                if (pid is None or p == str(pid))
                and p in table
                and table[p]["start"] == start
                and table[p]["group"] == job["group"]
                and table[p]["uid"] == os.getuid()
            ]


def env_number(name, default, integer=False):
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw) if integer else float(raw)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("nonpositive")
        return value
    except ValueError as exc:
        raise QueueError(f"invalid {name}; refusing to run on another policy") from exc


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "_exec":
        fd = int(sys.argv[2])
        permission = os.read(fd, 1)
        os.close(fd)
        if permission != b"1":
            return 75
        argv = json.loads(sys.argv[3])
        os.execvpe(argv[0], argv, os.environ)
    directory = (
        Path(os.environ.get("MEMCAP_STATE_HOME", str(Path.home() / ".local/state")))
        / "memcap/queue"
    )
    if sys.argv[1] == "hook" and (directory.parent / "paused").is_file():
        return 0
    scheduler = Scheduler(
        directory,
        max_jobs=env_number("QUEUE_MAX_JOBS", 2, True),
        workers=env_number("QUEUE_WORKERS", 2, True),
        memory_gb=env_number("QUEUE_JOB_GB", 2),
        headroom_gb=env_number("QUEUE_HEADROOM_GB", 3),
        poll=env_number("QUEUE_POLL_SEC", 2),
        max_pressure=os.environ.get("QUEUE_MAX_PRESSURE", "green"),
        policy=os.environ.get("QUEUE_POLICY", "strict"),
    )
    action = sys.argv[1]
    if action == "claim":
        from orphan_recovery import claim
        parser = argparse.ArgumentParser(prog="memcap claim")
        parser.add_argument("job_id")
        flags = parser.add_mutually_exclusive_group()
        flags.add_argument("--pin", action="store_true")
        flags.add_argument("--unpin", action="store_true")
        flags.add_argument("--release", action="store_true")
        args = parser.parse_args(sys.argv[2:])
        claim(scheduler, args.job_id, True if args.pin else False if args.unpin else None, args.release)
        print("memcap: resource claim updated; supervisor ownership and reservation preserved")
        return 0
    if action == "_inspect":
        from inspection import inspect_argv

        parser = argparse.ArgumentParser(prog="memcap _inspect")
        parser.add_argument("--session-key", default="")
        parser.add_argument("command", nargs=argparse.REMAINDER)
        args = parser.parse_args(sys.argv[2:])
        argv = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not argv:
            parser.error("inspection command required")
        return inspect_argv(
            argv,
            lambda words: scheduler.run(words, wait=None, session_key=args.session_key),
            args.session_key,
        )
    if action == "hook":
        try:
            agent = sys.argv[2] if len(sys.argv) > 2 else "codex"
            if agent not in {"codex", "claude"}:
                raise ValueError("unsupported agent")
            payload = json.load(sys.stdin)
            started_hook = time.monotonic()
            result = hook_response(payload, str(ROOT / "bin/memcap"), agent)
            from analytics_events import emit, hook_fields
            detail = result.get("hookSpecificOutput", {})
            updated = detail.get("updatedInput", {}).get("command", "")
            try:
                wrapper = shlex.split(updated)
            except ValueError:
                wrapper = []
            action_name = wrapper[1] if len(wrapper) > 1 and wrapper[0] in {'memcap', str(ROOT / 'bin/memcap')} else ''
            route = 'denied' if detail.get('permissionDecision') == 'deny' else {'_inspect': 'guarded', 'run': 'managed'}.get(action_name, 'native')
            from command_trace import hook as trace_hook
            trace_hook(payload, route)
            emit("route", **hook_fields(payload), agent=agent, route=route,
                 guard_ms=(time.monotonic() - started_hook) * 1000)
        except (ValueError, TypeError, AttributeError):
            result = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "memcap could not parse the launch request",
                }
            }
        if result:
            print(json.dumps(result))
        return 0
    if action == "wait":
        parser = argparse.ArgumentParser(prog="memcap wait")
        parser.add_argument("job_id", nargs="?")
        parser.add_argument(
            "--session",
            action="store_true",
            help="wait on finite jobs owned by this agent process; no lookup pipeline",
        )
        parser.add_argument("--timeout", type=float, default=60)
        parser.add_argument("--session-key", default=None, help=argparse.SUPPRESS)
        args = parser.parse_args(sys.argv[2:])
        if bool(args.job_id) == args.session:
            parser.error("choose JOB_ID or --session")
        return scheduler.wait_for(
            "--session" if args.session else args.job_id, args.timeout, args.session_key
        )
    if action == "authorize":
        ids = scheduler.authorize_cancel(
            sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None
        )
        print(" ".join(ids))
        return 0 if ids else 1
    if action == "queue":
        jobs = scheduler.status()
        if sys.argv[2:] == ["--summary"]:
            running = sum(j["status"] == "running" and not j["resource"] for j in jobs)
            resources = sum(
                j["status"] == "running" and bool(j["resource"]) for j in jobs
            )
            waiting = sum(j["status"] == "waiting" for j in jobs)
            legacy = sum(j.get("scheduler_version", 1) < 2 for j in jobs)
            print(
                f"{running} running / {scheduler.max_jobs} slots, {waiting} queued, {resources} resources; "
                f"policy={scheduler.policy}, {legacy} legacy supervisors (drain existing tasks)"
            )
        elif sys.argv[2:] == ["--json"]:
            print(json.dumps(jobs))
        elif sys.argv[2:]:
            raise QueueError("usage: memcap queue [--json]")
        elif not jobs:
            print("No queued or running jobs.")
        else:
            for job in jobs:
                state = "watchdog" if job.get("recovery_active") else "orphaned" if job.get("orphaned") else job["status"]
                kind = "resource" if job["resource"] else "job"
                age = f"{max(0, int(time.time() - job['measured_at']))}s ago" if "measured_at" in job else "age unknown"
                measured = (f"{job['measured_kb'] / GIB:.3f} GiB ({age})"
                            if "measured_kb" in job else "unknown")
                print(
                    f"{job['id'][:8]}  {state:9s} {kind:8s} lane={scheduler.lane(job)} requested={job['memory_kb'] / GIB:g} GiB "
                    f"reserved={job.get('reservation_kb', job['memory_kb']) / GIB:.3f} GiB "
                    f"measured={measured} cleanup={job.get('cleanup_blocker', 'not-observed')} "
                    f"pinned={bool(job.get('pinned'))} pid={job['group']}  {job['cwd']}"
                )
        return 0
    parser = argparse.ArgumentParser(prog="memcap run")
    parser.add_argument("--classification-code", type=int, choices=range(6), default=0, help=argparse.SUPPRESS)
    parser.add_argument("--resource", default="")
    parser.add_argument("--memory", type=float)
    parser.add_argument(
        "--wait", type=float, default=env_number("QUEUE_WAIT_SEC", WAIT_SECONDS)
    )
    parser.add_argument("--cwd")
    parser.add_argument("--session-key", default="", help=argparse.SUPPRESS)
    parser.add_argument("--analytics-operation", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--analytics-turn", default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--wait-forever",
        action="store_true",
        help="poll until admitted or cancelled; no queue deadline",
    )
    parser.add_argument("--shell", default="/bin/bash")
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--shell-command")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(sys.argv[2:])
    scheduler.classification_code = args.classification_code
    scheduler.analytics_metadata = {k: v for k, v in dict(operation=args.analytics_operation, turn=args.analytics_turn).items() if v}
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.shell_command is not None:
        if not (directory.parent / "paused").is_file() and polling_loop(
            args.shell_command
        ):
            raise QueueError(POLL_GUIDANCE)
        if argv:
            raise QueueError("choose a command or --shell-command, not both")
        command = args.shell_command
        argv = [args.shell, "-lc" if args.login else "-c", command]
    if not argv:
        parser.error("a command is required after --")
    if args.memory is not None and (not math.isfinite(args.memory) or args.memory <= 0):
        parser.error("--memory must be positive and finite (GB)")
    if (directory.parent / "paused").is_file():
        return scheduler.run(argv, args.cwd)
    if (
        args.session_key
        and args.shell_command is not None
        and args.memory is None
        and not args.resource
    ):
        from demand_policy import classify
        # A cached agent wrapper may have been generated before an upgrade.
        # Recheck its original command using today's classifier before reserving.
        # Explicit user reservations/resources retain their requested policy.
        if classify(args.shell_command, args.cwd).kind == 'light':
            os.chdir(Path(args.cwd or os.getcwd()).resolve(strict=True))
            os.execvpe(argv[0], argv, os.environ)
    return scheduler.run(
        argv,
        args.cwd,
        args.resource,
        args.memory,
        None if args.wait_forever else args.wait,
        args.session_key,
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (QueueError, OSError, subprocess.SubprocessError) as error:
        print(f"memcap queue: {error}", file=sys.stderr)
        sys.exit(75)
