"""Bounded, deterministic memcap fixtures. No network, real workloads or signals."""
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import time

from analytics_events import Producer, build_digest
from analytics_reports import distribution

FIXTURES = (
    ("read", "cat fixture.txt", "light"),
    ("search", "rg needle fixture.txt", "light"),
    ("ssm", "aws --region=us-east-1 ssm get-parameter --name fixture --with-decryption", "light"),
    ("compound", "cat fixture.txt && wc -c fixture.txt", "light"),
    ("build", "npm run build", "job"),
    ("test", "python3 -m pytest", "job"),
    ("unknown", "python3 arbitrary.py", "light"),
)


def benchmark(repetitions=10):
    if not 5 <= repetitions <= 50:
        raise ValueError("benchmark repetitions must be between 5 and 50")
    from scheduler_policy import classify_shell
    root = Path(__file__).resolve().parents[1]
    rng = random.Random(941)
    observations = []
    producer_times = []
    feedback_times = []
    with tempfile.TemporaryDirectory(prefix="memcap-analytics-bench-") as tmp:
        sandbox = Path(tmp)
        (sandbox / "fixture.txt").write_text("needle\n")
        env = {**os.environ, "MEMCAP_ROOT": str(root), "HOME": tmp, "MEMCAP_CONFIG_HOME": str(sandbox / "config"),
               "MEMCAP_STATE_HOME": str(sandbox / "state"), "MC_DRY_RUN": "1", "MC_NO_TOP": "1", "MC_DOCKER_RUNTIME": "none"}
        analytics = sandbox / "state/memcap/analytics"
        analytics.mkdir(parents=True, mode=0o700)
        (analytics / "enabled").touch()
        (analytics / "key").write_bytes(b"x" * 32)
        source = Producer(analytics)
        try:
            for _ in range(repetitions * 100):
                began = time.perf_counter_ns()
                source.emit("hook", phase="PreToolUse", source="benchmark")
                producer_times.append((time.perf_counter_ns() - began) / 1e6)
        finally:
            source.close()
        for repetition in range(repetitions):
            fixtures = list(FIXTURES)
            rng.shuffle(fixtures)
            for name, command, expected in fixtures:
                # Classification fixtures are reviewed labels, not measured-memory
                # guesses. No fixture commands are launched here.
                kind, _ = classify_shell(command)
                payload = dict(hook_event_name="PreToolUse", session_id="benchmark",
                               tool_use_id=f"{repetition}-{name}", cwd=tmp, tool_name="Bash",
                               tool_input={"command": command}, permission_mode="bypassPermissions")
                modes = ["enabled", "paused"]
                rng.shuffle(modes)
                for mode in modes:
                    marker = sandbox / "state/memcap/paused"
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    if mode == "paused":
                        marker.touch()
                    else:
                        marker.unlink(missing_ok=True)
                    began = time.perf_counter_ns()
                    result = subprocess.run(['/bin/bash', str(root / "bin/memcap"), "queue-hook", "claude"],
                                            input=json.dumps(payload), text=True, capture_output=True,
                                            env=env, cwd=tmp, timeout=10)
                    observations.append(dict(family=name, repetition=repetition, mode=mode,
                                             duration_ms=(time.perf_counter_ns() - began) / 1e6,
                                             success=result.returncode == 0 and (kind == expected or (expected == "job" and kind != "light")),
                                             expected=expected, actual=kind))
                    if name == "read":
                        began = time.perf_counter_ns()
                        feedback = subprocess.run(['/bin/bash', str(root / "bin/memcap"), "feedback"],
                                                  input=json.dumps(payload), text=True, capture_output=True,
                                                  env=env, cwd=tmp, timeout=10)
                        feedback_times.append(dict(mode=mode, duration_ms=(time.perf_counter_ns() - began) / 1e6,
                                                   success=feedback.returncode == 0))
    enabled = [o["duration_ms"] for o in observations if o["mode"] == "enabled"]
    paused = [o["duration_ms"] for o in observations if o["mode"] == "paused"]
    return dict(schema=1, build=build_digest(), created_at=time.time(), repetitions=repetitions,
                workload_digest=hashlib.sha256(json.dumps(FIXTURES).encode()).hexdigest(),
                randomization="paired randomized order; seed 941", outcome="passed" if all(o["success"] for o in observations) else "failed",
                producer_absent_ms=distribution(producer_times), whole_queue_hook_ms=distribution(enabled),
                paused_queue_hook_ms=distribution(paused), observations=observations,
                whole_feedback_hook_ms=distribution([r["duration_ms"] for r in feedback_times]),
                feedback_successes=sum(r["success"] for r in feedback_times),
                limits="Own bounded classification/hook fixtures only; not end-to-end agent task timing, alternate-policy memory simulation, or accepted development work.")


def compare_manifests(left, right):
    from analytics_events import make_event
    from analytics_reports import compare
    if left.get("workload_digest") != right.get("workload_digest"):
        return dict(verdict="insufficient_evidence", reason="benchmark workloads differ", regression=False)
    def rows(manifest):
        result = []
        for i, item in enumerate(manifest.get("observations", [])[:10000]):
            if not isinstance(item, dict):
                continue
            family = item.get("family", "unknown")
            row = make_event("completed", dict(job=str(i), family=family,
                             runtime_ms=item.get("duration_ms"), queue_wait_ms=0, exit_code=0 if item.get("success") else 1,
                             source="benchmark", workload=str(manifest.get("workload_digest")) + ":" + str(item.get("mode")), cache_state="unknown",
                             paused=int(item.get("mode") == "paused")), b"fixture", "a" * 32, i,
                             build=manifest.get("build", "0" * 64), policy="0" * 64, boot="0" * 32)
            if row:
                result.append(row)
        return result
    result = compare(rows(left), rows(right))
    result["benchmark"] = True
    result["limits"] = "Paired hook fixtures; model/agent completion and future machine health are not measured. Compare across runs/days before certifying a change."
    return result
