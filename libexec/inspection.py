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


def inspect_argv(argv, fallback, session_key=""):
    from text_probe import eligible
    from control_script import prepared

    script = prepared(
        argv, str(Path(__file__).resolve().parents[1] / "bin/memcap"), session_key
    )
    if script:
        os.execvpe(script[0], script, os.environ)

    probe = eligible(argv, check_files=True)
    if probe:
        # The recognized language uses only standard-library modules; isolate it
        # from PYTHONPATH and cwd modules that could run unrelated local code.
        argv = [argv[0], "-I", *argv[1:]]
    if probe or light_words(argv, glob_checked=True):
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


def guard_read_consumers(command, executable, session_key):
    """Guard the argv supplied by a filename pipe, never trust filenames as options."""
    prefix = (
        shlex.join([executable, "_inspect", "--session-key", session_key, "--"]) + " "
    )
    loop = re.fullmatch(
        r"(?P<before>.+)\|\s*while read (?P<var>[A-Za-z_][A-Za-z_0-9]*);\s*do "
        r"(?P<body>[^;\n]+);\s*done(?P<after>(?:;.*)?)",
        command,
        re.S,
    )
    if loop:
        body = loop["body"]
        try:
            words = shlex.split(body)
        except ValueError:
            return None
        if (
            not words
            or words[0] != "rg"
            or words[-1] != "$" + loop["var"]
            or not body.endswith('"$' + loop["var"] + '"')
            or not light_shell(substitute_reference(body, loop["var"], "/MEMCAP_PATH"))
            or not light_shell(loop["before"])
            or (
                loop["after"].strip("; ") and not light_shell(loop["after"].strip("; "))
            )
        ):
            return None
        return command[: loop.start("body")] + prefix + command[loop.start("body") :]
    # A single xargs -I{} rg invocation. Every surrounding stage must independently
    # qualify; insert the existing expanded-argv guard into each child invocation.
    tokens = list(spans(command))
    boundaries = [0]
    stages = []
    for token in tokens:
        if token[2] in {"|", ";", "&&", "||"}:
            stages.append((boundaries[-1], token[0]))
            boundaries.append(token[1])
    stages.append((boundaries[-1], len(command)))
    found = []
    for start, end in stages:
        stage = command[start:end].strip()
        try:
            words = shlex.split(stage)
        except ValueError:
            return None
        if words[:3] != ["xargs", "-I{}", "rg"]:
            continue
        if any(t[2] in {">", ">>", "<", ">&", "<&"} for t in spans(stage)):
            return None
        if words[-1:] != ["{}"] or sum(w.count("{}") for w in words[2:]) != 1:
            return None
        if not light_shell(shlex.join(words[2:-1] + ["/MEMCAP_PATH"])):
            return None
        # No substitutions, globs or other syntax can be hidden by shlex quoting.
        from scheduler_policy import literal_shell

        if not literal_shell(stage.replace("{}", "/MEMCAP_PATH")):
            return None
        found.append(
            (start, end, shlex.join(words[:2]) + " " + prefix + shlex.join(words[2:]))
        )
    if len(found) != 1:
        return None
    start, end, replacement = found[0]
    if not light_shell(command[:start] + " true " + command[end:]):
        return None
    return command[:start] + " " + replacement + " " + command[end:]


def guarded_shell(command, executable, session_key=""):
    from control_script import guard_invocation

    script = guard_invocation(command, executable, session_key)
    if script:
        return script
    text_probe = guard_text_probe(command, executable, session_key)
    if text_probe:
        return text_probe
    consumer = guard_read_consumers(command, executable, session_key)
    if consumer:
        return consumer
    substitutions = guard_substitutions(command, executable, session_key)
    if substitutions:
        return substitutions
    # A reported path lookup: f=$(rg -l PATTERN dir); sed ... $f. The
    # substitution producer is proven inspection; consumers check expanded argv.
    match = re.fullmatch(
        r"(?P<var>[A-Za-z_][A-Za-z_0-9]*)=\$\((?P<source>[^$`()\n]+)\);\s*(?P<body>.+)",
        command,
        re.S,
    )
    if match:
        from lightweight import local_variable

        if not local_variable(match["var"]):
            return None
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


def guard_text_probe(command, executable, session_key):
    from text_probe import eligible
    from scheduler_policy import literal_shell

    text = normalized_lines(command)
    if not literal_shell(text):
        return None
    tokens = list(spans(text))
    starts, pieces = [0], []
    for token in tokens:
        if token[2] in {";", "&&", "||", "|"}:
            pieces.append((starts[-1], token[0]))
            starts.append(token[1])
    pieces.append((starts[-1], len(text)))
    probes = []
    for start, end in pieces:
        stage = text[start:end].strip()
        try:
            words = shlex.split(stage)
        except ValueError:
            return None
        if eligible(words):
            probes.append((start, end, stage))
    if not probes:
        return None
    masked = text
    for start, end, _ in reversed(probes):
        masked = masked[:start] + " true " + masked[end:]
    if not light_shell(masked):
        return None
    prefix = shlex.join([executable, "_inspect", "--session-key", session_key, "--"])
    for start, end, stage in reversed(probes):
        text = text[:start] + " " + prefix + " " + stage + " " + text[end:]
    return text


def guard_literal(command, executable, session_key):
    # Only the already-proven narrow inspection grammar gains relaxed glob
    # classification. Every expanded external stage is checked again at runtime.
    if light_shell(command) or not light_shell(command, allow_bare_globs=True):
        return None
    return guard_stages(normalized_lines(command), executable, session_key)


def guard_stages(text, executable, session_key, variable=None, force=False):
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
        if not force and not any(
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
            if force:
                continue  # full light_shell proof already validated the binding
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
        # The complete shell was already proven lightweight. Reuse that same
        # family policy at runtime rather than maintaining a second name list.
        prefix = (
            shlex.join([executable, "_inspect", "--session-key", session_key, "--"])
            + " "
        )
        inserts.append((stage[index][0], prefix))
    if not inserts and not force:
        return None
    for offset, prefix in reversed(inserts):
        text = text[:offset] + prefix + text[offset:]
    return text


def substitution_end(text, start, depth):
    """Locate a $() boundary without evaluating it or confusing quoted ')'."""
    if depth > 8 or text.startswith("$((", start):
        return None
    quote, i = "", start + 2
    while i < len(text):
        c = text[i]
        if c == "\\" and quote != "'":
            i += 2
            continue
        if c in "\"'":
            if quote == c:
                quote = ""
            elif not quote:
                quote = c
        elif quote != "'" and text.startswith("$(", i):
            end = substitution_end(text, i, depth + 1)
            if end is None:
                return None
            i = end + 1
            continue
        elif not quote and c == ")":
            return i
        elif not quote and c in "(#":
            return None  # groups/arithmetic/comments need a fuller shell parser
        i += 1
    return None


def guard_substitutions(command, executable, session_key, depth=0):
    """Prove producers, then validate each consumer's actual expanded argv.

    Replacements are analysis markers only. Execution retains the original
    quoting, word splitting, glob expansion, redirections and producer count.
    """
    marker = "/__MEMCAP_EXPANSION_"
    if depth > 8 or len(command) > 65536 or marker in command:
        return None
    parts, replacements, quote, i = [], [], "", 0
    while i < len(command):
        c = command[i]
        if c == "\\" and quote != "'":
            parts.append(command[i : i + 2])
            i += 2
            continue
        if c in "\"'":
            if quote == c:
                quote = ""
            elif not quote:
                quote = c
        if quote != "'" and command.startswith("$(", i):
            end = substitution_end(command, i, depth)
            if end is None or len(replacements) >= 64:
                return None
            source = command[i + 2 : end]
            if light_shell(source):
                producer = source
            else:
                producer = guard_substitutions(
                    source, executable, session_key, depth + 1
                ) or guard_literal(source, executable, session_key)
            if producer is None:
                return None
            token = marker + str(len(replacements)) + "__"
            replacements.append((token, "$(" + producer + ")"))
            parts.append(token)
            i = end + 1
            continue
        parts.append(c)
        i += 1
    if not replacements:
        return None
    masked = "".join(parts)
    if not light_shell(masked, allow_bare_globs=True):
        return None
    rewritten = guard_stages(
        normalized_lines(masked), executable, session_key, force=True
    )
    if rewritten is None:
        return None
    for token, source in replacements:
        rewritten = rewritten.replace(token, source)
    return rewritten
