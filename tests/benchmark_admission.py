"""Controlled policy throughput experiment, not a hardware capacity benchmark.

Real owned subprocesses allocate 16 MiB and finish bounded hash work plus an
8-second external-service wait. Host observations are deterministic fixtures.
Run manually through the live queue; never call enforcement or touch Docker.
"""

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

DRIVER = """import sys,time
sys.path.insert(0,sys.argv[1])
from scheduler import Scheduler
GIB=1048576
def sample():
 return dict(cap_kb=20*GIB,tracked_kb=17*GIB,available_kb=int(4.5*GIB),pressure=2,fault=False,footprints={},tracked_pids=[],boot_id='fixture',monotonic=time.monotonic())
mode=sys.argv[3]
s=Scheduler(sys.argv[2],sampler=sample,policy=mode,max_pressure='yellow',memory_gb=(1 if mode=='adaptive' else 2),headroom_gb=2,max_jobs=(12 if mode=='adaptive' else 8),workers=(8 if mode=='adaptive' else 2),poll=.1)
code="import hashlib,time;data=bytearray(16*1024*1024);hashlib.sha256(data).hexdigest();time.sleep(8)"
raise SystemExit(s.run([sys.executable,'-c',code],session_key=sys.argv[4],wait=100))
"""


def trial(mode):
    with tempfile.TemporaryDirectory(prefix="memcap-throughput-") as tmp:
        directory = Path(tmp) / "queue"
        started = time.monotonic()
        children = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    DRIVER,
                    str(Path(__file__).resolve().parents[1] / "libexec"),
                    str(directory),
                    mode,
                    str(i),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for i in range(5)
        ]
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=110)
            if child.returncode:
                raise RuntimeError((child.returncode, stdout, stderr))
            results.append(child.returncode)
        elapsed = time.monotonic() - started
        events = [
            json.loads(line)
            for line in (directory / "events.jsonl").read_text().splitlines()
        ]
        waits = sorted(
            e["queue_wait_ms"] / 1000 for e in events if e["event"] == "admitted"
        )
        running = peak = 0
        for e in events:
            running += (e["event"] == "admitted") - (e["event"] == "completed")
            peak = max(peak, running)
        return dict(
            mode=mode,
            completed=len(results),
            seconds=round(elapsed, 3),
            p50_wait=statistics.median(waits),
            p95_wait=max(waits),
            peak_concurrency=peak,
            wait_turns=0,
            fixture_pressure="yellow",
            real_memory_per_child_mib=16,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", type=int, default=3)
    args = parser.parse_args()
    for i in range(args.pairs):
        for mode in ("strict", "adaptive") if i % 2 == 0 else ("adaptive", "strict"):
            print(json.dumps(trial(mode)), flush=True)
