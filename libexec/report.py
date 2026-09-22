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
}
FACTS = {
    "cap_kb",
    "tracked_kb",
    "available_kb",
    "pressure",
    "waiting",
    "running",
    "registry_age_seconds",
}


def read_json(path):
    if path.is_symlink():
        raise ValueError("symlinked report state")
    with path.open() as stream:
        raw = stream.read(1048577)
    if len(raw) > 1048576:
        raise ValueError("oversized report state")
    return json.loads(raw)


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


def capture(state):
    """Numeric allowlist only. Queue counts describe stored entries, not live jobs."""
    facts = {}
    try:
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [str(root / "bin/memcap"), "_queue-sample"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        cap, tracked, available, pressure, fault = map(
            int, result.stdout.splitlines()[0].split()
        )
        if result.returncode == 0 and not fault and pressure in (1, 2, 4):
            facts.update(
                cap_kb=cap,
                tracked_kb=tracked,
                available_kb=available,
                pressure=pressure,
            )
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass
    try:
        path = state / "queue/jobs.json"
        jobs = read_json(path)["jobs"]
        if not isinstance(jobs, list) or not all(
            isinstance(j, dict) and j.get("status") in ("waiting", "running")
            for j in jobs
        ):
            raise ValueError("invalid registry")
        facts.update(
            waiting=sum(j["status"] == "waiting" for j in jobs),
            running=sum(j["status"] == "running" for j in jobs),
            registry_age_seconds=max(0, int(time.time() - path.stat().st_mtime)),
        )
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return facts


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

    def report(self, kind, *, dry_run=False):
        if kind not in KINDS:
            raise ValueError("unknown report category")
        try:
            with self.locked():
                return self._report(kind, dry_run)
        except BlockingIOError:
            return {
                "status": "busy",
                "message": "Another report is being handled; continue work.",
            }

    def _report(self, kind, dry_run):
        fingerprint = hashlib.sha256(f"1:{self.version}:{kind}".encode()).hexdigest()[
            :20
        ]
        marker = f"<!-- memcap-report:{fingerprint} -->"
        draft = self.directory / f"{fingerprint}.md"
        now = self.now()
        raw = self.snapshot()
        facts = {
            key: value
            for key, value in raw.items()
            if key in FACTS and type(value) is int and 0 <= value <= 2**63 - 1
        }
        if facts.get("pressure") not in (1, 2, 4):
            facts.pop("pressure", None)
        body = (
            f"{marker}\n## Agent-reported observation\n\n{KINDS[kind]}. This is a suspected problem, not a confirmed root cause.\n\n"
            f"Memcap version: {self.version}\nCategory: {kind}\nReport identifier: {fingerprint}\nObserved at: {datetime.fromtimestamp(now, timezone.utc).isoformat()}\n\n"
            "Snapshot collected at report time, which may differ from failure time. Missing values are unknown. "
            "Queue counts are stored registry entries, not verified live jobs. Pressure: 1=green, 2=yellow, 4=red.\n\n"
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
            if last and now - last < 3600:
                return {**result, "status": row.get("status", "pending")}
            ledger["attempts"] = [t for t in ledger["attempts"] if now - t < 86400]
            if len(ledger["attempts"]) >= 5:
                return {**result, "status": "rate-limited"}
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
                        "title": f"[Agent report] {KINDS[kind]} (v{self.version})",
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
    args = parser.parse_args()
    if args.dry_run and args.action in ("enable", "disable"):
        parser.error("--dry-run applies to report categories, not consent changes")
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
        else:
            result = reporter.report(
                args.action, dry_run=args.dry_run or os.environ.get("MC_DRY_RUN") == "1"
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
