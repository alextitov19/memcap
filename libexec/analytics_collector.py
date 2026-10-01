"""One private recorder. No scheduler locks, process signals, or policy mutation."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import resource
import selectors
import shutil
import signal
import socket
import sqlite3
import subprocess
import time

from analytics_events import Producer, make_event, PACKET_LIMIT
from analytics_store import Store, private_directory


def probe(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=2)
        return result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def host_sample(directory, previous):
    """Reuse recent admission observations; otherwise only host aggregate probes."""
    from scheduler_metrics import vm_sample, rate
    now = time.monotonic()
    sample = {}
    try:
        path = directory.parent / "queue/sample.json"
        if path.stat().st_size < 4 * 1024 * 1024:
            cached = json.loads(path.read_text()).get("sample", {})
            age = now - cached.get("monotonic", 0)
            if 0 <= age <= 10 and not cached.get("fault"):
                allowed = ("pressure", "available_kb", "tracked_kb", "swap_in_kbps", "swap_out_kbps", "compressor_kb")
                sample = {**{k: cached[k] for k in allowed if k in cached}, "sample_age_ms": age * 1000}
    except (OSError, ValueError, TypeError):
        pass
    vm = vm_sample()
    for counter, field in (("swapins", "swap_in_kbps"), ("swapouts", "swap_out_kbps")):
        value = rate(previous, vm, counter)
        if value is not None:
            sample[field] = value
    for field in ("compressor_kb", "wired_kb"):
        if field in vm:
            sample[field] = vm[field]
    if "pressure" not in sample:
        pressure = probe(["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip()
        if pressure in {"1", "2", "4"}:
            sample["pressure"] = int(pressure)
    memory = probe(["/usr/sbin/sysctl", "-n", "hw.memsize"]).strip()
    if memory.isdigit():
        sample["physical_memory_kb"] = int(memory) // 1024
    if "available_kb" not in sample:
        free = re.search(r"System-wide memory free percentage:\s*(\d+)%", probe(["/usr/bin/memory_pressure", "-Q"]))
        if memory.isdigit() and free and 0 <= int(free[1]) <= 100:
            sample["available_kb"] = int(memory) * int(free[1]) / 100 / 1024
    sample["measurement_fault"] = int("pressure" not in sample or "available_kb" not in sample)
    sample["disk_free_kb"] = shutil.disk_usage(directory).free // 1024
    return sample, vm


def kernel_zone_sample():
    """Two fixed allocation counters, not process attribution or resident bytes.

    Unprivileged zprint can redact current size and underflow fragmentation.
    Use only validated element size/count; never persist its raw output.
    """
    result = {}
    names = {"data.kalloc.1024": "kernel_data_1024_inuse_kb",
             "data_shared.kalloc.1024": "kernel_data_shared_1024_inuse_kb"}
    for line in probe(["/usr/bin/zprint", "-t"]).splitlines():
        parts = line.split()
        if len(parts) >= 7 and parts[0] in names and parts[1] == "1024" and parts[6].isdigit():
            count = int(parts[6])
            if count <= 2**40:
                result[names[parts[0]]] = count
    return result


def collect(directory, port=43190):
    directory = Path(directory)
    private_directory(directory)
    if not (directory / "enabled").is_file():
        return 0
    lock = os.open(directory / "collector.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock)
        raise ValueError("analytics recorder is already running")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
    socket_path = directory / "events.sock"
    if socket_path.exists():
        if socket_path.is_symlink() or socket_path.stat().st_uid != os.getuid():
            raise ValueError("unsafe analytics socket")
        socket_path.unlink()  # Only under the independent collector singleton lock.
    sock.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    boot_raw = probe(["/usr/sbin/sysctl", "-n", "kern.boottime"])
    boot = hashlib.sha256(boot_raw.encode()).hexdigest()[:32] if boot_raw else "0" * 32
    (directory / "boot").write_text(boot)
    os.chmod(directory / "boot", 0o600)
    source = Producer(directory)
    inbox = queue.Queue(maxsize=8)
    http = thread = None
    token = (directory / "token").read_text().strip()
    from analytics_otlp import server
    try:
        http, thread = server(port, token, inbox)
    except OSError:
        # Hooks and job recording still function if optional OTLP cannot bind.
        pass
    stopped = False

    def stop(_sig, _frame):
        nonlocal stopped
        stopped = True

    old = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    selector = selectors.DefaultSelector()
    selector.register(sock, selectors.EVENT_READ)
    seq = 0
    errors = 0
    last_sample = last_maintain = last_heartbeat = last_kernel = 0
    cached_health = {}
    writable = True
    last_activity = 0
    prior_vm = {}
    last_tick = time.monotonic()
    started_cpu = time.process_time()
    received = 0
    from native_observer import Observer
    native_observer = Observer(directory.parent / 'native')
    with Store(directory) as store:
        def record(event, fields):
            nonlocal seq
            seq += 1
            row = make_event(event, {"source": "collector", **fields,
                             "paused": int((directory.parent / "paused").is_file())},
                             source.key, source.producer, seq, build=source.build,
                             policy=source.policy, boot=boot)
            store.insert(row)

        try:
            while not stopped and (directory / "enabled").is_file():
                now = time.monotonic()
                for _key, _mask in selector.select(.5):
                    for _ in range(2000):
                        try:
                            packet = sock.recv(PACKET_LIMIT + 1)
                        except BlockingIOError:
                            break
                        if len(packet) > PACKET_LIMIT or not writable:
                            errors += 1
                            continue
                        try:
                            row = json.loads(packet)
                            if store.insert(row):
                                received += 1
                                if row.get("event") in {"hook", "route", "queued", "admitted"}:
                                    last_activity = now
                        except sqlite3.Error:
                            errors += 1
                            store.db.rollback()
                            last_maintain = 0
                            break
                        except (ValueError, TypeError, RecursionError):
                            errors += 1
                try:
                    # Recover page pressure before generating more writes. A
                    # failed insertion must not starve retention indefinitely.
                    if last_maintain == 0 or now - last_maintain >= 60:
                        store.maintain()
                        last_maintain = now
                    for observation in native_observer.tick():
                        record('native_memory', observation)
                    for _ in range(8):
                        try:
                            batch = inbox.get_nowait()
                        except queue.Empty:
                            break
                        if writable:
                            for event, fields in batch:
                                record(event, fields)
                            last_activity = now
                    if now - last_sample >= (10 if now - last_activity < 300 else 60) and writable:
                        sample, prior_vm = host_sample(directory, prior_vm)
                        if now - last_kernel >= 60:
                            sample.update(kernel_zone_sample())
                            last_kernel = now
                        record("sample", sample)
                        usage = resource.getrusage(resource.RUSAGE_SELF)
                        record("observer", dict(observer_cpu_ms=(time.process_time() - started_cpu) * 1000,
                                                observer_peak_kb=usage.ru_maxrss / (1024 if os.uname().sysname == "Darwin" else 1),
                                                wake_delay_ms=max(0, now - last_tick - .5) * 1000,
                                                database_errors=errors))
                        last_sample = now
                    store.db.commit()
                except (sqlite3.Error, OSError):
                    errors += 1
                    store.db.rollback()
                    last_maintain = 0
                try:
                    # Health reporting remains reachable after a failed write.
                    if now - last_heartbeat >= 5:
                        cached_health = store.health()
                        writable = cached_health["disk_bytes"] < store.limit * .85
                        heartbeat = dict(at=time.time(), received=received, errors=errors,
                                         build=source.build, otlp_listening=http is not None,
                                         port=http.server_port if http else None,
                                         cpu_ms=(time.process_time() - started_cpu) * 1000,
                                         storage=cached_health, paused=(directory.parent / "paused").is_file())
                        temp = directory / ".heartbeat"
                        temp.write_text(json.dumps(heartbeat))
                        os.chmod(temp, 0o600)
                        os.replace(temp, directory / "heartbeat.json")
                        last_heartbeat = now
                except (sqlite3.Error, OSError):
                    errors += 1
                last_tick = time.monotonic()
        finally:
            if http:
                http.shutdown()
                http.server_close()
                thread.join(timeout=3)
            for sig, handler in old.items():
                signal.signal(sig, handler)
            selector.close()
            sock.close()
            source.close()
            socket_path.unlink(missing_ok=True)
            os.close(lock)
    return 0
