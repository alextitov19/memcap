"""Conservative launch classification and bounded, documented worker controls."""

from __future__ import annotations

import json
import os
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


def classify_shell(command: str) -> tuple[str, str]:
    words = simple_words(command)
    if not words:
        return "job", ""
    name = Path(words[0]).name
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
    }:
        # tail -f is a persistent process, not a short read.
        if name == "tail" and any(
            a in {"-f", "-F", "--follow"} or a.startswith("--follow=")
            for a in words[1:]
        ):
            return "job", ""
        return "light", ""
    if name == "rg" and not any(
        a.startswith(("--pre", "--hostname-bin")) for a in words[1:]
    ):
        return "light", ""
    if (
        name == "git"
        and len(words) > 1
        and words[1] in {"status", "diff", "log", "show", "rev-parse", "ls-files"}
    ):
        if not any(
            a.startswith(("--ext-diff", "--textconv", "--output", "--exec"))
            for a in words[2:]
        ):
            return "light", ""
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
    if isinstance(timeout, (int, float)) and timeout > 0:
        args += ["--wait", str(max(1, int(timeout / 1000) - 5))]
    args += ["--shell-command", command]
    updated = dict(original)
    updated["command"] = shlex.join(args)
    # Codex's hook schema uses command even when its exec tool uses cmd.
    updated.pop("cmd", None)
    result = {"hookEventName": "PreToolUse", "updatedInput": updated}
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
