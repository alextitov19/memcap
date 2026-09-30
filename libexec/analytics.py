"""Local performance analytics CLI. Never changes admission policy or consent."""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from analytics_events import emit, root, opaque, Producer, make_event
from analytics_reports import summarize, compare, html_report, text_report
from analytics_store import read_rows


def status(directory):
    rows, health = read_rows(directory, time.time() - 86400)
    try:
        heartbeat = json.loads((directory / "heartbeat.json").read_text())
    except (OSError, ValueError):
        heartbeat = {}
    return dict(enabled=(directory / "enabled").is_file(),
                recorder_live=0 <= time.time() - heartbeat.get("at", 0) < 15,
                enforcement_paused=(directory.parent / "paused").is_file(),
                heartbeat=heartbeat, observed_events=len(rows), storage=health,
                capabilities={"current_sessions": "existing feedback hooks record at next invocation; no hook command/trust changes",
                              "claude_native": "configured startup environment required; already-running agents may need restart",
                              "codex": "hooks only; API/token accounting unknown; no transcript scraping"},
                native_claude_events=sum(r.get("source") == "claude" for r in rows),
                recent_sessions=list(dict.fromkeys(r["session"] for r in reversed(rows) if r.get("session")))[:20])


def import_legacy(directory):
    source = Producer(directory)
    if not source.key:
        raise ValueError("enable analytics before importing")
    count = dropped = 0
    try:
        for name in ("events.previous.jsonl", "events.jsonl"):
            path = directory.parent / "queue" / name
            if not path.exists() or path.is_symlink() or path.stat().st_size > 17 * 1024 * 1024:
                continue
            with path.open() as stream:
                for line in stream:
                    if len(line) > 8192:
                        continue
                    try:
                        row = json.loads(line)
                        event = row.pop("event")
                        wall = float(row.pop("timestamp"))
                        ref = row.pop("job_ref", None)
                        fields = {**row, "legacy": 1, "source": "legacy"}
                        if ref is not None:
                            fields["job"] = str(ref)
                        # Stable content ID deduplicates imports across rotations.
                        ident = hashlib.sha256(line.encode()).hexdigest()[:32]
                        packet = make_event(event, fields, source.key, ident, 1,
                                            build="0" * 64, policy="0" * 64, boot="0" * 32,
                                            wall=wall, mono=0)
                        if packet:
                            source.socket.sendto(json.dumps(packet).encode(), str(directory / "events.sock"))
                            count += 1
                    except (ValueError, KeyError, TypeError):
                        continue
                    except OSError as exc:
                        if exc.errno not in {errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOBUFS}:
                            raise
                        # Import is an explicit offline operation, not a producer
                        # hook. Pace bounded local copies without retrying work.
                        time.sleep(.02)
                        try:
                            source.socket.sendto(json.dumps(packet).encode(), str(directory / "events.sock"))
                            count += 1
                        except OSError as retry_error:
                            if retry_error.errno not in {errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOBUFS}:
                                raise
                            dropped += 1
    finally:
        source.close()
    return dict(submitted=count, dropped=dropped, delivery="best_effort; repeat import safely deduplicates", historical_coverage="legacy jobs only; native/paused/session/policy unknown")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="memcap analytics")
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("status", "doctor", "today", "builds"):
        p = sub.add_parser(action)
        p.add_argument("--json", action="store_true")
    p = sub.add_parser("trends")
    p.add_argument("--days", type=int, choices=range(1, 366), default=30)
    p = sub.add_parser("explain")
    p.add_argument("identity")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("compare")
    p.add_argument("baseline")
    p.add_argument("candidate")
    p = sub.add_parser("html")
    p.add_argument("output", type=Path)
    p = sub.add_parser("work")
    p.add_argument("phase", choices=("start", "accept", "reject", "abandon"))
    p.add_argument("identity", help="local identifier; only its keyed hash is stored")
    p.add_argument("--session", default=None)
    p = sub.add_parser("enable")
    p.add_argument("--claude", action="store_true")
    p.add_argument("--claude-dir", action="append", default=[])
    p.add_argument("--service", action="store_true")
    p.add_argument("--port", type=int, default=43190)
    sub.add_parser("disable")
    p = sub.add_parser("_collect")
    p.add_argument("--port", type=int, default=43190)
    sub.add_parser("import-legacy")
    p = sub.add_parser("_signal")
    p.add_argument("scope")
    p.add_argument("signal", type=int)
    p.add_argument("result", type=int)
    p.add_argument("count", type=int)
    p.add_argument("identity")
    p = sub.add_parser("benchmark")
    p.add_argument("--repetitions", type=int, default=10)
    p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    directory = root()
    if args.action == "_signal":
        emit("action", source="enforcement", scope=args.scope, signal=args.signal,
             signal_result=args.result, target_count=args.count, action=args.identity)
        return 0
    if args.action == "_collect":
        from analytics_collector import collect
        return collect(directory, args.port)
    if args.action == "enable":
        from analytics_install import initialize, configure_profiles, install_service
        initialize(directory)
        profiles = [Path(p).expanduser() for p in args.claude_dir]
        if args.claude:
            profiles += [p for p in (Path.home() / ".claude", Path.home() / ".claude-personal") if p.is_dir()]
        result = dict(enabled=True, enforcement_unchanged=True)
        if profiles:
            result["profiles"] = configure_profiles(list(dict.fromkeys(profiles)), (directory / "token").read_text().strip(), args.port)
        if args.service:
            executable = Path(__file__).absolute().parents[1] / "bin/memcap"
            for prefix in (Path("/opt/homebrew"), Path("/usr/local")):
                stable = prefix / "opt/memcap/bin/memcap"
                if stable.exists() and stable.resolve() == executable.resolve():
                    executable = stable
                    break
            result["service"] = install_service(directory, executable, args.port)
        print(json.dumps({k: v for k, v in result.items() if k != "observations"}, indent=2))
        return 0
    if args.action == "disable":
        (directory / "enabled").unlink(missing_ok=True)
        print("Analytics disabled. Enforcement and existing history are unchanged.")
        return 0
    if args.action == "import-legacy":
        print(json.dumps(import_legacy(directory), indent=2))
        return 0
    if args.action == "benchmark":
        from analytics_benchmark import benchmark
        result = benchmark(args.repetitions)
        from integrate import atomic_write
        atomic_write(args.output, (json.dumps(result, indent=2) + "\n").encode(), 0o600)
        print(json.dumps({k: v for k, v in result.items() if k != "observations"}, indent=2))
        return 0
    if args.action == "work":
        outcome = {"start": "started", "accept": "accepted", "reject": "rejected", "abandon": "abandoned"}[args.phase]
        fields = dict(work=args.identity, outcome=outcome, source="owner")
        if args.session:
            fields["session"] = hashlib.sha256(args.session.encode()).hexdigest()
        if not emit("work", **fields):
            raise ValueError("work marker not delivered; recorder unavailable")
        print("Work marker submitted locally; use analytics today to inspect outcomes.")
        return 0
    if args.action in {"status", "doctor"}:
        print(json.dumps(status(directory), indent=2))
        return 0
    if args.action == "trends":
        from analytics_store import read_rollups
        print(json.dumps(dict(histograms=read_rollups(directory, args.days),
                              interpretation="Merge histogram counts, never average percentiles. Pressure metrics sum observed milliseconds; unobserved intervals are unknown. Retention is bounded by time and bytes."), indent=2))
        return 0
    rows, health = read_rows(directory)
    if args.action == "today":
        local = time.localtime()
        midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
        rows = [r for r in rows if r["wall"] >= midnight]
        result = summarize(rows, health)
        if not args.json:
            print(text_report(result))
            return 0
    elif args.action == "builds":
        groups = {}
        for row in rows:
            cohort = row["build"] + ":" + row["policy"]
            info = groups.setdefault(cohort, dict(build=row["build"], policy=row["policy"], events=0, first=row["wall"], last=row["wall"]))
            info["events"] += 1
            info["last"] = row["wall"]
        result = list(groups.values())
    elif args.action == "explain":
        ident = args.identity
        if len(ident) < 8:
            raise ValueError("use at least eight characters of an analytics session/job ID")
        matches = {r[k] for r in rows for k in ("session", "job", "work") if isinstance(r.get(k), str) and r[k].startswith(ident)}
        if len(matches) > 1:
            raise ValueError("ambiguous prefix; use the full identifier")
        selected = [r for r in rows if any(r.get(k) in matches for k in ("session", "job", "work"))]
        if selected:
            first, last = min(r["wall"] for r in selected), max(r["wall"] for r in selected)
            selected_ids = {(r["producer"], r["seq"]) for r in selected}
            selected += [r for r in rows if r["event"] in {"sample", "observer"} and first <= r["wall"] <= last
                         and (r["producer"], r["seq"]) not in selected_ids]
            selected.sort(key=lambda r: r["wall"])
        result = dict(summary=summarize(selected, health), timeline=selected)
    elif args.action == "compare":
        if Path(args.baseline).is_file() and Path(args.candidate).is_file():
            from analytics_benchmark import compare_manifests
            def manifest(path):
                if Path(path).stat().st_size > 5 * 1024 * 1024:
                    raise ValueError("benchmark manifest exceeds 5 MiB")
                value = json.loads(Path(path).read_text())
                if not isinstance(value, dict) or value.get("schema") != 1:
                    raise ValueError("unsupported benchmark manifest")
                return value
            result = compare_manifests(manifest(args.baseline), manifest(args.candidate))
            print(json.dumps(result, indent=2))
            return 0
        def choose(selector):
            if len(selector) < 8:
                raise ValueError("use at least eight characters of build[:policy] from analytics builds")
            parts = selector.split(":", 1)
            return [r for r in rows if r["build"].startswith(parts[0]) and (len(parts) == 1 or r["policy"].startswith(parts[1]))]
        result = compare(choose(args.baseline), choose(args.candidate))
    else:
        from integrate import atomic_write
        atomic_write(args.output, html_report(summarize(rows, health), rows).encode(), 0o600)
        print(f"Local report written: {args.output}")
        return 0
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print(f"memcap analytics: {exc}", file=sys.stderr)
        sys.exit(1)
