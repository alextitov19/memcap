"""Separate literal sequential shell stages without evaluating target code.

Everything outside this deliberately small grammar retains whole-command
admission. Builtins stay in the original shell. External argv are reclassified
at execution time, after cwd changes, using the ordinary runner.
"""
import shlex
import os
from pathlib import Path


def stable_shell(shell, login=False):
    return (shell in {'bash', 'sh', '/bin/bash', '/bin/sh'} and not login
            and not any(os.environ.get(k) for k in ('BASH_ENV', 'ENV'))
            and not any(k.startswith('BASH_FUNC_') for k in os.environ))


def split_command(command, executable, session_key='', wait=86400):
    from inspection import spans
    from scheduler_policy import literal_shell
    if not literal_shell(command) or any(c in command for c in '\n\r`$(){}<>*?[]#~'):
        return None
    tokens = list(spans(command))
    boundaries = [(start, end, word) for start, end, word, _ in tokens if word in {';', '&&', '||'}]
    if not boundaries or any(word and all(c in '|&;' for c in word) and word not in {';', '&&', '||'} for _, _, word, _ in tokens):
        return None
    parts, offset = [], 0
    for start, end, operator in boundaries + [(len(command), len(command), '')]:
        text = command[offset:start].strip()
        try:
            words = shlex.split(text)
        except ValueError:
            return None
        if not words or '=' in words[0] or words[0] in {'if', 'then', 'else', 'fi', 'for', 'while', 'until', 'case', 'function', 'eval', 'source', '.', 'exec', 'exit', 'return', 'trap', 'export', 'readonly', 'unset', 'alias', 'unalias', 'read', 'shopt', 'declare', 'typeset', 'local', 'let', 'shift', 'getopts', 'umask', 'ulimit', 'pushd', 'popd', 'dirs', 'wait', 'jobs', 'fg', 'bg', 'disown', 'hash', 'type', 'builtin', 'command', 'enable', 'break', 'continue', 'time'}:
            return None
        if words[0] in {'cd', 'pwd', 'true', 'false', ':', 'echo', 'printf'}:
            stage = text
        elif words[0] == 'set':
            if words[1:] not in (['-e'], ['-eu'], ['-euo', 'pipefail']):
                return None
            stage = text
        else:
            stage = shlex.join([executable, 'run', '--wait', str(wait), '--session-key', session_key, '--', *words])
        parts.append(stage + (' ' + operator if operator else ''))
        offset = end
    return ' '.join(parts)


def native_command(argv, cwd):
    from demand_policy import classify
    if len(argv) >= 3 and Path(argv[0]).name in {'bash', 'sh', 'zsh'} and argv[1] in {'-c', '-lc'}:
        command = argv[2]
    else:
        command = shlex.join(argv)
    return classify(command, cwd).kind == 'light'


def staged_script(argv, cwd, executable, session_key='', wait=86400):
    """Literal straight-line .sh files only; execute checked bytes, not the path."""
    if len(argv) < 2 or not stable_shell(argv[0]) or argv[1].startswith('-'):
        return None
    path = Path(cwd or '.').resolve() / argv[1]
    try:
        if path.suffix != '.sh' or path.stat().st_size > 256 * 1024:
            return None
        text = path.read_text()
    except (OSError, UnicodeError):
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    # Joining physical lines is only sound if quotes/escapes never span lines.
    for line in lines:
        try:
            shlex.split(line)
        except ValueError:
            return None
    transformed = split_command('; '.join(lines), executable, session_key, wait)
    return [argv[0], '-c', transformed, argv[1], *argv[2:]] if transformed else None
