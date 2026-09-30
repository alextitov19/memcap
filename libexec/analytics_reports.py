"""Descriptive analytics: unknown is not zero and association is not causation."""
import collections
import html
import json
import math
import statistics
import datetime
import random


def interval_union(intervals):
    total, end = 0, None
    for left, right in sorted(intervals):
        if right < left:
            continue
        total += max(0, right - max(left, end if end is not None else left))
        end = max(right, end if end is not None else right)
    return total


def distribution(values):
    values = sorted(values)
    if not values:
        return {"n": 0, "median": None, "p95": None, "max": None}
    return dict(n=len(values), median=statistics.median(values),
                p95=values[max(0, math.ceil(.95 * len(values)) - 1)] if len(values) >= 20 else None,
                max=values[-1], total=sum(values))


def duration(start, end):
    if not start or not end or start.get("boot") != end.get("boot") or start.get("boot") == "0" * 32:
        return None
    elapsed = end["mono"] - start["mono"]
    wall = end["wall"] - start["wall"]
    if elapsed < 0 or abs(elapsed - wall) > 2 or start.get("clock_uncertain") or end.get("clock_uncertain"):
        return None
    return elapsed * 1000


def job_rows(rows):
    jobs = {}
    for row in sorted(rows, key=lambda r: (r["wall"], r["seq"])):
        if row.get("job") and row["event"] in {"queued", "admitted", "completed", "cancelled", "stalled", "reservation", "claim"}:
            item = jobs.setdefault(row["job"], {"job": row["job"]})
            item[row["event"]] = row
    return list(jobs.values())


def summarize(rows, health=None):
    all_jobs = job_rows(rows)
    resources = [j for j in all_jobs if any(isinstance(v, dict) and v.get("persistent") for v in j.values())]
    jobs = [j for j in all_jobs if j not in resources]
    counts = collections.Counter(started=len(jobs), succeeded=0, failed=0, cancelled=0, unfinished=0)
    waits, runtimes, amplifications, exposures = [], [], [], collections.defaultdict(list)
    worst = []
    for job in jobs:
        end = job.get("completed") or job.get("cancelled")
        admission = job.get("admitted", {})
        if end:
            state = "failed" if end.get("outcome") in {"timeout", "failed"} else "cancelled" if end["event"] == "cancelled" else "succeeded" if end.get("exit_code") == 0 else "failed" if "exit_code" in end else "unfinished"
            counts[state] += 1
        else:
            counts["unfinished"] += 1
        wait = admission.get("queue_wait_ms", (end or {}).get("queue_wait_ms"))
        runtime = (end or {}).get("runtime_ms")
        if wait is not None:
            waits.append(wait)
            if admission and admission.get("boot") != "0" * 32:
                exposures[admission["boot"]].append((admission["mono"] - wait / 1000, admission["mono"]))
            worst.append(dict(job=job["job"], wait_ms=wait, runtime_ms=runtime,
                              family=admission.get("family", "unknown"), outcome=end.get("event") if end else "unfinished"))
        if runtime is not None:
            runtimes.append(runtime)
            if wait is not None and runtime >= 10:
                amplifications.append((runtime + wait) / runtime)
    hook_starts = {}
    hook_times = []
    sessions, turns = {}, {}
    for row in rows:
        if row.get("session"):
            sessions.setdefault(row["session"], []).append(row)
        if row.get("turn"):
            turns.setdefault((row.get("session"), row["turn"]), []).append(row)
        if row["event"] == "hook" and row.get("operation") and row.get("phase") == "PreToolUse":
            hook_starts[(row.get("session"), row["operation"])] = row
        if row["event"] == "hook" and row.get("phase") in {"PostToolUse", "PostToolUseFailure"}:
            elapsed = duration(hook_starts.get((row.get("session"), row.get("operation"))), row)
            if elapsed is not None:
                hook_times.append(elapsed)
    samples = sorted((r for r in rows if r["event"] == "sample"), key=lambda r: r["wall"])
    health_seconds, red, yellow, paging, paging_seconds = 0., 0., 0., 0., 0.
    for a, b in zip(samples, samples[1:]):
        interval = duration(a, b)
        if interval is None or interval > 90000 or a.get("measurement_fault"):
            continue
        seconds = interval / 1000
        if a.get("pressure") not in (1, 2, 4):
            continue
        health_seconds += seconds
        red += seconds if a["pressure"] == 4 else 0
        yellow += seconds if a["pressure"] == 2 else 0
        if "swap_in_kbps" in a and "swap_out_kbps" in a:
            paging_seconds += seconds
            paging += seconds if a["swap_in_kbps"] + a["swap_out_kbps"] >= 1024 else 0
    work_items = collections.defaultdict(list)
    for row in rows:
        if row["event"] == "work" and row.get("work"):
            work_items[row["work"]].append(row)
    work_outcomes = collections.Counter()
    accepted_times = []
    for group in work_items.values():
        ordered = sorted(group, key=lambda r: r["wall"])
        start = next((r for r in ordered if r.get("outcome") == "started"), None)
        end = ordered[-1]
        outcome = end.get("outcome", "unknown")
        work_outcomes["unfinished" if outcome == "started" else outcome] += 1
        elapsed = duration(start, end)
        if outcome == "accepted" and elapsed is not None:
            accepted_times.append(elapsed)
    turn_times, unfinished_turns = [], 0
    for group in turns.values():
        ordered = sorted((r for r in group if r["event"] == "hook" and r.get("source") == "hook"), key=lambda r: r["wall"])
        start = next((r for r in ordered if r.get("phase") == "UserPromptSubmit"), None)
        end = next((r for r in reversed(ordered) if r.get("phase") in {"Stop", "StopFailure"}), None)
        elapsed = duration(start, end)
        if elapsed is None:
            unfinished_turns += 1
        else:
            turn_times.append(elapsed)
    reservation_slack = 0.
    reservation_intervals = 0
    reservation_groups = collections.defaultdict(list)
    for row in rows:
        if row["event"] == "reservation" and row.get("job"):
            reservation_groups[row["job"]].append(row)
    for job in jobs:
        observations = sorted(reservation_groups[job["job"]], key=lambda r: r["wall"])
        for a, b in zip(observations, observations[1:]):
            elapsed = duration(a, b)
            if elapsed is not None and elapsed <= 5000 and a.get("measurement_complete") == 1 and "measured_kb" in a and "reservation_kb" in a:
                reservation_slack += max(0, a["reservation_kb"] - a["measured_kb"]) / 1048576 * elapsed / 60000
                reservation_intervals += 1
    producers = collections.defaultdict(set)
    drops = collections.defaultdict(int)
    for row in rows:
        producers[row["producer"]].add(row["seq"])
        drops[row["producer"]] = max(drops[row["producer"]], row.get("producer_dropped", 0))
    gaps = sum(max(v) - min(v) + 1 - len(v) for v in producers.values())
    route_counts = collections.Counter(r.get("route", "unknown") for r in rows if r["event"] == "route")
    return {
        "jobs": dict(counts), "queue_wait_ms": distribution(waits), "runtime_ms": distribution(runtimes),
        "persistent_resources": dict(observed=len(resources), reuse_claims=sum("claim" in j for j in resources)),
        "queue_amplification_min_runtime_10ms": distribution(amplifications),
        "job_wait_seconds": sum(waits) / 1000,
        "queue_exposure_seconds": sum(interval_union(v) for v in exposures.values()) if exposures else None,
        "completion_path_wait_seconds": None,
        "worst_waits": sorted(worst, key=lambda r: r["wait_ms"], reverse=True)[:10],
        "tool_elapsed_ms": distribution(hook_times),
        "hook_ms": distribution([r["hook_ms"] for r in rows if "hook_ms" in r]),
        "all_agent_hooks_ms": distribution([r["agent_hooks_ms"] for r in rows if "agent_hooks_ms" in r]),
        "sessions": len(sessions), "turns_with_explicit_ids": len(turns),
        "prompt_to_stop_attempt_ms": distribution(turn_times),
        "turns_without_measurable_endpoints": unfinished_turns,
        "work_items": dict(outcomes=dict(work_outcomes), accepted_elapsed_ms=distribution(accepted_times)),
        "reservations": dict(observed_unused_gib_minutes=reservation_slack if reservation_intervals else None,
                             complete_intervals=reservation_intervals,
                             interpretation="Observed allowance slack, including safety floors; not reclaimable memory or permission to release leases."),
        "stop_attempts": sum(r["event"] == "hook" and r.get("phase") == "Stop" for r in rows),
        "stop_blocks": sum(r.get("blocked", 0) for r in rows if r["event"] == "stop_wait"),
        "wait_calls": sum(r.get("family") == "wait" and r["event"] == "route" for r in rows),
        "feedback_bytes": sum(r.get("feedback_bytes", 0) for r in rows),
        "routes": dict(route_counts),
        "machine": dict(observed_seconds=health_seconds, red_seconds=red if health_seconds else None,
                        yellow_seconds=yellow if health_seconds else None,
                        paging_observed_seconds=paging_seconds,
                        paging_ge_1MiB_s_seconds=paging if paging_seconds else None,
                        available_kb=distribution([r["available_kb"] for r in samples if "available_kb" in r])),
        "native_api": dict(requests=sum(r["event"] == "api" for r in rows),
                           input_tokens=sum(r.get("input_tokens", 0) for r in rows) if any("input_tokens" in r for r in rows) else None,
                           output_tokens=sum(r.get("output_tokens", 0) for r in rows) if any("output_tokens" in r for r in rows) else None,
                           api_equivalent_usd=sum(r.get("api_cost_usd", 0) for r in rows) if any("api_cost_usd" in r for r in rows) else None),
        "observer": dict(cpu_ms=max((r.get("observer_cpu_ms", 0) for r in rows if r["event"] == "observer"), default=None),
                         peak_resident_kb=max((r.get("observer_peak_kb", 0) for r in rows if r["event"] == "observer"), default=None),
                         residency_is_not_footprint=True),
        "actions": [r for r in rows if r["event"] == "action"][-30:],
        "coverage": {**(health or {}), "events": len(rows), "observed_sequence_gaps": gaps,
                     "reported_drops": sum(drops.values()), "tail_loss": "unknown",
                     "legacy_events": sum(r.get("legacy", 0) for r in rows),
                     "matched_tool_spans": len(hook_times), "tool_starts_with_ids": len(hook_starts),
                     "unmatched_tool_starts": max(0, len(hook_starts) - len(hook_times)),
                     "mixed_policy_sessions": sum(len({(r["build"], r["policy"], r.get("paused")) for r in group}) > 1 for group in sessions.values())},
        "interpretation": "Observed delays, not counterfactual time saved. Stop is not acceptance. Yellow is context; unknown is not zero. Paused comparisons are observational.",
    }


def compare(baseline, candidate):
    def cohorts(rows):
        result = collections.defaultdict(list)
        for job in job_rows(rows):
            if any(isinstance(v, dict) and v.get("persistent") for v in job.values()):
                continue
            end = job.get("completed", {})
            row = {**job.get("admitted", {}), **end}
            if end.get("exit_code") == 0 and "runtime_ms" in row and "queue_wait_ms" in row:
                result[(row.get("family", "unknown"), row.get("workload"), row.get("model"), row.get("cache_state", "unknown"))].append(row["runtime_ms"] + row["queue_wait_ms"])
        return result
    left, right = cohorts(baseline), cohorts(candidate)
    matches = []
    for key in sorted(left.keys() & right.keys(), key=str):
        a, b = left[key], right[key]
        before, after = statistics.median(a), statistics.median(b)
        delta = after - before
        sufficient = min(len(a), len(b)) >= 5
        interval = None
        if sufficient:
            rng = random.Random(941)
            deltas = sorted(statistics.median(rng.choices(b, k=min(200, len(b)))) - statistics.median(rng.choices(a, k=min(200, len(a)))) for _ in range(200))
            interval = [deltas[5], deltas[194]]
        matches.append(dict(family=key[0], baseline_n=len(a), candidate_n=len(b), baseline_median_ms=before,
                            candidate_median_ms=after, delta_ms=delta,
                            exploratory_bootstrap_95pct_delta_ms=interval,
                            percent=100 * delta / before if before else None,
                            regression=sufficient and delta > 1000 and after > before * 1.2,
                            evidence="exploratory" if key[1] is None or key[3] == "unknown" else "matched_workload"))
    regression = any(m["regression"] for m in matches)
    return dict(verdict="regression_signal" if regression else "no_regression_signal" if any(min(m["baseline_n"], m["candidate_n"]) >= 5 for m in matches) else "insufficient_evidence",
                regression=regression, cohorts=matches, baseline=summarize(baseline), candidate=summarize(candidate),
                causal=False, uncertainty="Observational; unmatched workload, cache, model, concurrency and background conditions can confound differences. No improvement certification.")


def text_report(report):
    def number(value, unit=""):
        return "unknown" if value is None else f"{value:,.2f}{unit}"
    jobs = report["jobs"]
    machine = report["machine"]
    quality = report["coverage"]
    lines = ["memcap · local performance", "",
             f"Jobs: {jobs['succeeded']} succeeded, {jobs['failed']} failed, {jobs['cancelled']} cancelled, {jobs['unfinished']} unfinished.",
             f"Sessions observed: {report['sessions']}; matched tool spans: {quality['matched_tool_spans']}.",
             f"Job-wait total: {number(report['job_wait_seconds'], ' s')}; queue exposure: {number(report['queue_exposure_seconds'], ' s')}.",
             "Completion-path wait: unknown unless dependency links establish it.",
             f"Queue wait median: {number(report['queue_wait_ms']['median'], ' ms')}; p95: {number(report['queue_wait_ms']['p95'], ' ms')}; n={report['queue_wait_ms']['n']}.",
             f"Pressure observed: {number(machine['observed_seconds'], ' s')}; red: {number(machine['red_seconds'], ' s')}; yellow: {number(machine['yellow_seconds'], ' s')}.",
             f"Paging at least 1 MiB/s: {number(machine['paging_ge_1MiB_s_seconds'], ' s')}.",
             f"Stop attempts: {report['stop_attempts']}; blocks: {report['stop_blocks']}; wait calls: {report['wait_calls']}.",
             f"Routes: {json.dumps(report['routes'], sort_keys=True)}.",
             f"Explicit work outcomes: {json.dumps(report['work_items']['outcomes'], sort_keys=True)}.",
             f"Coverage: {quality['events']} events; {quality['legacy_events']} legacy; {quality['reported_drops']} reported drops; {quality['observed_sequence_gaps']} sequence gaps; tail loss unknown.",
             "", "Largest observed waits:"]
    for row in report["worst_waits"]:
        lines.append(f"  {row['job'][:12]}  {row['family']:8}  wait={row['wait_ms']/1000:.2f}s  runtime={number(row['runtime_ms'], ' ms')}  {row['outcome']}")
    if not report["worst_waits"]:
        lines.append("  None observed in retained data.")
    lines += ["", report["interpretation"]]
    return "\n".join(lines)


def html_report(report, rows):
    # Standalone escaped output: no scripts, external assets or command content.
    points = [r for r in rows if r["event"] == "sample" and "pressure" in r][-1000:]
    start = min((r["wall"] for r in points), default=0)
    span = max(1, max((r["wall"] for r in points), default=1) - start)
    circles = "".join(f'<circle cx="{20 + 920 * (r["wall"] - start) / span:.1f}" cy="{150 - r["pressure"] * 30}" r="3" fill="{ {1:"#137752",2:"#ad7b00",4:"#b00020"}.get(r["pressure"],"#777")}"/>' for r in points)
    def cell(row, key):
        value = row.get(key)
        if key == "wall" and value is not None:
            return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).strftime("%H:%M:%S")
        if key in {"session", "job"} and value:
            return value[:12]
        return "—" if value is None else str(round(value, 2)) if isinstance(value, float) else str(value)
    timeline = "".join('<tr>' + ''.join(f'<td>{html.escape(cell(r, k))}</td>' for k in ("wall", "event", "phase", "session", "job", "route", "queue_wait_ms")) + '</tr>' for r in rows[-1000:])
    def number(value, unit=""):
        return "Unknown" if value is None else f"{value:,.2f}{unit}"
    jobs = report["jobs"]
    metrics = (("Completed jobs", str(jobs["succeeded"])), ("Unfinished jobs", str(jobs["unfinished"])),
               ("Queue exposure", number(report["queue_exposure_seconds"], " s")),
               ("Red pressure observed", number(report["machine"]["red_seconds"], " s")))
    stats = ''.join('<div><dt>' + label + '</dt><dd>' + value + '</dd></div>' for label, value in metrics)
    coverage = report["coverage"]
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>memcap local performance</title>'
            '<style>:root{--ink:#17242b;--paper:#fff;--line:#d4dbe0;--muted:#4b5962;--panel:#f5f7f8}body{font:15px "Helvetica Neue",Helvetica,Arial,sans-serif;margin:32px;color:var(--ink);background:var(--paper);max-width:1200px}h1{font-size:28px;font-weight:600;margin-bottom:8px}h2{font-size:20px}p{color:var(--muted);line-height:1.5}dl{display:flex;flex-wrap:wrap;border-block:1px solid var(--line);padding:20px 0;gap:32px}dt{font-size:13px;color:var(--muted)}dd{font:26px Menlo,Consolas,monospace;margin:8px 0 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px Menlo,monospace}table{border-collapse:collapse;font:12px Menlo,monospace;width:100%}td,th{padding:8px;text-align:left;border-bottom:1px solid var(--line)}.scroll{overflow-x:auto}svg{width:100%;background:var(--panel)}summary{cursor:pointer;padding:12px 0}summary:focus-visible{outline:2px solid #1464b4}footer{margin-top:32px;font-size:12px;color:var(--muted)}@media(max-width:600px){body{margin:16px}dl{gap:20px}dd{font-size:22px}}@media(prefers-color-scheme:dark){:root{--ink:#edf1f3;--paper:#182025;--line:#49555e;--muted:#b6c2c9;--panel:#242f36}}</style>'
            '<h1>memcap local performance</h1><p>Developer delay and machine health, measured on this Mac.</p><dl>' + stats + '</dl>'
            '<p>' + f'{coverage["events"]:,} events · {coverage["reported_drops"]:,} reported drops · {coverage["matched_tool_spans"]:,} matched tool spans. Missing endpoints and delivery tail loss remain unknown.' + '</p>'
            '<h2>Memory pressure</h2><p>Green, yellow, and red observations across the retained window. Yellow is context. Gaps are unknown.</p>'
            '<svg viewBox="0 0 960 180" role="img" aria-label="Memory pressure observations">' + circles + '</svg>'
            '<details><summary>All measurements and coverage</summary><pre>' + html.escape(json.dumps(report, indent=2)) + '</pre></details>'
            '<h2>Recent timeline</h2><p>Latest 1,000 sanitized events. Identifiers are private hashes.</p><div class="scroll"><table><tr><th>UTC</th><th>Event</th><th>Phase</th><th>Session</th><th>Job</th><th>Route</th><th>Queue ms</th></tr>' + timeline + '</table></div>'
            '<footer>Local data only. Queue exposure is not counterfactual time saved. A Stop attempt does not establish accepted work.</footer></html>')
