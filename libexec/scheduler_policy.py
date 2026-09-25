"""Conservative launch classification and bounded, documented worker controls."""

from __future__ import annotations

import json
import hashlib
import os
import re
from pathlib import Path
import shlex


POLL_GUIDANCE = (
    "memcap refused a synthetic waiting loop; no workload was submitted. "
    "Do not retry this Bash command or create another drain tick/sleep loop. "
    "Use TaskOutput block=true timeout=60000 on the ORIGINAL workload's existing task "
    "ID (not this refused wait), or its blocking tool-session poll. "
    "If TaskOutput is unavailable, run memcap wait JOB_ID --timeout 60 using the existing memcap job ID from memcap queue. "
    "If that task exited, read its final result; only then submit the actual work once."
    " Report this productivity incident once with memcap report polling-overhead; "
    "do not create a reporting loop or enable reporting yourself."
)


def polling_loop(command):
    """Only known waiting-only scripts; never infer from a command description."""
    tick = re.fullmatch(
        r"end=\$\(\(SECONDS\+([1-9][0-9]?)\)\)[;\s]*"
        r"while \[ \$SECONDS -lt \$end \]; do sleep ([1-9][0-9]?); done[;\s]*"
        r'echo (?:tick|"drain tick done"|"poll tick")',
        command.strip(),
    )
    if tick:
        return int(tick[1]) <= 60 and int(tick[2]) <= 60
    file_tick = re.fullmatch(
        r"f=/(?:private/)?tmp/claude-[0-9]+/[A-Za-z0-9_./-]+/tasks/[A-Za-z0-9_-]+\.output[;\s]+"
        r"end=\$\(\(SECONDS\+([1-9][0-9]?)\)\)[;\s]+"
        r"while \[ \$SECONDS -lt \$end \]; do\s+"
        r'grep -q "[A-Za-z0-9_ |\\-]+" "\$f" 2>/dev/null && break[;\s]+'
        r'sleep ([1-9][0-9]?)[;\s]+done[;\s]+tail -n ([1-9][0-9]?) "\$f"',
        command.strip(),
    )
    if file_tick:
        return all(int(n) <= 60 for n in file_tick.groups())
    return bool(
        re.fullmatch(
            r"F=/(?:private/)?tmp/claude-[0-9]+/[A-Za-z0-9_./-]+/tasks/[A-Za-z0-9_-]+\.output; "
            r'until \[ -s "\$F" \] && ! (?:grep|rg) -q "memcap: queued" "\$F" 2>/dev/null; '
            r'do sleep [1-9][0-9]?; done; echo "[A-Za-z0-9 =_-]+"; cat "\$F"',
            command.strip(),
        )
    )


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
    # Literal, non-executing environment prefixes only. In particular BASH_ENV,
    # LD_PRELOAD, and arbitrary env options must not open an execution escape.
    words = list(words)
    if Path(words[0]).name == "env":
        words.pop(0)
    while words and re.fullmatch(
        r"(?:LC_ALL|LANG|AWS_PROFILE|AWS_REGION|AWS_DEFAULT_REGION|AWS_PAGER)=[^\n]*",
        words[0],
    ):
        if words[0].startswith("AWS_PAGER=") and words[0] != "AWS_PAGER=":
            return False
        words.pop(0)
    if not words:
        return False
    if re.match(r"[A-Za-z_][A-Za-z_0-9]*=", words[0]):
        return False  # never mistake an unapproved env assignment for argv[0]
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
    if any("__MEMCAP_READ_SUBSTITUTION__" in w for w in words) and name != "aws":
        # Unquoted substitution can turn an rg argument into --pre, for example.
        # Only the fixed SSM API operations below may receive these values.
        return False
    from lightweight import extra_family

    family = extra_family(name, words[1:])
    if family is not None:
        return family
    if name == "aws":
        args = words[1:]
        while args and args[0].startswith("--"):
            if args[0] in {"--no-cli-pager", "--no-cli-auto-prompt"}:
                args = args[1:]
            elif (
                args[0] in {"--profile", "--region", "--output", "--query"}
                and len(args) > 1
            ):
                args = args[2:]
            else:
                return False
        if args[:2] == ["logs", "tail"]:
            return not any(
                a == "--follow" or a.startswith("--follow=") for a in args[2:]
            )
        if args[:2] == ["cloudwatch", "describe-alarms"]:
            return True
        if args[:2] == ["sts", "get-caller-identity"]:
            return True
        if args[:2] == ["ec2", "describe-instances"]:
            return True
        # These are API control calls; remote script contents do not execute on
        # this host. Interactive sessions and arbitrary AWS transfers still queue.
        return (
            len(args) >= 2
            and args[0] == "ssm"
            and (
                args[1]
                in {
                    "send-command",
                    "get-command-invocation",
                    "list-command-invocations",
                    "list-commands",
                }
                or args[1:3] == ["wait", "command-executed"]
            )
        )
    if name == "git":
        while len(words) > 2 and words[1] == "-C":
            words = [words[0]] + words[3:]
    # The diagnostic next steps must remain usable while build capacity is full.
    # Exact read-only forms only: no streaming stats, bootstrap or device changes.
    if (
        name == "xcrun"
        and words[1:4] == ["simctl", "list", "devices"]
        and all(word in {"available", "--json", "-j"} for word in words[4:])
    ):
        return True
    if name == "adb" and words[1:] in (["devices"], ["devices", "-l"]):
        return True
    if name in {"cut", "uniq"}:
        return True
    if name == "xargs" and words[1:] == ["wc", "-l"]:
        # wc streams counts and has no command-execution option. No arbitrary
        # child program or xargs option (including parallelism) is accepted.
        return True
    if name == "sort":
        return not any(w.startswith("--co") for w in words[1:])
    if name in {"fd", "fdfind"}:
        return not any(
            w.startswith(("--exec", "-x", "-X"))
            or (
                w.startswith("-")
                and not w.startswith("--")
                and any(c in w[1:] for c in "xX")
            )
            for w in words[1:]
        )
    if name == "awk" and len(words) >= 3:
        # One reported log excerpt idiom, not general awk (system/getline execute).
        return bool(
            re.fullmatch(
                r"/[^/\n]+/\{p=1\} p\{print\} /[^/\n]+/\{if\(p\) exit\}", words[1]
            )
        ) and all(not w.startswith("-") and "=" not in w for w in words[2:])
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
    if name == "printf" and "-v" in words[1:]:
        return False
    if name in {
        "ps",
        "pgrep",
        "tr",
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
        and words[1]
        in {
            "status",
            "diff",
            "log",
            "show",
            "rev-parse",
            "ls-files",
            "ls-tree",
            "rev-list",
            "check-ignore",
            "check-attr",
            "for-each-ref",
            "describe",
            "show-ref",
        }
    ):
        if not any(
            a.startswith(("--ext-diff", "--textconv", "--output", "--exec"))
            for a in words[2:]
        ):
            return True
    if name == "cd" and len(words) == 2:
        return True
    if name == "sleep" and len(words) == 2:
        return (
            bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", words[1]))
            and float(words[1]) <= 60
        )
    if name == "memcap" and len(words) >= 2:
        if words[1] in {"--version", "-v", "--help", "-h"}:
            return len(words) == 2
        if words[1] == "_inspect":
            # This runtime guard either execs proven inspection or enters the queue.
            return True
        if words[1] == "report":
            from report import KINDS, CONTEXTS, SYMPTOMS

            if len(words) < 3:
                return False
            if words[2] in {"status", "enable", "disable", "--help", "-h"}:
                return len(words) == 3
            if words[2] not in KINDS:
                return False
            i, seen = 3, set()
            while i < len(words):
                option = words[i]
                if option in seen:
                    return False
                seen.add(option)
                if option == "--dry-run":
                    i += 1
                elif (
                    option == "--symptom"
                    and i + 1 < len(words)
                    and words[i + 1] in SYMPTOMS
                ):
                    i += 2
                elif (
                    option == "--context"
                    and i + 1 < len(words)
                    and words[i + 1] in CONTEXTS
                ):
                    i += 2
                elif (
                    option == "--wait-seconds"
                    and i + 1 < len(words)
                    and re.fullmatch(r"[0-9]{1,6}", words[i + 1])
                    and int(words[i + 1]) <= 604800
                ):
                    i += 2
                else:
                    return False
            return True
        if words[1] == "wait":
            # The read-only CLI validates usage and cannot launch work. Even
            # malformed timeouts/IDs must fail immediately, outside admission.
            return True
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
    if name == "jq":
        args = words[1:]
        while args and (
            re.fullmatch(r"-[rceM]+", args[0])
            or args[0]
            in {
                "--raw-output",
                "--compact-output",
                "--exit-status",
                "--monochrome-output",
            }
        ):
            args = args[1:]
        if not args or any(w.startswith("-") for w in args[1:]):
            return False
        # Finite selectors/formatters only, never filter files, recursion, input
        # generators, module loading, or arbitrary jq programs.
        if "\\(" in args[0]:
            return False  # jq string interpolation can hide generators/recursion.
        expression = re.sub(r'"(?:[^"\\]|\\.)*"', '""', args[0])
        if ".." in expression or not re.fullmatch(
            r'[\w.\[\]()|,:/?!@<>=+\s"-]+', expression
        ):
            return False
        names = re.findall(r"(?<![\w.])([A-Za-z_][A-Za-z_0-9]*)", expression)
        return all(
            n
            in {
                "keys",
                "length",
                "join",
                "sort",
                "sort_by",
                "unique",
                "tostring",
                "select",
                "has",
                "type",
                "null",
                "true",
                "false",
                "tsv",
                "csv",
                "json",
                "text",
                "empty",
                "not",
                "and",
                "or",
            }
            for n in names
        )
    if name == "gh" and len(words) >= 3 and words[1] == "api":
        return not any(a == "--slurp" for a in words[2:])
    if name == "gh" and len(words) >= 3 and words[1] in {"issue", "pr"}:
        return words[2] in {"list", "view", "status", "checks"} and not any(
            word == "-w" or word.startswith("--web") for word in words[3:]
        )
    if name == "gh" and words[1:] == ["auth", "status"]:
        return True
    if name == "gh" and len(words) >= 3 and words[1] == "workflow":
        return words[2] in {"list", "view", "run", "enable", "disable"} and not any(
            word == "-w" or word.startswith("--web") for word in words[3:]
        )
    if name == "gh" and len(words) >= 3 and words[1] == "run":
        return words[2] in {"watch", "view", "list", "cancel"} and not any(
            word == "-w" or word.startswith("--web") for word in words[3:]
        )
    return False


def literal_shell(command: str, allow_bare_globs=False) -> bool:
    """Check expansions before shlex discards whether a regex/glob was quoted."""
    quote = ""
    prefix = ""
    escaped = False
    i = 0
    while i < len(command):
        c = command[i]
        if c == "\r":
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
            elif (
                c == "$"
                and not re.match(r"[A-Za-z0-9_({\[*@$!#-]", command[i + 1 : i + 2])
                and not (not quote and command[i + 1 : i + 2] in {"'", '"'})
            ):
                # A regex end anchor, e.g. "^\\s*$", is literal shell text.
                prefix += c
            else:
                return False
        elif not quote and c in "()":
            return False
        elif not quote and c in "*?[":
            if "/" not in prefix and not allow_bare_globs:
                return False
            prefix += c
        elif not quote and c == "{" and allow_bare_globs and "/" in prefix:
            brace = re.match(r"\{[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)+\}", command[i:])
            if not brace:
                return False
            prefix += brace[0]
            i += len(brace[0]) - 1
        elif not quote and c == "~" and not prefix and command[i + 1 : i + 2] == "/":
            prefix = "/"  # Only current-home pathname expansion, never ~user.
        elif not quote and c in "{}~":
            return False
        elif not quote and (c.isspace() or c in "|&;<>()"):
            prefix = ""
        else:
            prefix += c
        i += 1
    return not quote and not escaped


def normalized_lines(command: str) -> str:
    """Normalize separators for classification only; never change execution text.

    Quotes retain literal newlines. A backslash-newline is continuation except
    inside single quotes. Only an unquoted # at the start of a word is a comment;
    quotes inside comments must not hide executable lines from classification.
    """
    result, quote, i = [], "", 0
    word_start = True
    while i < len(command):
        char = command[i]
        if char == "#" and not quote and word_start:
            while i < len(command) and command[i] != "\n":
                i += 1
            continue
        if char == "\\" and quote != "'" and i + 1 < len(command):
            if command[i + 1] != "\n":
                result.extend(command[i : i + 2])
                word_start = False
            i += 2
            continue
        if char in "\"'":
            if char == quote:
                quote = ""
            elif not quote:
                quote = char
            word_start = False
        elif not quote:
            word_start = char.isspace() or char in "|&;<>()"
        result.append(" ; " if char == "\n" and not quote else char)
        i += 1
    return "".join(result)


def light_file_loop(command: str) -> bool:
    """Recognize the reported finite 'for file; test -f; read' idiom only.

    Literal filenames cannot become options or shell syntax. Checking the prefix
    and body independently also proves the matched loop boundaries are unquoted.
    No loop runs during classification, and arbitrary shell loops still queue.
    """
    plain = re.fullmatch(
        r"(?P<prefix>.*?)(?:^|;)\s*for (?P<var>[A-Za-z_][A-Za-z_0-9]*) in "
        r"(?P<files>[A-Za-z0-9_./ -]+);\s*do\s+(?P<body>.*);\s*done\s*;?\s*",
        command,
        re.S,
    )
    if plain:
        from inspection import substitute_reference

        files = plain["files"].split()
        if (
            1 <= len(files) <= 64
            and not any(f.startswith("-") for f in files)
            and not re.search(r"\b(?:for|while)\s", plain["body"])
            and (not plain["prefix"].strip() or light_shell(plain["prefix"]))
            and all(
                light_shell(substitute_reference(plain["body"], plain["var"], f))
                for f in files
            )
        ):
            return True
    match = re.fullmatch(
        r"(?P<prefix>.*?)(?:^|;)\s*for (?P<var>[A-Za-z_][A-Za-z0-9_]*) in "
        r"(?P<files>[A-Za-z0-9_./ -]+);\s*do\s+\[ -f \$(?P=var) \] && \{ "
        r"(?P<body>.*);\s*\};\s*done\s*;?\s*",
        command,
        re.S,
    )
    if not match:
        return False
    prefix, variable, files, body = (
        match[k] for k in ("prefix", "var", "files", "body")
    )
    files = files.split()
    if not 1 <= len(files) <= 16 or any(f.startswith("-") for f in files):
        return False
    if prefix.strip() and not light_shell(prefix):
        return False
    # No nested loops; each literal filename is checked independently. Preserve
    # single-quoted regex replacement strings such as '$1=$2'.
    if re.search(r"\bfor\s", body):
        return False
    for filename in files:
        expanded, quote, i = [], "", 0
        while i < len(body):
            char = body[i]
            if char == "\\" and quote != "'" and i + 1 < len(body):
                expanded.append(body[i : i + 2])
                i += 2
                continue
            if char in "\"'":
                if char == quote:
                    quote = ""
                elif not quote:
                    quote = char
            if quote != "'" and char == "$":
                reference = re.match(
                    r"\$(?:" + variable + r"\b|\{" + variable + r"\})", body[i:]
                )
                if reference:
                    expanded.append(filename)
                    i += len(reference[0])
                    continue
            expanded.append(char)
            i += 1
        if not light_shell("".join(expanded)):
            return False
    return True


def literal_excerpt_helper(command: str) -> bool:
    """One fixed read-only helper and a bounded literal list of file/line calls."""
    match = re.fullmatch(
        r"(?P<prefix>.*?)(?:^|;|&&)\s*(?P<name>[A-Za-z_][A-Za-z_0-9]*)\(\)\s*\{\s*"
        r'echo "=== \$1:\$2";\s*sed -n "\$\(\(\s*\$2-2\s*\)\),'
        r'\$\(\(\s*\$2\+2\s*\)\)p" "\$1";\s*\};\s*(?P<calls>[^\n]+)',
        command,
        re.S,
    )
    if not match or match["name"] in {"echo", "sed"}:
        return False  # Those names would recurse inside the otherwise fixed body.
    prefix = match["prefix"].strip()
    if prefix and not light_shell(prefix):
        return False
    calls = match["calls"].rstrip("; ").split(";")
    if not 1 <= len(calls) <= 64:
        return False
    for call in calls:
        invocation = re.fullmatch(
            re.escape(match["name"]) + r" ([A-Za-z0-9_./-]+) ([0-9]{1,7})",
            call.strip(),
        )
        if not invocation or invocation[1].startswith("-") or int(invocation[2]) < 3:
            return False
    return True


def literal_note(command: str) -> bool:
    """A bounded quoted cat heredoc is literal data, never executable shell.

    Validate the complete prefix, header and optional lightweight suffix. Reject
    early delimiters so executable commands cannot hide in the literal body.
    """
    if len(command) > 65536:
        return False
    match = re.fullmatch(
        r"(?P<prefix>[^\n]*[;]\s*)?(?P<header>cat\s+>{1,2}\s+[^\n]+?)\s+<<(?P<quote>['\"])(?P<delimiter>[A-Za-z_][A-Za-z_0-9]*)(?P=quote)\n(?P<body>.*?)\n(?P=delimiter)(?:\n(?P<suffix>.*))?",
        command,
        re.S,
    )
    if not match or match["delimiter"] in match["body"].splitlines():
        return False
    prefix = (match["prefix"] or "").rstrip("; ")
    suffix = (match["suffix"] or "").strip()
    return (
        (not prefix or light_shell(prefix))
        and light_shell(match["header"])
        # Do not recursively accept chains of heredocs through this fast path.
        and (not suffix or ("<<" not in suffix and light_shell(suffix)))
    )


def light_shell(command: str, allow_bare_globs=False) -> bool:
    # Recognize a narrow shell grammar solely for lightweight commands. Every
    # stage must qualify. Never evaluate substitutions or reconstruct the input.
    if literal_excerpt_helper(command) or literal_note(command):
        return True
    command = normalized_lines(command)
    # Literal aliases commonly precede log reads. Prove the surrounding stages
    # independently; never evaluate arbitrary assignments or substitutions.
    from inspection import spans, substitute_reference

    boundary = 0
    for token in list(spans(command)) + [(len(command), len(command), ";", False)]:
        if token[2] not in {";", "&&"}:
            continue
        stage = command[boundary : token[0]].strip()
        from lightweight import local_variable

        binding = re.fullmatch(r"([A-Za-z_][A-Za-z_0-9]*)=([A-Za-z0-9_./-]+)", stage)
        if binding and local_variable(binding[1]):
            prefix = command[:boundary].strip().rstrip(";& ")
            body = command[token[1] :]
            if not body.strip():
                return False
            return (
                not prefix or light_shell(prefix, allow_bare_globs)
            ) and light_shell(
                substitute_reference(body, binding[1], binding[2]), allow_bare_globs
            )
        boundary = token[1]
    # A literal repo alias does not execute locally. Substitutions, arbitrary
    # environment assignments and heavy stages remain outside this grammar.
    repo = re.fullmatch(
        r"(?P<name>R|REPO)=(?P<quote>['\"]?)(?P<repo>[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*)(?P=quote)\s*&&\s*(?P<body>.+)",
        command,
    )
    if repo:
        from inspection import substitute_reference

        return light_shell(
            substitute_reference(repo["body"], repo["name"], repo["repo"]),
            allow_bare_globs,
        )
    # The reported finite directory-status loop uses quoted path operands only.
    # Keep this exact read-only idiom narrow; substitutions or changed bodies queue.
    if re.fullmatch(
        r"\s*ls;\s*for (?P<v>[A-Za-z_][A-Za-z0-9_]*) in \*/;\s*do "
        r'\[ -d "\$(?P=v)/\.git" \] && echo "GIT: \$(?P=v)" && '
        r'git -C "\$(?P=v)" status -sb \| head -[1-9][0-9]? && '
        r'git -C "\$(?P=v)" log --oneline -[1-9][0-9]?;\s*done\s*;?\s*',
        command,
    ):
        return True
    if light_file_loop(command):
        return True
    # SSM workflows commonly save a command ID, wait briefly for propagation,
    # then query it with $(cat /tmp/id). Do not generalize this to arbitrary
    # substitutions or consumers capable of executing expanded arguments.
    command = re.sub(
        r"\$\(\s*cat\s+(?:/(?:[A-Za-z0-9_./-]+)|\./[A-Za-z0-9_./-]+)\s*\)",
        "__MEMCAP_READ_SUBSTITUTION__",
        command,
    )
    if not literal_shell(command, allow_bare_globs):
        return False
    try:
        from inspection import spans

        source = list(spans(command))
        tokens = [shlex.split(word[2])[0] for word in source]
    except ValueError:
        return False
    separators = {"|", "&&", "||", ";"}
    redirects = {">", ">>", "<", ">&", "<&"}
    words: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        raw = source[i][2]
        if raw in separators:
            if token == ";" and not words:
                i += 1
                continue
            if not light_words(words, glob_checked=True):
                return False
            words = []
        else:
            if (
                raw.isdigit()
                and i + 1 < len(tokens)
                and source[i][1] == source[i + 1][0]
                and source[i + 1][2] in redirects
            ):
                i += 1
                token = tokens[i]
                raw = source[i][2]
            if raw in redirects:
                i += 1
                if i >= len(tokens):
                    return False
                target = tokens[i]
                if token in {">&", "<&"}:
                    if not (target.isdigit() or target == "-"):
                        return False
                elif not target or any(char in target for char in "|&;<>()*?[]"):
                    return False
            elif raw and all(char in "|&;<>()" for char in raw):
                return False
            else:
                words.append(token)
        i += 1
    return (
        light_words(words, glob_checked=True)
        if words
        else bool(tokens) and tokens[-1] == ";"
    )


def classify_shell(command: str) -> tuple[str, str]:
    if light_shell(command):
        return "light", ""
    words = simple_words(command)
    persistent = (
        persistent_shell(command)
        if not words or Path(words[0]).name not in {"npm", "pnpm", "yarn", "vite"}
        else ""
    )
    if persistent:
        return "resource", persistent
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


def persistent_shell(command):
    """One known persistent command with literal cd/env/redirection wrappers.

    It still goes through admission; only finite-job completion waits exclude it.
    A subsequent test/build or unknown shell expansion must remain a finite job.
    """
    text = normalized_lines(command).strip()
    if not literal_shell(text):
        return ""
    from inspection import spans

    tokens = list(spans(text))
    segments, words = [], []
    i = 0
    while i < len(tokens):
        raw = tokens[i][2]
        if raw in {";", "&&"}:
            segments.append(words)
            words = []
        elif raw in {">", ">>", "<", ">&", "<&"}:
            if (
                words
                and tokens[i - 1][2].isdigit()
                and tokens[i - 1][1] == tokens[i][0]
            ):
                words.pop()
            i += 1
            if i >= len(tokens):
                return ""
        elif raw in {"|", "||", "&"} or not raw:
            return ""
        else:
            try:
                words.append(shlex.split(raw)[0])
            except (ValueError, IndexError):
                return ""
        i += 1
    segments.append(words)
    if not all(len(s) == 2 and s[0] == "cd" for s in segments[:-1]):
        return ""
    words = segments[-1]
    while words and re.fullmatch(r"(?:PORT|HOST|NODE_ENV)=[A-Za-z0-9_.:-]+", words[0]):
        words = words[1:]
    if not words:
        return ""
    name = Path(words[0]).name
    args = words[1:]
    if name in {"npm", "pnpm", "yarn"}:
        if args[:1] in (["run"], ["run-script"]):
            args = args[1:]
        if args and args[0] in {"dev", "start"}:
            return "shell:" + text
    if name == "vite" and (not args or args[0].startswith("-")):
        return "shell:" + text
    if name == "adb":
        if args[:1] == ["-s"] and len(args) > 2:
            args = args[2:]
        if args[:1] == ["logcat"] and all(
            not a.startswith("-")
            or a in {"-v", "-b", "--pid", "--uid", "-s", "-T"}
            or a.startswith(("--pid=", "--uid="))
            for a in args[1:]
        ):
            return "shell:" + text
    return ""


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
    if polling_loop(command):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": POLL_GUIDANCE,
            }
        }
    kind, resource = classify_shell(command)
    from inspection import guarded_shell

    session_key = (
        hashlib.sha256(payload.get("session_id", "").encode()).hexdigest()
        if isinstance(payload.get("session_id"), str)
        else ""
    )
    guarded = guarded_shell(command, executable, session_key) if kind == "job" else None
    try:
        wrapped = shlex.split(command)
    except ValueError:
        wrapped = []
    # Only an entire canonical wrapper invocation bypasses reinsertion. A
    # wrapper followed by '; another-command' must still queue as a whole.
    already_wrapped = (
        len(wrapped) > 1
        and wrapped[0] in {executable, "memcap"}
        and wrapped[1] in {"run", "queue", "status", "feedback", "_inspect"}
        and shlex.join(wrapped) == command
    )
    control = simple_words(command)
    if kind == "light" and control and control[:2] == ["memcap", "wait"]:
        # Hooks already know their installed executable. A native wait must not
        # depend on the caller's PATH or the global Homebrew bin symlink.
        updated = {**original, "command": shlex.join([executable, *control[1:]])}
        updated.pop("cmd", None)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": updated,
            }
        }
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
    if agent == "claude" and not guarded:
        updated["run_in_background"] = True
    updated["command"] = guarded or shlex.join(args)
    # Codex's hook schema uses command even when its exec tool uses cmd.
    updated.pop("cmd", None)
    result = {"hookEventName": "PreToolUse", "updatedInput": updated}
    if agent == "claude" and not guarded:
        from report import PERFORMANCE_GUIDANCE

        result["additionalContext"] = (
            "memcap keeps this task queued until memory is available, then starts it automatically. "
            "Await native completion notifications without polling when supported. Otherwise use TaskOutput with block=true and timeout=60000 for one blocking wait of up to 60 seconds. If TaskOutput is unavailable, use memcap wait JOB_ID --timeout 60 with the existing ID from memcap queue; it creates no job or reservation. Repeat once per minute while pending; do not repeatedly read output files or emit holding messages. "
            "If Stop has blocked ending the turn, use that blocking wait instead of trying to finish for a notification. "
            "Do not create Bash sleep loops or drain ticks to wait. Do not submit duplicates, stop because it is queued, or bypass memcap. "
            "Read the final output and exit status before continuing dependent work."
            + " "
            + PERFORMANCE_GUIDANCE
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
