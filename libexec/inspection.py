"""Guard expanded inspection arguments without changing shell/output semantics.

A bare *.go can expand to --pre=helper.go. The shell performs the expansion once;
we validate that exact argv before exec, or send it through normal admission.
"""

import os
import re
import shlex
import sys
from pathlib import Path
from scheduler_policy import light_shell, light_words, normalized_lines


def inspect_argv(argv, fallback):
    if light_words(argv, glob_checked=True):
        try:
            os.execvpe(argv[0], argv, os.environ)
        except (FileNotFoundError, PermissionError) as exc:
            print(f"memcap inspection: {argv[0]}: {exc}", file=sys.stderr)
            return 127 if isinstance(exc, FileNotFoundError) else 126
    return fallback(argv)


def spans(text):
    """Literal shell word spans, retaining quotes and escapes in the source."""
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        start = i
        if text[i] in "|&;<>":
            while i < len(text) and text[i] in "|&;<>":
                i += 1
            yield start, i, text[start:i], False
            continue
        quote = ""
        glob = False
        while i < len(text):
            c = text[i]
            if not quote and (c.isspace() or c in "|&;<>"):
                break
            if c == "\\" and quote != "'" and i + 1 < len(text):
                i += 2
                continue
            if c in "\"'":
                if quote == c:
                    quote = ""
                elif not quote:
                    quote = c
            elif not quote and c in "*?[{":
                glob = True
            i += 1
        yield start, i, text[start:i], glob


def substitute_reference(text, variable, value):
    result = []
    quote = ""
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and quote != "'" and i + 1 < len(text):
            result.append(text[i : i + 2])
            i += 2
            continue
        if c in "\"'":
            if quote == c:
                quote = ""
            elif not quote:
                quote = c
        if c == "$" and quote != "'":
            match = re.match(
                r"\$(?:" + variable + r"\b|\{" + variable + r"\})", text[i:]
            )
            if match:
                result.append(value)
                i += len(match[0])
                continue
        result.append(c)
        i += 1
    return "".join(result)


def guarded_shell(command, executable, session_key=""):
    # A reported path lookup: f=$(rg -l PATTERN dir); sed ... $f. The
    # substitution producer is proven inspection; consumers check expanded argv.
    match = re.fullmatch(
        r"(?P<var>f|file|files|F)=\$\((?P<source>[^$`()\n]+)\);\s*(?P<body>.+)",
        command,
        re.S,
    )
    if match:
        source = match["source"]
        body = match["body"]
        variable = match["var"]
        try:
            source_words = shlex.split(source)
        except ValueError:
            return None
        if (
            not source_words
            or Path(source_words[0]).name != "rg"
            or not any(
                w in {"-l", "--files-with-matches", "--files"} for w in source_words
            )
            or not light_shell(source)
        ):
            return None
        if not light_shell(
            substitute_reference(body, variable, "/MEMCAP_PATH"), allow_bare_globs=True
        ):
            return None
        guarded = guard_stages(
            normalized_lines(body), executable, session_key, variable
        )
        if not guarded:
            return None
        return variable + "=$(" + source + "); " + guarded
    return guard_literal(command, executable, session_key)


def guard_literal(command, executable, session_key):
    # Only the already-proven narrow inspection grammar gains relaxed glob
    # classification. Every expanded external stage is checked again at runtime.
    if light_shell(command) or not light_shell(command, allow_bare_globs=True):
        return None
    return guard_stages(normalized_lines(command), executable, session_key)


def guard_stages(text, executable, session_key, variable=None):
    stages = []
    stage = []
    for word in spans(text):
        if word[2] in {"|", "&&", "||", ";"}:
            if stage:
                stages.append(stage)
            stage = []
        else:
            stage.append(word)
    if stage:
        stages.append(stage)
    inserts = []
    for stage in stages:
        if not any(
            word[3]
            or (
                variable
                and ("$" + variable in word[2] or "${" + variable + "}" in word[2])
            )
            for word in stage
        ):
            continue
        index = 0
        while (
            index < len(stage)
            and "=" in stage[index][2]
            and not stage[index][2].startswith(('"', "'"))
        ):
            index += 1
        if index == len(stage):
            return None
        try:
            words = shlex.split(stage[index][2])
        except ValueError:
            return None
        if len(words) != 1:
            return None
        name = Path(words[0]).name
        if name == "printf":
            return None  # expanded -v could assign variables in the caller shell
        if name in {"cd", "echo", "true", "false"}:
            # These builtins cannot spawn an expansion-supplied command. Keeping
            # cd in the original shell preserves directory changes across stages.
            continue
        if name not in {
            "rg",
            "grep",
            "egrep",
            "fgrep",
            "cat",
            "head",
            "tail",
            "ls",
            "wc",
            "git",
            "env",
            "ps",
            "sed",
            "aws",
            "gh",
        }:
            return None
        prefix = (
            shlex.join([executable, "_inspect", "--session-key", session_key, "--"])
            + " "
        )
        inserts.append((stage[index][0], prefix))
    if not inserts:
        return None
    for offset, prefix in reversed(inserts):
        text = text[:offset] + prefix + text[offset:]
    return text
