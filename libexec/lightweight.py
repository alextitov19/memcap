"""Low-memory command families. Classification never grants tool permission."""

import os
import re


def parameter_value(text):
    """Data-only parameters, including an empty/literal/variable default.

    No assignment, nested expansion, quoting, arithmetic or executable fallback.
    Actual expanded argv is still checked by the runtime inspection guard.
    """
    name = r"(?:[A-Za-z_][A-Za-z_0-9]*|[0-9]|\?)"
    default = r"(?:\$" + name + r"|\$\{" + name + r"\}|[A-Za-z_0-9./:@-]*)"
    return re.match(r"\$(?:\{" + name + r"(?::?-" + default + r")?\}|" + name + r")", text)


def local_variable(name):
    # Local aliases must not change command lookup, shell startup or an already
    # exported program setting. No special execution environment is rewritten.
    return (
        bool(re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name))
        and name not in os.environ
        and name
        not in {
            "PATH",
            "IFS",
            "ENV",
            "CDPATH",
            "SHELLOPTS",
            "BASHOPTS",
            "ZDOTDIR",
            "FPATH",
            "PS4",
            "PROMPT_COMMAND",
            "RANDOM",
            "SECONDS",
        }
        and not name.startswith(("BASH", "LD_", "DYLD_", "PYTHON", "MEMCAP", "MC_"))
    )


def sed_script(script):
    """Finite print/delete/quit and substitution scripts, without e/r/w branches."""
    address = r"(?:[0-9]+|\$|/(?:\\.|[^/\\\n])+/)"
    prefix = re.compile(r"(?:" + address + r"(?:," + address + r")?)?!?")
    pos = 0
    while pos < len(script):
        while pos < len(script) and script[pos] in " ;\t":
            pos += 1
        if pos == len(script):
            break
        pos = prefix.match(script, pos).end()
        if pos >= len(script):
            return False
        op = script[pos]
        pos += 1
        if op == "s":
            if pos >= len(script) or script[pos].isalnum() or script[pos] in "\\\n\r":
                return False
            delimiter = script[pos]
            pos += 1
            for _ in range(2):
                while pos < len(script) and script[pos] != delimiter:
                    if script[pos] in "\n\r":
                        return False
                    if script[pos] == "\\":
                        pos += 1
                    pos += 1
                if pos >= len(script):
                    return False
                pos += 1
            while pos < len(script) and script[pos] in "gIp0123456789":
                pos += 1
        elif op not in "pdq":
            return False
        if pos < len(script) and script[pos] not in " ;\t":
            return False
    return bool(script.strip(" ;\t"))


def sed_command(args):
    scripts, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg in {"-e", "--expression"}:
            if i + 1 >= len(args):
                return False
            scripts.append(args[i + 1])
            i += 2
        elif arg.startswith("--expression="):
            scripts.append(arg.split("=", 1)[1])
            i += 1
        elif arg == "-i":
            i += 1
            if i < len(args) and args[i] == "":
                i += 1  # macOS's explicit empty backup suffix
        elif arg.startswith("-i") or re.fullmatch(r"-[nEr]+", arg):
            i += 1
        elif arg == "--":
            i += 1
            break
        elif arg.startswith("-"):
            return False  # no script files or unknown execution-affecting options
        else:
            break
    if not scripts:
        if i >= len(args):
            return False
        scripts.append(args[i])
        i += 1
    return all(sed_script(s) for s in scripts) and all(
        not a.startswith("-") for a in args[i:]
    )


def extra_family(name, args):
    """Return None for families handled by the older classifier."""
    if name == "command":
        return len(args) >= 2 and args[0] in {"-v", "-V"} and all(not a.startswith("-") for a in args[1:])
    if name == "unzip":
        return len(args) >= 2 and args[0] in {"-p", "-l", "-t"} and all(not a.startswith("-") for a in args[1:])
    if name == "gofmt":
        # Formatting source does not execute it. Unknown flags, rewrite programs
        # and directory-wide invocations retain normal admission.
        files = []
        for arg in args:
            if arg in {"-w", "-l", "-d", "-s"}:
                continue
            if arg.startswith("-") or not arg.endswith(".go"):
                return False
            files.append(arg)
        return 1 <= len(files) <= 64
    if name == "paste":
        return True  # streaming text columns; no child-execution option
    if name == "lsof":
        return bool(args) and all(
            a in {"-n", "-P", "-nP", "-t", "-a", "-sTCP:LISTEN", "-sTCP:ESTABLISHED"}
            or re.fullmatch(r"-i(?:TCP|UDP)?(?::[0-9]{1,5})?", a)
            for a in args
        )
    if name == "awk":
        rest = list(args)
        if rest and rest[0] == "-F" and len(rest) > 1:
            rest = rest[2:]
        elif rest and rest[0].startswith("-F") and len(rest[0]) > 2:
            rest = rest[1:]
        if rest and not rest[0].startswith("-") and all(not a.startswith("-") and "=" not in a for a in rest[1:]):
            regex = r"/(?:\\.|[^/\\\n])*/"
            comparison = r"\$[1-9][0-9]?\s*(?:>=|<=|==|!=|>|<)\s*[0-9]{1,9}"
            patterns = (
                regex + r"(?:\s*,\s*" + regex + r")?",
                comparison + r"(?:\s*(?:&&|\|\|)\s*" + comparison + r"){0,3}",
                regex + r"\s*\{f=1;next\}\s*f\s*&&\s*" + regex + r"\s*\{exit\}\s*f",
                r"NR\s*(?:<=|<)\s*[0-9]{1,6}\s*&&\s*" + regex,
                r"NR>=[0-9]{1,6}\s*&&\s*" + regex + r"\{p=\$0\}\s*NR>=[0-9]{1,6}\s*&&\s*NR<=[0-9]{1,6}\s*&&\s*" + regex + r'\{print NR": "\$0\}',
            )
            if any(re.fullmatch(pattern, rest[0].strip()) for pattern in patterns):
                return True
    if name == "pdftotext":
        return True  # text extraction, no rasterization or child execution
    if name == "textutil":
        return args[:2] == ["-convert", "txt"] and "-stdout" in args
    if name in {"gzip", "gunzip", "base64"}:
        return True  # streaming transforms; no arbitrary child execution
    if name == "file":
        return not any(
            a.startswith("--uncompress")
            or (
                a.startswith("-")
                and not a.startswith("--")
                and any(c in a[1:] for c in "zZ")
            )
            for a in args
        )
    if name in {
        "cp",
        "mv",
        "rm",
        "mkdir",
        "rmdir",
        "touch",
        "ln",
        "chmod",
        "stat",
        "du",
        "df",
        "date",
        "readlink",
        "realpath",
        "basename",
        "dirname",
        "cmp",
        "diff",
        "test",
        "[",
    }:
        return True  # filesystem/metadata work, not arbitrary child execution
    if name == "sed":
        return sed_command(args)
    if name == "find":
        # All ordinary predicates/traversal options can compose. Actions that
        # execute children or destructive find programs remain managed.
        return bool(args) and not any(
            a
            in {
                "-exec",
                "-execdir",
                "-ok",
                "-okdir",
                "-delete",
                "-fprint",
                "-fprint0",
                "-fprintf",
            }
            for a in args
        )
    if name == "curl":
        return not any(
            a.startswith(("--parallel", "--config"))
            or (
                a.startswith("-")
                and not a.startswith("--")
                and any(c in a[1:] for c in "ZK")
            )
            for a in args
        )
    if name == "benmore":
        if args[:1] in (["docs"], ["check"], ["pull"]):
            if args[0] == "pull" and len(args) == 5 and args[3] == "--env":
                return all(not a.startswith("-") for a in [*args[1:3], args[4]])
            return 2 <= len(args) <= (3 if args[0] == "pull" else 2) and all(
                not a.startswith("-") for a in args[1:])
        if args[:1] == ["tail"]:
            rest = list(args[1:])
            if not rest or rest.pop(0).startswith("-"):
                return False
            while rest:
                if len(rest) < 2:
                    return False
                flag, value = rest[:2]
                if not ((flag == "--lines" and re.fullmatch(r"[1-9][0-9]{0,3}", value))
                        or (flag == "--since" and re.fullmatch(r"[0-9]{1,6}[smhd]", value))):
                    return False
                rest = rest[2:]
            return True
        if len(args) == 2 and args[1] in {"--help", "-h"} and re.fullmatch(r"[a-z][a-z-]*", args[0]):
            return True  # Help or an older CLI's usage error; neither runs a build.
        if args[:1] == ["push"]:
            rest, files = list(args[1:]), []
            while rest:
                if rest[0] in {"--app", "--env"} and len(rest) > 1 and not rest[1].startswith("-"):
                    rest = rest[2:]
                elif not rest[0].startswith("-") and re.search(r"\.(?:tsx?|jsx?|ya?ml|html|css|json|md|svg|txt|sql)$", rest[0]):
                    files.append(rest.pop(0))
                else:
                    return False
            return 1 <= len(files) <= 64
        return (
            bool(args)
            and args[0]
            in {
                "help",
                "--help",
                "-h",
                "version",
                "--version",
                "status",
                "logs",
                "sql",
                "env",
                "probe",
                "restart",
                "apps",
                "list",
                "whoami",
                "use",
                "describe",
            }
            and not any(
                a in {"-f", "--follow", "--watch"}
                or a.startswith(("--follow=", "--watch="))
                for a in args
            )
        )
    if name == "docker":
        if not args:
            return False
        if args[0] == "stats":
            rest = list(args[1:])
            if "--no-stream" not in rest:
                return False
            while rest:
                if rest[0] in {"--no-stream", "--no-trunc", "--all", "-a"}:
                    rest.pop(0)
                elif rest[0] == "--format" and len(rest) >= 2:
                    rest = rest[2:]
                elif not rest[0].startswith("-"):
                    rest.pop(0)
                else:
                    return False
            return True
        if args[0] == "compose":
            args = args[1:]
            while len(args) > 1 and args[0] in {
                "-f",
                "--file",
                "-p",
                "--project-name",
                "--project-directory",
            }:
                args = args[2:]
        if args and args[0] in {
            "ps",
            "inspect",
            "images",
            "version",
            "info",
            "port",
            "top",
            "config",
        }:
            return True
        if args and args[0] == "logs":
            return not any(
                a.startswith("--follow")
                or (a.startswith("-") and not a.startswith("--") and "f" in a[1:])
                for a in args[1:]
            )
    if name == "sips":
        rest, properties, files = list(args), 0, 0
        while rest:
            if rest[0] in {"-g", "--getProperty"} and len(rest) > 1 and rest[1] in {"pixelWidth", "pixelHeight", "format", "space", "hasAlpha"}:
                properties += 1
                rest = rest[2:]
            elif not rest[0].startswith("-"):
                files += 1
                rest.pop(0)
            else:
                return False
        return bool(properties and files)
    return None
