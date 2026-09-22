"""Conservative launch classification and bounded, documented worker controls."""

from __future__ import annotations

import json
import hashlib
import os
import re
from pathlib import Path
import shlex


def simple_words(command: str) -> list[str] | None:
    # Never infer safety or reconstruct shell expressions/substitutions.
    if any(char in command for char in "$`\n\r;&|<>(){}*?~"):
        return None
    try:
        return shlex.split(command)
    except ValueError:
        return None


def light_words(words: list[str], glob_checked=False) -> bool:
    if not words:
        return False
    # A bare glob can expand to execution options such as rg's --pre. Require
    # a literal directory prefix for pathname globs; uncertain patterns queue.
    for word in words:
        expanded_status = word.replace("$?", "0")
        wildcard = re.search(r"[\*?\[]", expanded_status)
        if (
            not glob_checked
            and wildcard
            and "/" not in expanded_status[: wildcard.start()]
        ):
            return False
    name = Path(words[0]).name
    # The diagnostic next steps must remain usable while build capacity is full.
    # Exact read-only forms only: no streaming stats, bootstrap or device changes.
    if name == "xcrun" and words[1:] == ["simctl", "list", "devices", "--json"]:
        return True
    if name == "docker" and words[1:] in (
        ["stats", "--no-stream"],
        ["ps"],
        ["ps", "-a"],
        ["ps", "--all"],
        ["buildx", "ls"],
    ):
        return True
    if name == "ps" and words[1:] == ["-Ao", "pid,ppid,command"]:
        return True
    if name in {
        "cat",
        "head",
        "tail",
        "pwd",
        "ls",
        "wc",
        "true",
        "false",
        "printf",
        "echo",
        "grep",
        "egrep",
        "fgrep",
    }:
        # tail -f is a persistent process, not a short read.
        if name == "tail" and any(
            a in {"-f", "-F", "--follow"} or a.startswith("--follow=")
            for a in words[1:]
        ):
            return False
        return True
    if name == "rg" and not any(
        a.startswith(("--pre", "--hostname-bin")) for a in words[1:]
    ):
        return True
    if (
        name == "git"
        and len(words) > 1
        and words[1] in {"status", "diff", "log", "show", "rev-parse", "ls-files"}
    ):
        if not any(
            a.startswith(("--ext-diff", "--textconv", "--output", "--exec"))
            for a in words[2:]
        ):
            return True
    if name == "cd" and len(words) == 2:
        return True
    if name == "sed" and len(words) >= 3 and words[1] == "-n":
        return bool(re.fullmatch(r"[0-9]+(?:,[0-9]+)?p", words[2])) and all(
            not word.startswith("-") for word in words[3:]
        )
    if name == "memcap" and len(words) >= 2:
        if words[1] in {"doctor", "integrate"}:
            # Repair/diagnostic commands must not wait behind the queue they inspect.
            i = 2
            while i < len(words):
                if words[i] in {"--claude", "--codex", "--help", "-h"} or (
                    words[1] == "doctor" and words[i] == "--no-runtime"
                ):
                    i += 1
                elif (
                    words[i] in {"--claude-dir", "--codex-dir"}
                    and i + 1 < len(words)
                    and not words[i + 1].startswith("-")
                ):
                    i += 2
                else:
                    return False
            return True
        return words[1] in {
            "status",
            "queue",
            "diagnostics",
            "help",
            "version",
            "gc",
        } and all(word in {"--json", "--summary"} for word in words[2:])
    if name == "gh" and len(words) >= 3 and words[1] == "run":
        return words[2] in {"watch", "view", "list"} and not any(
            word == "-w" or word.startswith("--web") for word in words[3:]
        )
    return False


def literal_shell(command: str) -> bool:
    """Check expansions before shlex discards whether a regex/glob was quoted."""
    quote = ""
    prefix = ""
    escaped = False
    i = 0
    while i < len(command):
        c = command[i]
        if c in "\n\r":
            return False
        if escaped:
            prefix += c
            escaped = False
        elif c == "\\" and quote != "'":
            escaped = True
        elif quote and c == quote:
            quote = ""
        elif c in "\"'" and not quote:
            quote = c
        elif quote != "'" and c in "$`":
            if c == "$" and command[i : i + 2] == "$?":
                prefix += "0"
                i += 1
            else:
                return False
        elif not quote and c in "*?[":
            if "/" not in prefix:
                return False
            prefix += c
        elif not quote and c in "{}~":
            return False
        elif not quote and (c.isspace() or c in "|&;<>()"):
            prefix = ""
        else:
            prefix += c
        i += 1
    return not quote and not escaped


def light_shell(command: str) -> bool:
    # Recognize a narrow shell grammar solely for lightweight commands. Every
    # stage must qualify. Never evaluate substitutions or reconstruct the input.
    if not literal_shell(command):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False
    separators = {"|", "&&", "||", ";"}
    redirects = {">", ">>", "<", ">&", "<&"}
    words: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in separators:
            if not light_words(words, glob_checked=True):
                return False
            words = []
        else:
            if token.isdigit() and i + 1 < len(tokens) and tokens[i + 1] in redirects:
                i += 1
                token = tokens[i]
            if token in redirects:
                i += 1
                if i >= len(tokens):
                    return False
                target = tokens[i]
                if token in {">&", "<&"}:
                    if not (target.isdigit() or target == "-"):
                        return False
                elif not target or any(char in target for char in "|&;<>()*?[]"):
                    return False
            elif token and all(char in "|&;<>()" for char in token):
                return False
            else:
                words.append(token)
        i += 1
    return light_words(words, glob_checked=True)


def classify_shell(command: str) -> tuple[str, str]:
    if light_shell(command):
        return "light", ""
    words = simple_words(command)
    if not words:
        return "job", ""
    name = Path(words[0]).name
    if name in {"npm", "pnpm", "yarn"}:
        tail = words[1:]
        if tail and tail[0] in {"run", "run-script"}:
            tail = tail[1:]
        if tail and tail[0] in {"dev", "start"}:
            # Distinct arguments (ports, host, etc.) are distinct resources.
            return "resource", "script:" + shlex.join(tail)
    if name == "vite" and (len(words) == 1 or words[1].startswith("-")):
        return "resource", shlex.join(words)
    return "job", ""


def bounded(value: str, maximum: int) -> int:
    try:
        return min(maximum, max(1, int(value)))
    except (ValueError, TypeError):
        return maximum


def cap_flags(
    argv: list[str], flags: tuple[str, ...], output: str, workers: int
) -> list[str]:
    result: list[str] = []
    limit = workers
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            # Insert options before an end-of-options delimiter.
            return result + [f"{output}={limit}"] + argv[i:]
        if arg in flags and i + 1 < len(argv):
            limit = min(limit, bounded(argv[i + 1], workers))
            i += 2
            continue
        if any(arg.startswith(f + "=") for f in flags):
            limit = min(limit, bounded(arg.split("=", 1)[1], workers))
        elif any(
            len(f) == 2 and arg.startswith(f) and arg[len(f) :].isdigit() for f in flags
        ):
            limit = min(limit, bounded(arg[2:], workers))
        else:
            result.append(arg)
        i += 1
    return result + [f"{output}={limit}"]


def worker_argv(argv: list[str], cwd: Path, workers: int) -> list[str]:
    if not argv:
        return argv
    name = Path(argv[0]).name
    if name in {"vitest", "jest"}:
        if name == "jest" and any(a in {"--runInBand", "-i"} for a in argv):
            return argv
        result = cap_flags(argv, ("--maxWorkers", "-w"), "--maxWorkers", workers)
        if name == "vitest" and any(
            a == "--minWorkers" or a.startswith("--minWorkers=") for a in result
        ):
            maximum = next(
                int(a.split("=", 1)[1]) for a in result if a.startswith("--maxWorkers=")
            )
            result = cap_flags(result, ("--minWorkers",), "--minWorkers", maximum)
        return result
    if name == "playwright" and len(argv) > 1 and argv[1] == "test":
        return cap_flags(argv, ("--workers", "-j"), "--workers", workers)
    if (
        name == "cargo"
        and len(argv) > 1
        and argv[1] in {"build", "test", "check", "clippy", "run"}
    ):
        return cap_flags(argv, ("--jobs", "-j"), "--jobs", workers)
    if (
        name == "go"
        and len(argv) > 1
        and argv[1] in {"build", "test", "install", "vet"}
    ):
        # Do not reinterpret the program's own flags (go run and test -args).
        # GOFLAGS still bounds compilation for go run.
        rest = argv[2:]
        split = next((i for i, a in enumerate(rest) if a in {"-args", "--"}), len(rest))
        cleaned = cap_flags(rest[:split], ("-p",), "-p", workers)
        return argv[:2] + [cleaned[-1]] + cleaned[:-1] + rest[split:]
    if name in {"npx", "pnpm", "yarn"} and len(argv) > 2 and argv[1] == "exec":
        return argv[:2] + worker_argv(argv[2:], cwd, workers)
    if name == "npx" and len(argv) > 1 and not argv[1].startswith("-"):
        return argv[:1] + worker_argv(argv[1:], cwd, workers)
    if name not in {"npm", "pnpm", "yarn"}:
        return argv
    pos = 2 if len(argv) > 1 and argv[1] in {"run", "run-script"} else 1
    if len(argv) <= pos or argv[pos].startswith("-"):
        return argv
    try:
        script = (
            json.loads((cwd / "package.json").read_text())
            .get("scripts", {})
            .get(argv[pos], "")
        )
        words = simple_words(script)
    except (OSError, ValueError, TypeError, AttributeError):
        return argv
    if not words or Path(words[0]).name not in {"vitest", "jest", "playwright"}:
        return argv
    args = argv[pos + 1 :]
    if args[:1] == ["--"]:
        args = args[1:]
    limited = worker_argv(words + args, cwd, workers)
    # Existing script flags cannot be removed without changing package.json.
    # Explicit CLI overrides are appended; smaller script limits are retained.
    options = [
        a
        for a in limited
        if a.startswith(("--maxWorkers=", "--workers=", "--minWorkers="))
    ]
    if not options:
        return argv
    clean = cap_flags(
        ["placeholder"] + args,
        ("--maxWorkers", "--workers", "--minWorkers", "-w", "-j"),
        "--memcap-unused",
        workers,
    )[1:-1]
    return argv[: pos + 1] + (["--"] if name == "npm" else []) + clean + options


def worker_environment(environ: dict[str, str], workers: int) -> dict[str, str]:
    env = dict(environ)
    for key in (
        "GOMAXPROCS",
        "CARGO_BUILD_JOBS",
        "CMAKE_BUILD_PARALLEL_LEVEL",
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VITEST_MAX_WORKERS",
        "VITEST_MAX_THREADS",
        "VITEST_MAX_FORKS",
    ):
        env[key] = str(bounded(env.get(key, str(workers)), workers))
    # Keep unrelated Go flags; -p bounds package/build parallelism separately.
    try:
        flags = shlex.split(env.get("GOFLAGS", ""))
        env["GOFLAGS"] = shlex.join(cap_flags(flags, ("-p",), "-p", workers))
    except ValueError:
        pass  # Preserve invalid user input; let Go report it rather than reinterpret it.
    return env


def hook_response(payload: dict, executable: str, agent: str = "codex") -> dict:
    if payload.get("hook_event_name") != "PreToolUse" or payload.get(
        "tool_name"
    ) not in {"Bash", "exec_command", "shell_command"}:
        return {}
    original = payload.get("tool_input", {})
    command = original.get("command", original.get("cmd"))
    if not isinstance(command, str):
        return {}
    kind, resource = classify_shell(command)
    try:
        wrapped = shlex.split(command)
    except ValueError:
        wrapped = []
    # Only an entire canonical wrapper invocation bypasses reinsertion. A
    # wrapper followed by '; another-command' must still queue as a whole.
    already_wrapped = (
        len(wrapped) > 1
        and wrapped[0] in {executable, "memcap"}
        and wrapped[1] in {"run", "queue", "status", "feedback"}
        and shlex.join(wrapped) == command
    )
    if kind == "light" or already_wrapped:
        return {}
    default_shell = (
        os.environ.get("SHELL", "/bin/bash") if agent == "codex" else "/bin/bash"
    )
    shell = original.get("shell") or default_shell
    if not isinstance(shell, str) or not Path(shell).is_absolute():
        shell = "/bin/bash"
    args = [executable, "run", "--shell", shell]
    if original.get("login", agent == "codex"):
        args.append("--login")
    if resource:
        args += ["--resource", resource]
    cwd = original.get("workdir") or original.get("cwd") or payload.get("cwd")
    if isinstance(cwd, str):
        args += ["--cwd", cwd]
    timeout = original.get("timeout")
    if agent == "claude":
        args.append("--wait-forever")
    elif isinstance(timeout, (int, float)) and timeout > 0:
        args += ["--wait", str(max(1, int(timeout / 1000) - 5))]
    if isinstance(payload.get("session_id"), str) and payload["session_id"]:
        args += [
            "--session-key",
            hashlib.sha256(payload["session_id"].encode()).hexdigest(),
        ]
    args += ["--shell-command", command]
    updated = dict(original)
    if agent == "claude":
        updated["run_in_background"] = True
    updated["command"] = shlex.join(args)
    # Codex's hook schema uses command even when its exec tool uses cmd.
    updated.pop("cmd", None)
    result = {"hookEventName": "PreToolUse", "updatedInput": updated}
    if agent == "claude":
        result["additionalContext"] = (
            "memcap keeps this task queued until memory is available, then starts it automatically. "
            "Use TaskOutput with block=true and timeout=60000 for one blocking wait of up to 60 seconds. Repeat once per minute while pending; do not repeatedly read output files or emit holding messages. "
            "Do not submit duplicates, stop because it is queued, or bypass memcap. "
            "Read the final output and exit status before continuing dependent work."
        )
    if agent == "codex":
        # Codex requires allow with updatedInput. Do not silently grant broader
        # command approval in a session that has not already opted out of prompts.
        if payload.get("permission_mode") != "bypassPermissions":
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Run this queued command through the normal approval flow: "
                    + updated["command"],
                }
            }
        result["permissionDecision"] = "allow"
    return {"hookSpecificOutput": result}
