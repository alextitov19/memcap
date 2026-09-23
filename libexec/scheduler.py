"""Per-user workload admission. No sockets, service, third-party packages or kills.

The lock covers both reservation and a gated child launch. Signals are requested
through the existing shell choke point, never sent by this module.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
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
from scheduler_metrics import append_event, shared_sample
from workload_estimates import fingerprint, record as record_estimate

from scheduler_policy import (
    hook_response,
    polling_loop,
    POLL_GUIDANCE,
    simple_words,
    light_shell,
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


def processes() -> dict:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,uid=,lstart=,stat="],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode or not result.stdout.strip():
        raise QueueError("process identities unavailable; no work admitted")
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
    def locked(self):
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
            deadline = time.monotonic() + 35
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise QueueError(
                            "queue lock unavailable; command was not admitted"
                        )
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
            if job["status"] != "running" or not job["members"]:
                continue
            stamp = sample.get("monotonic", time.monotonic())
            if stamp <= job.get("last_observation", job.get("start_monotonic", 0)):
                continue
            job["last_observation"] = stamp
            footprints = sample.get("footprints", {})
            if sample.get("fault") or not all(p in footprints for p in job["members"]):
                job["learning_incomplete"] = True
                continue
            measured = sum(footprints[p] for p in job["members"])
            job["observed_peak_kb"] = max(job.get("observed_peak_kb", 0), measured)
            job["sample_count"] = job.get("sample_count", 0) + 1

    def allocation(self, jobs):
        if self.policy == "strict":
            return self.workers
        contenders = max(1, len({self.session_bucket(j) for j in jobs}))
        pool = max(1, (os.cpu_count() or 2) - 2)
        occupied = sum(
            j.get("workers", self.workers)
            for j in jobs
            if j["status"] == "running" and not j["resource"]
        )
        return max(1, min(self.workers, pool // contenders, pool - occupied))

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
        key = fingerprint(
            dict(
                argv=argv,
                cwd=str(cwd),
                executable=executable,
                version=version,
                workers=workers,
                manifests=manifests,
                cache_state={
                    name: (cwd / name).exists()
                    for name in ("node_modules", "target", ".build", "build")
                },
            ),
            bytes.fromhex(secret),
        )
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
        row = data.get("estimates", {}).get(key, {})
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
                        job["members"] = members
                    job["orphaned"] = not owner_alive
                    live.append(job)
            except (KeyError, TypeError) as exc:
                raise QueueError(
                    "invalid job record; refusing to discard reservations"
                ) from exc
        data["jobs"] = live

    @staticmethod
    def reservation(job, sample, measured):
        # Automatic estimates cover the startup burst, then follow observed demand.
        # Explicit requests and uncertain/departed owners keep their full allowance.
        reserve = job["memory_kb"]
        if (
            job.get("elastic") is True
            and not job.get("orphaned")
            and job["members"]
            and all(p in sample.get("footprints", {}) for p in job["members"])
        ):
            peak = max(measured, job.get("observed_peak_kb", 0))
            job["observed_peak_kb"] = peak
            if 30 <= time.time() - job.get("started", time.time()):
                reserve = max(GIB // 2, int(peak * 1.25))
            else:
                reserve = max(reserve, int(peak * 1.25))
        return max(reserve, measured)

    def admissible(self, jobs, memory, resource, sample):
        for job in jobs:
            if job["status"] == "running":
                measured = sum(
                    sample.get("footprints", {}).get(p, 0) for p in job["members"]
                )
                job["reservation_kb"] = self.reservation(job, sample, measured)
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
        reasons = {
            "available": "capacity available",
            "budget": "combined budget reserved or in use",
            "headroom": "preserving host memory headroom",
            "slots": "all finite-job slots occupied",
            "pressure_or_measurement": "host pressure or unreliable memory measurement",
            "measurement": "invalid memory measurement",
            "paging": "sustained paging recovery",
            "stabilizing": "observing pressure recovery",
            "startup": "observing previous startup",
        }
        return decision["allow"], reasons[decision["reason"]]

    @staticmethod
    def session_bucket(job):
        # Older runners have no session key; keep their project's work together.
        return job.get("session_key") or "project:" + job.get("cwd", "")

    def next_waiter(self, jobs, resource, sample, turns=None, now=None):
        # One rotation across finite jobs and resources, shared under the queue lock.
        turns = turns or {}
        now = time.time() if now is None else now
        waiting = [j for j in jobs if j["status"] == "waiting"]
        waiting.sort(
            key=lambda j: (turns.get(self.session_bucket(j), 0), j.get("enqueued", now))
        )
        for job in waiting:
            # Give a large, aging request a chance to accumulate capacity. This
            # gates new admissions only; running jobs and reservations stay intact.
            age = now - job.get("enqueued", now)
            if (
                age >= 60
                and (self.policy == "strict" or age % 30 < 6)
                and any(j["status"] == "running" and not j["resource"] for j in jobs)
            ) or self.admissible(jobs, job["memory_kb"], job["resource"], sample)[0]:
                return job["id"]
        return None

    def record_turn(self, data, job):
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
        self, argv, cwd=None, resource="", memory_gb=None, wait=1800, session_key=""
    ):
        cwd = Path(cwd or os.getcwd()).resolve(strict=True)
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
        registered = False
        estimate_key = ""
        try:
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                old_handlers[sig] = signal.signal(sig, self.handle_signal)
            with self.locked() as data:
                table = processes()
                self.refresh(data, table)
                if (
                    self.nested(data, table)
                    or (self.directory.parent / "paused").exists()
                ):
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
                data["jobs"].append(
                    {
                        "id": ident,
                        "owner": os.getpid(),
                        "owner_start": owner["start"],
                        "group": 0,
                        "members": {},
                        "status": "waiting",
                        "resource": resource,
                        "cwd": str(cwd),
                        "memory_kb": memory,
                        "workers": workers,
                        "estimate_key": estimate_key,
                        "scheduler_version": 2,
                        "elastic": memory_gb is None,
                        "enqueued": time.time(),
                        "label": Path(argv[0]).name,
                        "cancel": False,
                        "session_key": session_key,
                    }
                )
                self.save(data)
                registered = True
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
                with self.locked() as data:
                    self.refresh(data, processes())
                    self.observe(data, sample)
                    job = next(j for j in data["jobs"] if j["id"] == ident)
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
                                data["jobs"], memory, resource, sample
                            )
                            if (
                                allowed
                                and self.next_waiter(
                                    data["jobs"],
                                    resource,
                                    sample,
                                    data.get("session_turns"),
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
                                "host pressure or unreliable memory measurement": "pressure_or_measurement",
                                "host pressure or unreliable pressure measurement": "pressure_or_measurement",
                                "invalid memory measurement": "measurement",
                                "sustained paging recovery": "paging",
                                "observing pressure recovery": "stabilizing",
                                "observing previous startup": "startup",
                                "waiting for earlier queued work": "fairness",
                                "waiting for another session’s turn or an aged job to fit": "fairness",
                            }.get(reason, "unknown"),
                        }
                        self.save(data)
                        if wait is not None and time.monotonic() - began >= wait:
                            print(
                                f"memcap: queue wait expired: {reason}; command not started."
                                + EXPIRED_GUIDANCE,
                                file=sys.stderr,
                            )
                            return 75
                        if time.monotonic() - last_notice > 60:
                            print(
                                f"memcap: queued {ident[:8]}: {reason}. Command has not started; "
                                f"keep polling this existing task once per minute (TaskOutput block=true timeout=60000 if available; otherwise memcap wait {ident[:8]} --timeout 60). "
                                "Continue independent work; do not submit duplicates or bypass memcap.",
                                file=sys.stderr,
                            )
                            last_notice = time.monotonic()
                if child is None:
                    time.sleep(self.poll)
            while True:
                result = child.poll()
                sample = (
                    self.measure()
                    if result is None or self.policy == "adaptive"
                    else {}
                )
                with self.locked() as data:
                    self.refresh(data, processes())
                    job = next(j for j in data["jobs"] if j["id"] == ident)
                    self.observe(data, sample)
                    job["cancel"] = bool(self.cancelled)
                    members = dict(job["members"])
                    if result is not None and not members:
                        key = job.get("estimate_key")
                        if (
                            key
                            and not self.cancelled
                            and result == 0
                            and not job.get("learning_incomplete")
                            and job.get("sample_count", 0) >= 2
                        ):
                            history = data.setdefault("estimates", {})
                            history[key] = record_estimate(
                                history.get(key, {"estimate_kb": job["memory_kb"]}),
                                job.get("observed_peak_kb", 0),
                                complete=True,
                            )
                            while len(history) > 256:
                                del history[next(iter(history))]
                        append_event(
                            self.directory,
                            dict(
                                event="completed",
                                exit_code=abs(result),
                                runtime_ms=int((time.time() - job["started"]) * 1000),
                                peak_kb=job.get("observed_peak_kb", 0),
                            ),
                        )
                    self.save(data)
                if self.cancelled:
                    subprocess.run(
                        [str(ROOT / "bin/memcap"), "_queue-cancel", ident],
                        check=False,
                        timeout=30,
                    )
                    return 128 + self.cancelled
                if result is not None and not members:
                    return result if result >= 0 else 128 - result
                time.sleep(self.poll)
        finally:
            if registered:
                # Keep running groups when interrupted or the supervisor fails;
                # another admission must not mistake lost supervision for free RAM.
                with self.locked() as data:
                    table = processes()
                    self.refresh(data, table)
                    data["jobs"] = [
                        j
                        for j in data["jobs"]
                        if j["id"] != ident
                        or (j["status"] == "running" and j["members"])
                    ]
                    self.save(data)
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)

    def launch(self, argv, cwd, job, data):
        workers = min(job.get("workers", self.workers), self.allocation(data["jobs"]))
        # A smaller final allocation is safe, but do not teach a larger-worker
        # profile using the smaller run's peak.
        if workers != job.get("workers", workers):
            job["estimate_key"] = ""
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
                    argv[:2] + [shlex.join(worker_argv(words, cwd, workers))] + argv[3:]
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
                    workers=workers,
                    request_kb=job["memory_kb"],
                    queue_wait_ms=int((time.time() - job["enqueued"]) * 1000),
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

    def wait_for(self, ident, timeout):
        if (
            (ident != "--session" and not re.fullmatch(r"[a-f0-9]{8,32}", ident))
            or not math.isfinite(timeout)
            or not 0 <= timeout <= 60
        ):
            raise QueueError("usage: memcap wait JOB_ID [--timeout SECONDS (0..60)]")
        deadline = time.monotonic() + timeout
        while True:
            try:
                if ident == "--session":
                    from idle_gc import Collector, process_table, owner, GCError

                    try:
                        table = process_table()
                        if not owner(str(os.getpid()), table):
                            raise QueueError(
                                "cannot identify this agent; use memcap wait JOB_ID --timeout 60"
                            )
                        matches = Collector(self.directory.parent).pending_jobs(
                            str(os.getpid()), table
                        )
                    except GCError as exc:
                        raise QueueError(
                            "queue state unreadable; task completion is unverified"
                        ) from exc
                else:
                    data = json.loads((self.directory / "jobs.json").read_text())
                    matches = [j for j in data["jobs"] if j["id"].startswith(ident)]
            except FileNotFoundError:
                matches = []
            except (ValueError, KeyError, TypeError) as exc:
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
                print(
                    f"memcap: {ident} pending ({live['jobs'][0]['status']}). Repeat memcap wait {ident} --timeout 60; no job or reservation was created."
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
        )
    if action == "hook":
        try:
            agent = sys.argv[2] if len(sys.argv) > 2 else "codex"
            if agent not in {"codex", "claude"}:
                raise ValueError("unsupported agent")
            result = hook_response(
                json.load(sys.stdin), str(ROOT / "bin/memcap"), agent
            )
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
        args = parser.parse_args(sys.argv[2:])
        if bool(args.job_id) == args.session:
            parser.error("choose JOB_ID or --session")
        return scheduler.wait_for(
            "--session" if args.session else args.job_id, args.timeout
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
                state = "orphaned" if job.get("orphaned") else job["status"]
                kind = "resource" if job["resource"] else "job"
                print(
                    f"{job['id'][:8]}  {state:9s} {kind:8s} {job['memory_kb'] / GIB:g} GB  pid={job['group']}  {job['cwd']}"
                )
        return 0
    parser = argparse.ArgumentParser(prog="memcap run")
    parser.add_argument("--resource", default="")
    parser.add_argument("--memory", type=float)
    parser.add_argument(
        "--wait", type=float, default=env_number("QUEUE_WAIT_SEC", 1800)
    )
    parser.add_argument("--cwd")
    parser.add_argument("--session-key", default="", help=argparse.SUPPRESS)
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
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.shell_command is not None:
        if polling_loop(args.shell_command):
            raise QueueError(POLL_GUIDANCE)
        if argv:
            raise QueueError("choose a command or --shell-command, not both")
        command = args.shell_command
        argv = [args.shell, "-lc" if args.login else "-c", command]
    if not argv:
        parser.error("a command is required after --")
    if args.memory is not None and (not math.isfinite(args.memory) or args.memory <= 0):
        parser.error("--memory must be positive and finite (GB)")
    if (
        args.session_key
        and args.shell_command is not None
        and args.memory is None
        and not args.resource
    ):
        from inspection import guarded_shell

        guarded = guarded_shell(
            args.shell_command, str(ROOT / "bin/memcap"), args.session_key
        )
        if guarded and args.shell in {"/bin/bash", "/bin/zsh", "/bin/sh"}:
            os.chdir(Path(args.cwd or os.getcwd()).resolve(strict=True))
            os.execvpe(
                args.shell,
                [args.shell, "-lc" if args.login else "-c", guarded],
                os.environ,
            )
    if (
        args.session_key
        and args.shell_command is not None
        and args.memory is None
        and not args.resource
        and args.shell in {"/bin/bash", "/bin/zsh", "/bin/sh"}
        and light_shell(args.shell_command)
    ):
        # A cached agent wrapper may have been generated before an upgrade.
        # Recheck its original command using today's classifier before reserving.
        # Explicit user reservations/resources retain their requested policy.
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
