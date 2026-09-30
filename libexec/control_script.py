"""Inspect finite shell helpers; never infer workload size from a filename.

Only literal commands from the lightweight policy qualify. Expanded arguments
are checked again by _inspect. Execute the validated text, not a reopened file.
"""

import os
import re
import shlex
import stat
from pathlib import Path

from lightweight import local_variable, parameter_value

MARKER = "/__MEMCAP_SCRIPT_VALUE_"


def execution_text(text):
    # The classifier's normalized ';' placeholders include blank/comment lines
    # and the newline after `then`. Restore separators before executing a proof.
    # Quoted semicolons and case's ';;' remain untouched.
    from inspection import spans
    for start, end, token, _ in reversed(list(spans(text))):
        if token == ";":
            text = text[:start] + "\n" + text[end:]
    return text


def condition(text):
    """Data-only bash tests used by finite remote helpers; never evaluate here."""
    value = r'(?:"\$(?:[A-Za-z_][A-Za-z_0-9]*|\{[A-Za-z_][A-Za-z_0-9]*(?::-)?\})"|"[A-Za-z_0-9.-]*"|\*\[\[:space:\]\]\*)'
    clause = r"(?:-[zn]\s+" + value + "|" + value + r"\s+(?:==|!=)\s+" + value + ")"
    return bool(re.fullmatch(r"\s*" + clause + r"(?:\s*(?:\|\||&&)\s*" + clause + r")*\s*", text))


def branches(text, executable, session_key, names, depth, functions):
    """Prove every branch, then retain the original shell's control flow."""
    replacements = []
    from inspection import spans
    def at(position, word):
        return any(start == position and token == word for start, _, token, _ in spans(text))
    # Only non-nested if and case forms. Nested/dynamic syntax remains managed.
    case = re.compile(r'\bcase\s+"\$[A-Za-z_][A-Za-z_0-9]*"\s+in\s+(.*?)\besac\b', re.S)
    for match in list(case.finditer(text))[::-1]:
        if not at(match.start(), "case") or not at(match.end()-4, "esac"):
            return None
        arms = match[1].split(";;")
        if arms[-1].strip("; \t"):
            return None
        output = []
        for arm in arms[:-1]:
            pair = arm.lstrip("; \t").split(")", 1)
            if len(pair) != 2 or not re.fullmatch(r"\s*(?:''|\*|[A-Za-z_0-9-]+)(?:\s*\|\s*(?:''|[A-Za-z_0-9-]+))*\s*", pair[0]):
                return None
            guarded = rewrite(pair[1], executable, session_key, names, depth+1, functions)
            if guarded is None:
                return None
            output.append(pair[0] + ") " + guarded + ";;")
        rendered = text[match.start():match.start(1)] + " ".join(output) + " esac"
        token = "echo __MEMCAP_BRANCH_" + str(len(replacements)) + "__"
        replacements.append((token, rendered))
        text = text[:match.start()] + token + text[match.end():]
    pattern = re.compile(r"\bif\s+\[\[\s+(.*?)\s+\]\];\s*then\s+(.*?)\bfi\b", re.S)
    for match in list(pattern.finditer(text))[::-1]:
        if not at(match.start(), "if") or not at(match.end()-2, "fi"):
            return None
        if not condition(match[1]):
            return None
        guarded = rewrite(match[2], executable, session_key, names, depth+1, functions)
        if guarded is None:
            return None
        rendered = "if [[ " + match[1] + " ]]; then " + guarded + " fi"
        token = "echo __MEMCAP_BRANCH_" + str(len(replacements)) + "__"
        replacements.append((token, rendered))
        text = text[:match.start()] + token + text[match.end():]
    # A tested assignment such as [[ -n "$SERVICE" ]] && CMD="$CMD $SERVICE".
    for match in list(re.finditer(r"\[\[\s+(.*?)\s+\]\]", text, re.S))[::-1]:
        if not at(match.start(), "[[") or not at(match.end()-2, "]]" ):
            return None
        if not condition(match[1]):
            return None
        token = "echo __MEMCAP_BRANCH_" + str(len(replacements)) + "__"
        replacements.append((token, match[0]))
        text = text[:match.start()] + token + text[match.end():]
    if not replacements:
        return None
    guarded = rewrite(text, executable, session_key, names, depth+1, functions)
    if guarded is None:
        return None
    for token, rendered in reversed(replacements):
        guarded = guarded.replace(token, rendered)
    return guarded


def script_argv(argv):
    return (
        bool(argv) and "/" in argv[0] and argv[0].endswith(".sh")
    ) or (
        len(argv) >= 2
        and argv[0] in {"bash", "/bin/bash", "sh", "/bin/sh"}
        and not argv[1].startswith("-")
    )


def rewrite(text, executable, session_key, names=None, depth=0, functions=()):
    from inspection import spans, substitution_end
    from scheduler_policy import light_shell, normalized_lines

    if len(text) > 32768 or depth > 8 or MARKER in text or (depth == 0 and "__MEMCAP_BRANCH_" in text):
        return None
    text = normalized_lines(text)
    names = set(names or ())
    names.update(re.findall(r"(?:^|[;\s])([A-Za-z_][A-Za-z_0-9]*)=", text))
    if any(not local_variable(name) for name in names):
        return None
    if re.search(r"(?:^|[;\s])(?:if|case)\s|\[\[", text):
        return branches(text, executable, session_key, names, depth, functions)
    # One observed remote parameter helper. Its body is proven and guarded,
    # then calls retain shell-function/positional-argument semantics. Never
    # allow arbitrary definitions, shadow external families or recurse.
    tokens = list(spans(text))
    definitions = [i for i, token in enumerate(tokens) if token[2] == "ssm()"]
    if definitions:
        if len(definitions) != 1 or "ssm" in functions:
            return None
        start = definitions[0]
        # The definition must execute in the current shell unconditionally.
        # Otherwise a skipped/pipelined definition could leave a same-named
        # external program available to an apparently checked helper call.
        if (start and tokens[start-1][2] != ";") or start+2 >= len(tokens) or tokens[start+1][2] != "{":
            return None
        end = next((i for i in range(start+2, len(tokens)) if tokens[i][2] == "}"), None)
        if end is None or tokens[end-1][2] != ";":
            return None
        if end+1 < len(tokens) and tokens[end+1][2] not in {";", "&&", "||"}:
            return None
        before = rewrite(text[:tokens[start][0]], executable, session_key, names, depth+1, functions)
        body = rewrite(text[tokens[start+1][1]:tokens[end][0]], executable, session_key, names, depth+1)
        after = rewrite(text[tokens[end][1]:], executable, session_key, names, depth+1, (*functions, "ssm"))
        if before is None or body is None or after is None:
            return None
        return before + "ssm() {" + body + "}" + after
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
                text[i + 2 : end], executable, session_key, names, depth + 1, functions
            )
            if producer is None:
                return None
            value, consumed = "$(" + producer + ")", end + 1 - i
        elif quote != "'" and c == "$":
            match = parameter_value(text[i:])
            if match:
                # Parameter values are data. Dynamic executable names and
                # execution-capable expanded arguments are still checked below.
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
            # A literal ~/ path on the RHS is shell data. Mask it only for
            # proof; execution retains the caller shell's HOME expansion.
            proof = re.sub(r"(?<==)~/[A-Za-z0-9_./-]+", "/__MEMCAP_HOME_PATH__", stage)
            if not literal_shell(proof) or any(b[1] not in names for b in bindings):
                return None
            continue
        if words in (["set", "-euo", "pipefail"], ["set", "-eu"], ["set", "-e"], ["set", "+e"], ["set", "-u"]):
            continue
        if len(words) == 2 and words[0] == "exit" and re.fullmatch(r"[0-9]{1,3}", words[1]) and int(words[1]) <= 255:
            continue
        if words[0] in functions:
            # Arguments are data to a checked helper, including substitutions
            # already guarded above. Prove the remaining shell syntax as echo.
            offset = stage.find(words[0]) + len(words[0])
            if not light_shell("echo " + stage[offset:], allow_bare_globs=True):
                return None
            continue
        if MARKER in words[0] or not light_shell(stage, allow_bare_globs=True):
            return None
        name = Path(words[0]).name
        if name == "printf":
            # Keep the builtin's formatting semantics, but never a dynamic format
            # or -v variable assignment. Data operands may be expanded normally.
            if len(words) < 2 or MARKER in words[1] or words[1].startswith("-"):
                return None
        elif name not in {"cd", "echo", "true", "false", "command"}:
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
    direct = "/" in argv[0] and argv[0].endswith(".sh")
    path = Path(cwd or os.getcwd()) / argv[0 if direct else 1]
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
    if direct:
        first = text.splitlines()[0] if text else ""
        if first not in {"#!/bin/bash", "#!/usr/bin/env bash", "#!/bin/sh"} or not os.access(path, os.X_OK):
            return None
        interpreter = "bash" if first == "#!/usr/bin/env bash" else "/bin/sh" if first == "#!/bin/sh" else "/bin/bash"
        argv = [interpreter, *argv]
    guarded = rewrite(text, executable, session_key)
    if guarded is None:
        return None
    return [argv[0], "-c", execution_text(guarded), argv[1], *argv[2:]]


def guard_invocation(command, executable, session_key, cwd=None):
    from inspection import spans
    from scheduler_policy import light_shell, literal_shell, normalized_lines

    text = normalized_lines(command)
    if not literal_shell(text):
        return None
    cwd, start, scripts = Path(cwd or os.getcwd()), 0, []
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
    return execution_text(text)


def guard_inline(command, executable, session_key):
    # Preserve assignment scope and quote handling for remote API helpers
    # pasted directly into Bash, rather than requiring a separate script file.
    if not re.search(r"(?:^|[;\s])(?:[A-Za-z_][A-Za-z_0-9]*=|ssm\(\)|set\s)", command):
        return None
    guarded = rewrite(command, executable, session_key)
    return execution_text(guarded) if guarded is not None else None
