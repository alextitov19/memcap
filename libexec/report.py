"""Opt-in public reports. Never upload raw logs, argv, paths or tool output."""

from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.parse import urlencode

REPO = "alextitov19/memcap"
KINDS = {
    "queue-lock": "Queue lock unavailable",
    "measurement": "Memory measurement unavailable",
    "integration": "Agent integration problem",
    "queue-stall": "Suspected queue progress problem",
    "unexpected-termination": "Suspected unexpected process termination",
    "missing-task-poll": "Agent cannot poll its existing task",
    "lightweight-queued": "Lightweight inspection delayed by the workload queue",
    "polling-overhead": "Queue polling disrupted productive agent work",
}
CONTEXTS = {
    "repository-search",
    "file-read",
    "status-check",
    "ssm-control",
    "remote-control",
    "wait-command",
    "stop-hook",
    "heavy-work",
}

# Shared by hook context and the integrated profile block so the two cannot drift.
PROTECTION_GUIDANCE = (
    "Never disable, raise, weaken or bypass memcap protection to unblock or speed up unrelated work, "
    "even in maximum/skip-permissions modes. Only the user may change memcap policy: when the user "
    "explicitly authorizes work on memcap itself, developing, testing, releasing and installing memcap "
    "is permitted, following its repository AGENTS.md sandboxing rules and running heavy work through "
    "the live queue. That authorization never extends to other tasks."
)

MEMORY_GUIDANCE = (
    "Docker's configured VM ceiling is a limit, not a reservation. VM charged footprint, "
    "resident memory and container usage are different measurements; never subtract container "
    "usage from VM footprint and promise that stopping Docker will free that difference. "
    "Accumulated swap is not current paging activity. Neither the footprint planning target "
    "nor swap used alone proves a simulator cannot boot. A user-paused queue is not an "
    "admission refusal: do not impose a manual hard cap from the displayed target. Continue "
    "authorized work under the user's selected state, using current pressure, available "
    "memory and device readiness as evidence. Never stop another project's stack or reset "
    "its device based on those totals, and never resume or change protection yourself. "
    "Green/yellow is not a guarantee against future pressure growth. "
    "If memcap wait reports no pending managed work, do not loop on it for an unmanaged "
    "native background task; use that task's completion notification and final output/status."
)

PERFORMANCE_GUIDANCE = (
    "You MUST report each observed memcap-caused productivity incident once, without a user reminder, "
    "even if the command eventually succeeds. Use memcap report lightweight-queued whenever ordinary "
    "repository searches (rg/grep), file reads or status checks get queued, or you switch tools to avoid "
    "that delay. Use memcap report queue-stall for excessive waits, starvation or blocked independent work; "
    "use memcap report polling-overhead for repetitive polling, output-file reads or stop-hook loops "
    "that waste time or agent usage. Include --wait-seconds N when elapsed wait is known (whole seconds); "
    "include --context with repository-search, file-read, status-check, ssm-control, remote-control, wait-command, stop-hook or heavy-work when known. Distinct contexts are retained separately instead of silently deduplicated together. "
    "omit it when unknown. Do not dismiss these incidents as normal capacity waiting or require a failed "
    "exit code or proven root cause. Capacity waiting for genuinely heavy work alone is not a defect. "
    "Report once per incident, not on every poll; reuse returned issue URLs and respect deduplication "
    "and existing consent. Memcap imposes no publication quota or retry cooldown. "
    "GitHub authentication, service errors or uncertain submissions can still defer publishing. "
    "Keep working; never retry deferred reports in a loop."
)
from report_metrics import capture, read_json, sanitize


def private_dir(path):
    if path.is_symlink():
        raise ValueError("symlinked report directory")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise ValueError("foreign report directory")
    path.chmod(0o700)


def write_private(path, text):
    private_dir(path.parent)
    fd, temp = tempfile.mkstemp(prefix=".report-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def issue_url(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"https://github\.com/alextitov19/memcap/issues/[1-9][0-9]*", value
    ):
        raise ValueError("invalid issue URL")
    return value


class GitHub:
    def __init__(self):
        self.deadline = None

    def request(self, method, path, payload=None):
        if self.deadline is None:
            self.deadline = time.monotonic() + 8
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("report deadline")
        argv = ["gh", "api", "--hostname", "github.com", "--method", method, path]
        if payload is not None:
            argv += ["--input", "-"]
        result = subprocess.run(
            argv,
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
            timeout=remaining,
            env={
                **os.environ,
                "GH_HOST": "github.com",
                "GH_PROMPT_DISABLED": "1",
                "GH_PAGER": "cat",
            },
        )
        if result.returncode:
            # stderr can contain account details or credentials; do not persist it.
            raise OSError("GitHub request failed")
        return json.loads(result.stdout)


class Reporter:
    def __init__(
        self, config, state, version, *, transport=None, now=time.time, snapshot=None
    ):
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError("invalid memcap version")
        self.consent_path = Path(config) / "reporting.json"
        self.directory = Path(state) / "reports"
        self.version, self.now = version, now
        self.transport = transport or GitHub()
        self.snapshot = snapshot or (lambda: capture(Path(state)))

    def consent(self, enabled):
        write_private(
            self.consent_path,
            json.dumps({"schema": 1, "enabled": bool(enabled), "repository": REPO})
            + "\n",
        )

    def enabled(self):
        try:
            data = read_json(self.consent_path)
            return (
                isinstance(data, dict)
                and type(data.get("schema")) is int
                and data.get("schema") == 1
                and data.get("enabled") is True
                and data.get("repository") == REPO
            )
        except (OSError, ValueError):
            return False

    @contextmanager
    def locked(self):
        private_dir(self.directory)
        fd = os.open(
            self.directory / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def ledger(self):
        path = self.directory / "ledger.json"
        try:
            data = read_json(path)
        except FileNotFoundError:
            return {"records": {}, "attempts": []}
        if (
            not isinstance(data, dict)
            or set(data) != {"records", "attempts"}
            or not isinstance(data["records"], dict)
            or not isinstance(data["attempts"], list)
        ):
            raise ValueError("invalid report ledger")
        if not all(isinstance(v, dict) for v in data["records"].values()):
            raise ValueError("invalid report records")
        if not all(
            type(t) in (int, float) and math.isfinite(t) and t >= 0
            for t in data["attempts"]
        ):
            raise ValueError("invalid report attempts")
        return data

    def save(self, ledger):
        write_private(self.directory / "ledger.json", json.dumps(ledger) + "\n")

    def report(self, kind, *, dry_run=False, wait_seconds=None, context=None):
        if kind not in KINDS:
            raise ValueError("unknown report category")
        if context is not None and context not in CONTEXTS:
            raise ValueError(
                "unknown report context; use a fixed context, never raw commands"
            )
        if wait_seconds is not None and (
            type(wait_seconds) is not int or not 0 <= wait_seconds <= 604800
        ):
            raise ValueError("wait seconds must be a whole number from 0 to 604800")
        try:
            with self.locked():
                return self._report(kind, dry_run, wait_seconds, context)
        except BlockingIOError:
            return {
                "status": "busy",
                "message": "Another report is being handled; continue work.",
            }

    def _report(self, kind, dry_run, wait_seconds=None, context=None):
        identity = f"1:{self.version}:{kind}" + (f":{context}" if context else "")
        fingerprint = hashlib.sha256(identity.encode()).hexdigest()[:20]
        marker = f"<!-- memcap-report:{fingerprint} -->"
        draft = self.directory / f"{fingerprint}.md"
        now = self.now()
        raw = self.snapshot()
        facts = sanitize(raw)
        if wait_seconds is not None:
            facts["agent_reported_wait_seconds"] = wait_seconds
        body = (
            f"{marker}\n## Agent-reported observation\n\n{KINDS[kind]}. This is a suspected problem, not a confirmed root cause.\n\n"
            f"Memcap version: {self.version}\nCategory: {kind}\nReport identifier: {fingerprint}\nObserved at: {datetime.fromtimestamp(now, timezone.utc).isoformat()}\n\n"
            f"Activity context: {context or 'unspecified'} (agent supplied, fixed vocabulary).\n\n"
            "Snapshot collected at report time, which may differ from failure time. Missing values are unknown. "
            "Queue counts, session counts and ages describe stored entries, not verified live jobs. "
            "Blockers describe each waiting entry's last recorded admission decision; old runners may have none. "
            "Registry/decision ages show evidence freshness, not how long a lock was held.\n\n"
            "Units: `_kb` = KiB; `_seconds` = seconds; load averages are multiplied by 1000 (not CPU percentages). "
            "Pressure: 1=green, 2=yellow, 4=red. Architecture: 1=arm64, 2=x86_64. "
            "`enforcement_paused`: 1=paused, 0=not paused (not proof the watchdog is running). "
            "`measurement_fault`: 1=independent reporting probe unavailable/unreliable; not proof of a scheduler outage. "
            "`measurement_probe_status`: 1=valid, 2=no response (timeout/error), 3=invalid/degraded. "
            "`queue_measurement_fault` describes the separate cached queue sample; check `sample_age_ms`. "
            "Simulator/browser memory is a subset of agent memory; do not add it twice. "
            "`*_requested_kb` sums original job requests, not effective reservations after adjustment. "
            "`running_reserved_kb` sums effective recorded allowances; `running_measured_kb` and "
            "`running_unused_reservations_kb` are included only when all running entries have complete measurements. "
            "`blocked_pressure_or_measurement` includes failed pressure/measurement checks. "
            "Unknown/missing decisions are counted in `blocked_unknown`.\n\n"
            "`agent_reported_wait_seconds`, when present, is supplied by the reporting agent; "
            "the reporter did not independently time it. A successful command can still suffer a latency regression.\n\n"
            f"```json\n{json.dumps(facts, sort_keys=True, indent=2)}\n```\n\n"
            "No source code, project paths, commands, process identities, raw logs or tool output are included. "
            "Maintainers may request a minimal reproduction after triage.\n"
        )
        write_private(draft, body)
        result = {"status": "draft", "draft": str(draft)}
        if dry_run or not self.enabled():
            return result
        try:
            ledger = self.ledger()
            row = ledger["records"].get(fingerprint, {})
            if row.get("url"):
                return {
                    **result,
                    "status": "deduplicated",
                    "url": issue_url(row["url"]),
                }
            last = row.get("last_attempt", 0)
            if type(last) not in (int, float) or not math.isfinite(last):
                raise ValueError("invalid report attempt")
            # Keep a bounded-time audit history, not a publication allowance.
            # Older installations' five attempts must not block upgraded agents.
            ledger["attempts"] = [t for t in ledger["attempts"] if now - t < 86400]
            row = {**row, "last_attempt": now}
            ledger["records"][fingerprint] = row
            self.save(ledger)
        except (OSError, ValueError, TypeError):
            return {**result, "status": "local-state-error"}
        try:
            query = urlencode(
                {
                    "q": f'repo:{REPO} is:issue in:body "{fingerprint}"',
                    "per_page": 10,
                }
            )
            found = self.transport.request("GET", "search/issues?" + query)
            if (
                not isinstance(found, dict)
                or found.get("incomplete_results") is not False
                or not isinstance(found.get("items"), list)
            ):
                raise ValueError("incomplete issue search")
            match = next(
                (
                    item
                    for item in found["items"]
                    if isinstance(item, dict)
                    and isinstance(item.get("body"), str)
                    and marker in item["body"]
                ),
                None,
            )
            url = issue_url(match["html_url"]) if match else None
            if not self.enabled():
                return result
            if row.get("status") == "submission-uncertain":
                if url:
                    row.update(url=url, status="deduplicated")
                    self.save(ledger)
                    return {**result, "status": "deduplicated", "url": url}
                return {**result, "status": "submission-uncertain"}
            # Record ambiguity BEFORE POST. A timeout/crash after GitHub accepts a
            # request must not create a second issue or comment on the next call.
            row["status"] = "submission-uncertain"
            ledger["attempts"].append(now)
            self.save(ledger)
            if url:
                number = url.rsplit("/", 1)[1]
                self.transport.request(
                    "POST", f"repos/{REPO}/issues/{number}/comments", {"body": body}
                )
                status = "commented"
            else:
                posted = self.transport.request(
                    "POST",
                    f"repos/{REPO}/issues",
                    {
                        "title": f"[Agent report] {KINDS[kind]}"
                        + (f" — {context}" if context else "")
                        + f" (v{self.version})",
                        "body": body,
                    },
                )
                url = issue_url(posted["html_url"])
                status = "published"
            row.update(url=url, status=status)
            self.save(ledger)
            return {**result, "status": status, "url": url}
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            return {
                **result,
                "status": "submission-uncertain"
                if row.get("status") == "submission-uncertain"
                else "pending",
            }


def main():
    parser = argparse.ArgumentParser(
        description="Opt-in sanitized reports to github.com/" + REPO
    )
    parser.add_argument("--memcap-version", required=True)
    parser.add_argument("action", choices=["enable", "disable", "status", *KINDS])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--context",
        choices=sorted(CONTEXTS),
        help="Fixed activity context; no commands or free text are uploaded",
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        help="Observed wait, whole seconds (0–604800); omit if unknown",
    )
    args = parser.parse_args()
    if args.context and args.action not in KINDS:
        parser.error("--context requires a report category")
    if args.dry_run and args.action in ("enable", "disable"):
        parser.error("--dry-run applies to report categories, not consent changes")
    if args.wait_seconds is not None and (
        args.action not in KINDS or not 0 <= args.wait_seconds <= 604800
    ):
        parser.error(
            "--wait-seconds requires a report category and an integer from 0 to 604800"
        )
    config = (
        Path(os.environ.get("MEMCAP_CONFIG_HOME", str(Path.home() / ".config")))
        / "memcap"
    )
    state = (
        Path(os.environ.get("MEMCAP_STATE_HOME", str(Path.home() / ".local/state")))
        / "memcap"
    )
    reporter = Reporter(config, state, args.memcap_version)
    try:
        if args.action in ("enable", "disable"):
            reporter.consent(args.action == "enable")
        if args.action in ("enable", "disable", "status"):
            print(
                "Automatic public GitHub reports "
                + ("enabled" if reporter.enabled() else "disabled")
                + f" for {REPO}."
            )
            print(
                "Only sanitized numeric diagnostics and fixed error categories; uses your GitHub CLI login."
            )
            print(
                "No memcap publication quota or retry cooldown. Duplicate suppression remains active."
            )
        else:
            result = reporter.report(
                args.action,
                dry_run=args.dry_run or os.environ.get("MC_DRY_RUN") == "1",
                wait_seconds=args.wait_seconds,
                context=args.context,
            )
            print(json.dumps(result))
            if "url" not in result:
                print(
                    "Report retained locally or deferred; continue your work. Do not repeatedly retry reporting or upload raw logs."
                )
                if not reporter.enabled():
                    print(
                        "The user may opt in once with memcap report enable; agents must not enable it on their behalf."
                    )
    except (OSError, ValueError, TypeError):
        print(
            "memcap: report could not be saved; no further submission attempted. Continue your work."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
