"""Install memcap-owned agent entries; never change permissions or approve trust.

Prepare every edit before writing, preserve symlinks, back up original bytes,
replace atomically, and refuse ambiguous or concurrently changed configurations.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from collections import Counter
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

from report import PERFORMANCE_GUIDANCE, PROTECTION_GUIDANCE

SCHEMA = 1
BEGIN = "<!-- memcap:begin -->"
END = "<!-- memcap:end -->"
GUIDANCE = f"""{BEGIN}
## memcap shared-memory coordination

memcap coordinates memory across agent sessions. Its installed hooks supply current
operating guidance and diagnostics; use `memcap status`, `memcap queue` and
`memcap doctor` to inspect capacity, queued work and integration health.

In the default shared mode, measured Docker, agent and simulator memory count
toward one total. Docker's VM ceiling is not a reservation or an admission reason.
Use current queue diagnostics to distinguish capacity, pressure and lock failures.
QUEUE_POLICY=adaptive treats that total as a planning target and uses fresh pressure,
physical headroom and staged starts. Yellow is allowed when configured; red stops
new heavy launches. Strict mode keeps the absolute budget. Neither promises running
or unmanaged work cannot reach red. Inspect actual admission reasons instead of
inferring a blocker from Docker's ceiling or charged footprint.
Old queue supervisors keep their loaded version until their tasks finish. New
commands use the updated installed runner; do not duplicate old pending work.

Keep polling an existing queued task with TaskOutput block=true timeout=60000 or a
blocking tool-session poll, once per minute. If unavailable, run
`memcap wait JOB_ID --timeout 60` using the existing ID from `memcap queue`.
Use `memcap wait --session --timeout 60` to wait on finite jobs owned by this agent
process without a shell pipeline to discover IDs. Cancel an owned obsolete task
whose result is no longer needed using native task cancellation, then read its
final status; never cancel still-needed work or other sessions' tasks.
This creates no job or reservation. Never invent drain ticks or Bash sleep loops. Continue independent work. Read final
output and exit status before dependent work; waiting is not a failed command.
Do not submit duplicates or repeatedly read unchanged output.
{PROTECTION_GUIDANCE}
Respect explicit user cancellation. Preserve other sessions' processes and work.

A simulator preparation timeout does not prove memory starvation or failed tests.
Check device readiness and the runner's admission reason. Do not reset another
session's device or kill Docker to unblock work; establish ownership before cleanup.

For suspected memcap defects, run `memcap report CATEGORY` once. Categories are
`queue-lock`, `measurement`, `integration`, `queue-stall`, `unexpected-termination`,
`missing-task-poll`, `lightweight-queued` and `polling-overhead`.
Reports use fixed categories and sanitized numeric facts;
they publish to GitHub only after the user's one-time opt-in, otherwise stay local.
They automatically include available machine capacity, OS version, memory/load,
pause state and queue ages/blockers. Never enable reporting on the user's
behalf or upload raw logs, commands, paths or source. Continue work if reporting is
deferred; do not create a reporting retry loop. Reuse any returned issue URL.

{PERFORMANCE_GUIDANCE}

This block is managed by `memcap integrate`. Limits and detailed policy come from
memcap's current configuration and hook feedback, not hardcoded values here.
{END}"""


class IntegrationError(Exception):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IntegrationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def json_object(text, path):
    try:
        data = json.loads(text, object_pairs_hook=unique_object)
    except ValueError as error:
        raise IntegrationError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(data, dict):
        raise IntegrationError(f"expected a JSON object in {path}")
    return data


def ours(hook):
    if hook.get("type") != "command" or not isinstance(hook.get("command"), str):
        return False
    try:
        words = shlex.split(hook["command"])
    except ValueError:
        return False
    return bool(
        words
        and Path(words[0]).name in {"memcap", "memcap-real"}
        and (
            words[1:]
            in (
                ["feedback"],
                ["feedback", "--wait"],
                ["queue-hook", "claude"],
                ["queue-hook", "codex"],
            )
        )
    )


def merge_hooks(data, generated):
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        raise IntegrationError("hooks must be an object; refusing to replace it")
    merged = {}
    for event in dict.fromkeys([*hooks, *generated["hooks"]]):
        groups = hooks.get(event, [])
        pending = list(generated["hooks"].get(event, []))
        if not isinstance(groups, list):
            raise IntegrationError(f"{event} matcher groups must be a list")
        retained = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise IntegrationError(f"invalid hook group for {event}")
            if not all(isinstance(h, dict) for h in group["hooks"]):
                raise IntegrationError(f"invalid handler for {event}")
            mixed = any(not ours(h) for h in group["hooks"])
            remaining = []
            replacement_group = group
            for index, handler in enumerate(group["hooks"]):
                if not ours(handler):
                    remaining.append(handler)
                    continue
                role = shlex.split(handler["command"])[1]
                match = next(
                    (
                        g
                        for g in pending
                        if shlex.split(g["hooks"][0]["command"])[1] == role
                        and (mixed or not remaining)
                        and (
                            not mixed
                            or (
                                (g.get("matcher") or None)
                                == (group.get("matcher") or None)
                                and not (set(group) - {"hooks", "matcher"})
                            )
                        )
                    ),
                    None,
                )
                if match is not None:
                    remaining.extend(match["hooks"])
                    pending.remove(match)
                    if not mixed:
                        replacement_group = match
                elif any(not ours(h) for h in group["hooks"][index + 1 :]):
                    raise IntegrationError(
                        f"ambiguous mixed {event} group: removing a memcap handler "
                        "would shift unrelated hook trust keys; separate or remove "
                        "the stale memcap handler manually and review hook trust"
                    )
            # Empty group slots preserve the indexes of later unrelated hooks.
            retained.append({**replacement_group, "hooks": remaining})
        merged[event] = retained + pending
    return {**data, "hooks": merged}


def managed_markdown(text):
    starts, ends = text.count(BEGIN), text.count(END)
    if not starts and not ends:
        return text + ("\n\n" if text else "") + GUIDANCE + "\n"
    if starts != 1 or ends != 1 or text.index(BEGIN) > text.index(END):
        raise IntegrationError(
            "ambiguous managed Markdown markers; repair them before integrating"
        )
    return text[: text.index(BEGIN)] + GUIDANCE + text[text.index(END) + len(END) :]


@dataclass
class Edit:
    path: Path
    target: Path
    before: bytes | None
    after: bytes | None
    mode: int

    @classmethod
    def read(cls, path):
        path = Path(path).absolute()
        if path.is_symlink() and not path.exists():
            raise IntegrationError(f"dangling symlink: {path}")
        target = path.resolve()
        before, mode = None, 0o600
        if target.exists():
            info = target.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise IntegrationError(f"not a regular file owned by this user: {path}")
            before, mode = target.read_bytes(), stat.S_IMODE(info.st_mode)
        return cls(path, target, before, before, mode)

    def unchanged(self):
        if self.path.is_symlink() and not self.path.exists():
            return False
        if self.target.exists():
            info = self.target.stat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != self.mode
            ):
                return False
        return (
            self.path.resolve() == self.target
            and (self.target.read_bytes() if self.target.exists() else None)
            == self.before
        )


def atomic_write(path, content, mode):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".memcap-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            os.fchmod(file.fileno(), mode)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def commit(edits):
    changed = [e for e in edits if e.before != e.after]
    with ExitStack() as stack:
        for parent in sorted({e.target.parent for e in changed}):
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(
                parent / ".memcap-integration.lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
            stack.callback(os.close, fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise IntegrationError(
                    "another integration update is running; retry when it finishes"
                ) from error
        if not all(e.unchanged() for e in changed):
            raise IntegrationError(
                "configuration changed while preparing; no configuration written"
            )
        token = uuid.uuid4().hex
        for edit in changed:
            if edit.before is not None:
                backup = edit.target.with_name(
                    f".{edit.target.name}.memcap-backup-{token}"
                )
                atomic_write(backup, edit.before, 0o600)
                print(f"Backup: {backup}")
        written = []
        try:
            for edit in changed:
                if not edit.unchanged():
                    raise IntegrationError(f"concurrent change to {edit.path}")
                atomic_write(edit.target, edit.after, edit.mode)
                written.append(edit)
        except (OSError, IntegrationError) as error:
            conflicts = []
            for edit in reversed(written):
                try:
                    if (
                        edit.path.resolve() != edit.target
                        or edit.target.read_bytes() != edit.after
                    ):
                        conflicts.append(str(edit.path))
                    elif edit.before is None:
                        edit.target.unlink()
                    else:
                        atomic_write(edit.target, edit.before, edit.mode)
                except OSError:
                    conflicts.append(str(edit.path))
            detail = (
                f"; restore from backups for: {', '.join(conflicts)}"
                if conflicts
                else "; previous file contents restored"
            )
            raise IntegrationError(f"update failed: {error}{detail}") from error
    return len(changed)


def profiles(args, home, environment):
    selected = []
    explicit = args.claude or args.codex or args.claude_dir or args.codex_dir
    if args.claude or not explicit:
        default = home / ".claude"
        if args.claude or default.exists() or shutil.which("claude"):
            selected.append(("claude", default))
        for path in sorted(home.glob(".claude-*")):
            if path.is_dir() and (
                (path / "settings.json").exists() or (path / "CLAUDE.md").exists()
            ):
                selected.append(("claude", path))
        if environment.get("CLAUDE_CONFIG_DIR"):
            selected.append(
                ("claude", Path(environment["CLAUDE_CONFIG_DIR"]).expanduser())
            )
    if args.codex or not explicit:
        path = Path(environment.get("CODEX_HOME", str(home / ".codex"))).expanduser()
        if args.codex or path.exists() or shutil.which("codex"):
            selected.append(("codex", path))
    selected.extend(("claude", Path(p).expanduser()) for p in args.claude_dir)
    selected.extend(("codex", Path(p).expanduser()) for p in args.codex_dir)
    unique = {}
    for agent, directory in selected:
        if directory.exists() and not directory.is_dir():
            raise IntegrationError(f"profile is not a directory: {directory}")
        unique.setdefault((agent, directory.resolve()), (agent, directory.absolute()))
    return list(unique.values())


class Installer:
    def __init__(self, executable, version):
        self.executable = str(executable)
        self.version = version
        self.generated = {}

    def hooks(self, agent):
        if agent not in self.generated:
            result = subprocess.run(
                [self.executable, "agent-hooks", agent, "--queue"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode:
                raise IntegrationError(
                    f"hook generation failed: {result.stderr.strip()}"
                )
            self.generated[agent] = json_object(result.stdout, "generated hooks")
        return self.generated[agent]

    def prepare(self, selected):
        planned = {}
        for agent, directory in selected:
            config = Edit.read(
                directory / ("settings.json" if agent == "claude" else "hooks.json")
            )
            data = (
                json_object(config.before.decode(), config.path)
                if config.before is not None
                else {}
            )
            merged = merge_hooks(data, self.hooks(agent))
            config.after = (
                config.before
                if merged == data
                else (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode()
            )
            doc = Edit.read(
                directory / ("CLAUDE.md" if agent == "claude" else "AGENTS.md")
            )
            doc.after = managed_markdown((doc.before or b"").decode()).encode()
            metadata = Edit.read(directory / ".memcap-integration.json")
            metadata.after = (
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "memcap_version": self.version,
                        "agent": agent,
                        "executable": self.executable,
                    },
                    indent=2,
                )
                + "\n"
            ).encode()
            for edit in (config, doc, metadata):
                if edit.target in planned and planned[edit.target].after != edit.after:
                    raise IntegrationError(f"conflicting profiles share {edit.target}")
                planned[edit.target] = edit
        return list(planned.values())

    def install(self, selected):
        changes = commit(self.prepare(selected))
        print(
            f"Integration configured for {len(selected)} profile(s); {changes} file(s) changed."
        )
        for agent, directory in selected:
            print(f"  {agent}: {directory}")
        print(
            "Reload/restart agent sessions to load changed hooks and guidance; no Mac reboot required."
        )
        if any(a == "codex" for a, _ in selected):
            print(
                "Codex: review/trust new or changed memcap hooks in /hooks. Trust and permissions were not changed."
            )
        print(
            "Checking installed files; Codex runtime trust requires a separate doctor check."
        )
        self.doctor(selected, runtime=False)
        print("Run memcap doctor to check configuration and Codex hook trust.")

    def doctor(self, selected, runtime=True):
        from integration_probe import codex_hooks

        issues = 0
        for agent, directory in selected:
            label = f"{agent}: {directory}"
            try:
                edits = self.prepare([(agent, directory)])
                pending = [e.path.name for e in edits if e.before != e.after]
                if pending:
                    print(
                        f"WARN {label}: missing/stale integration ({', '.join(pending)}); run memcap integrate."
                    )
                    issues += 1
                else:
                    print(
                        f"OK {label}: hooks, timeouts and managed guidance match memcap {self.version}."
                    )
                config = json_object((edits[0].before or b"{}").decode(), edits[0].path)
                if config.get("disableAllHooks") is True:
                    print(
                        f"WARN {label}: disableAllHooks is true; review agent settings. No settings changed."
                    )
                    issues += 1
                if agent == "codex":
                    if (directory / "AGENTS.override.md").exists():
                        print(
                            f"WARN {label}: AGENTS.override.md may supersede managed guidance; inspect it."
                        )
                        issues += 1
                    if not runtime:
                        print(
                            f"WARN {label}: Codex hook trust is unverified (--no-runtime). Check /hooks."
                        )
                        issues += 1
                        continue
                    loaded = codex_hooks(directory)
                    wanted = [
                        (
                            event.lower(),
                            h["command"],
                            h["timeout"],
                            g.get("matcher") or None,
                        )
                        for event, groups in self.hooks(agent)["hooks"].items()
                        for g in groups
                        for h in g["hooks"]
                    ]
                    source = (directory / "hooks.json").resolve()
                    own = [
                        h
                        for h in loaded
                        if Path(h.get("sourcePath") or "").resolve() == source
                        and ours({"type": "command", "command": h.get("command")})
                    ]
                    observed = [
                        (
                            str(h.get("eventName", "")).replace("_", "").lower(),
                            h.get("command"),
                            h.get("timeoutSec"),
                            h.get("matcher") or None,
                        )
                        for h in own
                    ]
                    if Counter(observed) != Counter(wanted) or any(
                        not h.get("enabled")
                        or h.get("trustStatus") not in {"trusted", "managed"}
                        for h in own
                    ):
                        print(
                            f"WARN {label}: Codex hooks missing, disabled or require trust review; open /hooks."
                        )
                        issues += 1
                    else:
                        print(
                            f"OK {label}: Codex reports all {len(own)} memcap hooks enabled and trusted."
                        )
            except (
                IntegrationError,
                OSError,
                ValueError,
                TypeError,
                subprocess.SubprocessError,
            ) as error:
                print(f"WARN {label}: {error}")
                issues += 1
        print(
            "Checks cover selected global profiles; project/managed overrides and already-open session reload state are not certified. Reload sessions after integration updates."
        )
        return 1 if issues else 0


def main():
    parser = argparse.ArgumentParser(prog="memcap integrate/doctor")
    parser.add_argument("action", choices=("integrate", "doctor", "discover"))
    parser.add_argument("--executable", required=True, help=argparse.SUPPRESS)
    parser.add_argument("--version", required=True, help=argparse.SUPPRESS)
    parser.add_argument(
        "--claude",
        action="store_true",
        help="select detected Claude profiles, including the default",
    )
    parser.add_argument(
        "--codex", action="store_true", help="select CODEX_HOME or ~/.codex"
    )
    parser.add_argument(
        "--claude-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="select an explicit Claude profile (repeatable)",
    )
    parser.add_argument(
        "--codex-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="select an explicit Codex home (repeatable)",
    )
    parser.add_argument(
        "--no-runtime",
        action="store_true",
        help="doctor: skip Codex runtime probe and report trust as unverified",
    )
    args = parser.parse_args()
    if args.no_runtime and args.action != "doctor":
        parser.error("--no-runtime applies only to doctor")
    selected = profiles(args, Path.home(), os.environ)
    if not selected:
        print(
            "No Claude/Codex profiles detected. Select --claude, --codex or an explicit --claude-dir/--codex-dir."
        )
        return 1
    if args.action == "discover":
        for agent, directory in selected:
            print(f"  {agent}: {directory}")
        return 0
    installer = Installer(args.executable, args.version)
    if args.action == "doctor":
        return installer.doctor(selected, not args.no_runtime)
    installer.install(selected)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        IntegrationError,
        OSError,
        ValueError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"memcap integration: {error}", file=sys.stderr)
        sys.exit(1)
