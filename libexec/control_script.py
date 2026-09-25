"""Inspect finite shell helpers; never infer workload size from a filename.

Only literal commands from the lightweight policy qualify. Expanded arguments
are checked again by _inspect. Execute the validated text, not a reopened file.
"""

import os
import re
import shlex
import stat
from pathlib import Path

from lightweight import local_variable

MARKER = "/__MEMCAP_SCRIPT_VALUE_"


def script_argv(argv):
    return (
        len(argv) >= 2
        and argv[0] in {"bash", "/bin/bash", "sh", "/bin/sh"}
        and not argv[1].startswith("-")
    )


def rewrite(text, executable, session_key, names=None, depth=0):
    from inspection import spans, substitution_end
    from scheduler_policy import light_shell, normalized_lines

    if len(text) > 32768 or depth > 8 or MARKER in text:
        return None
    text = normalized_lines(text)
    # Local aliases are data only. Startup/lookup/exported settings are excluded.
    names = set(names or ())
    names.update(re.findall(r"(?:^|[;\s])([A-Za-z_][A-Za-z_0-9]*)=", text))
    if any(not local_variable(name) for name in names):
        return None
    parts, replacements, quote, i = [], [], "", 0
    while i < len(text):
        c = text[i]
        if c == "\\" and quote != "'":
            parts.append(text[i : i + 2])
            i += 2
            continue
        if c in "\"'":
            if quote == c:
                quote = ""
            elif not quote:
                quote = c
        value = None
        if quote != "'" and text.startswith("$(", i):
            end = substitution_end(text, i, depth)
            if end is None:
                return None
            producer = rewrite(
                text[i + 2 : end], executable, session_key, names, depth + 1
            )
            if producer is None:
                return None
            value, consumed = "$(" + producer + ")", end + 1 - i
        elif quote != "'" and c == "$":
            match = re.match(
                r"\$(?:\{([A-Za-z_][A-Za-z_0-9]*|[0-9])\}|([A-Za-z_][A-Za-z_0-9]*|[0-9]))",
                text[i:],
            )
            if match and (match[1] or match[2]) in names | set("0123456789"):
                value, consumed = match[0], len(match[0])
            else:
                return None
        if value is not None:
            if len(replacements) >= 128:
                return None
            token = MARKER + str(len(replacements)) + "__"
            replacements.append((token, value))
            parts.append(token)
            i += consumed
        else:
            parts.append(c)
            i += 1
    masked = "".join(parts)
    tokens = list(spans(masked))
    boundaries, start = [], 0
    for token in tokens:
        if token[2] in {";", "|", "&&", "||"}:
            boundaries.append((start, token[0]))
            start = token[1]
    boundaries.append((start, len(masked)))
    inserts = []
    prefix = (
        shlex.join([executable, "_inspect", "--session-key", session_key, "--"]) + " "
    )
    for start, end in boundaries:
        stage = masked[start:end].strip()
        if not stage:
            continue
        try:
            words = shlex.split(stage)
        except ValueError:
            return None
        bindings = [
            re.fullmatch(r"([A-Za-z_][A-Za-z_0-9]*)=(.*)", word, re.S) for word in words
        ]
        if all(bindings):
            from scheduler_policy import literal_shell

            # Assignment-only statements preserve the original shell's scope.
            if not literal_shell(stage) or any(b[1] not in names for b in bindings):
                return None
            continue
        if words in (["set", "-euo", "pipefail"], ["set", "-eu"], ["set", "-e"]):
            continue
        if MARKER in words[0] or not light_shell(stage, allow_bare_globs=True):
            return None
        name = Path(words[0]).name
        if name == "printf":
            # Keep the builtin's formatting semantics, but never a dynamic format
            # or -v variable assignment. Data operands may be expanded normally.
            if len(words) < 2 or MARKER in words[1] or words[1].startswith("-"):
                return None
        elif name not in {"cd", "echo", "true", "false"}:
            inserts.append(
                (
                    start + len(masked[start:end]) - len(masked[start:end].lstrip()),
                    prefix,
                )
            )
    for offset, value in reversed(inserts):
        masked = masked[:offset] + value + masked[offset:]
    for token, value in replacements:
        masked = masked.replace(token, value)
    return masked if boundaries else None


def prepared(argv, executable, session_key="", cwd=None):
    if not script_argv(argv) or any(os.environ.get(k) for k in ("BASH_ENV", "ENV")):
        return None
    path = Path(cwd or os.getcwd()) / argv[1]
    try:
        # Nonblocking open also prevents a changed path becoming a FIFO wait.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "r") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 32768:
                return None
            text = source.read(32769)
    except (OSError, UnicodeError):
        return None
    guarded = rewrite(text, executable, session_key)
    if guarded is None:
        return None
    return [argv[0], "-c", guarded, argv[1], *argv[2:]]


def guard_invocation(command, executable, session_key):
    from inspection import spans
    from scheduler_policy import light_shell, literal_shell, normalized_lines

    text = normalized_lines(command)
    if not literal_shell(text):
        return None
    cwd, start, scripts = Path.cwd(), 0, []
    for token in list(spans(text)) + [(len(text), len(text), ";", False)]:
        if token[2] not in {";", "&&", "||", "|"}:
            continue
        stage = text[start : token[0]].strip()
        try:
            words = shlex.split(stage)
        except ValueError:
            return None
        if words[:1] == ["cd"] and len(words) == 2 and not words[1].startswith("-"):
            cwd = cwd / words[1]
        elif script_argv(words) and prepared(words, executable, session_key, cwd):
            scripts.append(
                start
                + len(text[start : token[0]])
                - len(text[start : token[0]].lstrip())
            )
        elif stage and not light_shell(stage):
            return None
        start = token[1]
    if not scripts:
        return None
    prefix = (
        shlex.join([executable, "_inspect", "--session-key", session_key, "--"]) + " "
    )
    for offset in reversed(scripts):
        text = text[:offset] + prefix + text[offset:]
    return text
