"""Use the same private scope for hook admission and completion observation."""
import hashlib
import json
import shlex


def identity(payload):
    session = payload.get("session_id", "")
    if not isinstance(session, str):
        return ""
    agent = payload.get("agent_id")
    return (session, agent) if isinstance(agent, str) and agent else session


def identity_key(value):
    if isinstance(value, tuple):
        parent = hashlib.sha256(value[0].encode()).hexdigest()
        child = hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()
        return parent + "/" + child
    return hashlib.sha256(value.encode()).hexdigest() if value else ""


def key(payload):
    return identity_key(identity(payload))


def bind_runner(argv, session_key):
    """Replace runner metadata only, never an argument belonging to its child.

    Explicit/cached wrappers otherwise skip hook wrapping and lose ownership.
    Unknown options are left for the runner to reject, not guessed past.
    """
    if not session_key or len(argv) < 2 or argv[1] not in {"run", "_inspect"}:
        return None
    values = {"--classification-code", "--resource", "--memory", "--wait",
              "--cwd", "--session-key", "--shell", "--shell-command",
              "--analytics-operation", "--analytics-turn"}
    flags = {"--wait-forever", "--login"}
    result, i = [*argv[:2], "--session-key", session_key], 2
    while i < len(argv):
        arg = argv[i]
        if arg == "--" or not arg.startswith("-"):
            return result + argv[i:]
        option, equals, _ = arg.partition("=")
        if option in values:
            size = 1 if equals else 2
            if i + size > len(argv):
                return None
            if option != "--session-key":
                result.extend(argv[i:i + size])
            i += size
        elif arg in flags:
            result.append(arg)
            i += 1
        else:
            return None
    return result


def scope_waits(command, executable, session_key):
    """Scope waits inside proven lightweight chains, preserving redirections."""
    if not session_key:
        return None
    from inspection import spans
    from scheduler_policy import normalized_lines

    text = normalized_lines(command)
    stage, edits = [], []
    for token in list(spans(text)) + [(len(text), len(text), ";", False)]:
        if token[2] not in {";", "|", "&&", "||"}:
            stage.append(token)
            continue
        argv, i = [], 0
        redirects = {">", ">>", "<", ">&", "<&"}
        while i < len(stage):
            part = stage[i]
            if (part[2].isdigit() and i + 1 < len(stage)
                    and part[1] == stage[i + 1][0] and stage[i + 1][2] in redirects):
                i += 1
                part = stage[i]
            if part[2] in redirects:
                i += 2
                continue
            argv.append(part)
            i += 1
        stage = []
        try:
            words = [shlex.split(part[2])[0] for part in argv]
        except (ValueError, IndexError):
            continue
        if len(words) < 3 or words[0] not in {"memcap", executable} or words[1] != "wait" or "--session" not in words:
            continue
        edits.append((argv[0][0], argv[0][1], shlex.quote(executable)))
        edits.append((argv[1][1], argv[1][1], " --session-key " + shlex.quote(session_key)))
        i = 2
        while i < len(words):
            if words[i] == "--session-key" and i + 1 < len(words):
                edits.append((argv[i][0], argv[i + 1][1], ""))
                i += 2
            elif words[i].startswith("--session-key="):
                edits.append((argv[i][0], argv[i][1], ""))
                i += 1
            else:
                i += 1
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text if edits else None
