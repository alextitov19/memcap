"""Low-memory command families. Classification never grants tool permission."""

import os
import re


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
    return None
