"""Bounded demand inspection for tool wrappers. No target tool is executed."""
import re
import time


def options(args, valued=(), flags=()):
    """Consume only understood leading options; ambiguous values stay unknown."""
    args = list(args)
    while args and args[0].startswith('-'):
        arg = args.pop(0)
        if arg == '--':
            return args
        key, equal, _ = arg.partition('=')
        if key in valued:
            if not equal:
                if not args:
                    return None
                args.pop(0)
        elif arg not in flags:
            return None
    return args


def task_file(worker, cwd, names):
    # Same bounded dependency reader/fingerprint as local shell/package scripts.
    if time.monotonic() > worker.deadline:
        worker.uncertain = True
        return None, None
    for directory in (cwd, *list(cwd.parents)[:7]):
        for name in names:
            path = directory / name
            try:
                if path.is_file():
                    return path, worker.text(path)
            except (OSError, RuntimeError):
                worker.uncertain = True
                return None, None
    worker.uncertain = True
    return None, None


def mise_task(worker, cwd, name, depth):
    if depth > 8:
        worker.uncertain = True
        return None
    path, text = task_file(worker, cwd, ('mise.toml', '.mise.toml'))
    if text is None:
        return None
    try:
        import tomllib
        task = tomllib.loads(text).get('tasks', {}).get(name)
    except (ImportError, ValueError, TypeError, AttributeError):
        worker.uncertain = True
        return None
    if isinstance(task, str):
        return worker.shell(task, path.parent, depth + 1)
    if not isinstance(task, dict):
        worker.uncertain = True
        return None
    directory = path.parent
    if isinstance(task.get('dir'), str) and '{{' not in task['dir']:
        directory /= task['dir']
    for dependency in task.get('depends', []) if isinstance(task.get('depends', []), list) else []:
        if isinstance(dependency, str):
            reason = mise_task(worker, directory, dependency, depth + 1)
            if reason:
                return reason
    body = task.get('run', [])
    for command in [body] if isinstance(body, str) else body if isinstance(body, list) else []:
        if isinstance(command, str):
            reason = worker.shell(command, directory, depth + 1)
            if reason:
                return reason
    worker.uncertain = True  # Templates/plugins/imports are not evaluated.
    return None


def just_task(worker, cwd, name, depth, filename=None):
    if depth > 8:
        worker.uncertain = True
        return None
    if filename:
        path = cwd / filename
        text = worker.text(path)
    else:
        path, text = task_file(worker, cwd, ('justfile', 'Justfile', '.justfile'))
    if text is None:
        return None
    recipes, current = {}, None
    for line in text.splitlines():
        if line[:1].isspace():
            if current:
                recipes[current][1].append(line.lstrip().lstrip('@-'))
            continue
        match = re.fullmatch(r'([A-Za-z_][\w-]*)(?:\s+[^:]*)?:\s*(?![=])([^=]*)', line)
        if match:
            current = match[1]
            recipes[current] = (match[2], [])
        elif line and not line.startswith('#'):
            current = None
    selected = recipes.get(name or next(iter(recipes), ''))
    if selected:
        deps, lines = selected
        for dep in deps.split():
            if dep in recipes:
                reason = just_task(worker, path.parent, dep, depth + 1, path.name)
                if reason:
                    return reason
        return worker.shell('\n'.join(lines), path.parent, depth + 1)
    worker.uncertain = True
    return None


def inspect_tool(worker, name, args, cwd, depth):
    """Return (handled, positive evidence); None alone never certifies low demand."""
    own_args = args[:args.index('--')] if '--' in args else args
    if name in {'gotestsum', 'staticcheck', 'deadcode', 'vhs', 'air', 'trivy', 'k6',
                'whisper-cli', 'whisper-server', 'fastlane', 'pod', 'sqlc', 'eas'}:
        if any(arg in {'--help', '-h', '--version'} for arg in own_args):
            return True, None
    if name == 'mise':
        args = list(args)
        while len(args) > 1 and args[0] in {'-C', '--cd'}:
            cwd /= args[1]
            args = args[2:]
        if not args:
            return True, None
        action, rest = args[0], args[1:]
        if action in {'exec', 'x'}:
            if '--' in rest:
                return True, worker.argv(rest[rest.index('--')+1:], cwd, depth + 1)
            worker.uncertain = True
            return True, None
        if action in {'run', 'r'}:
            if any(a in {'--help', '-h', '--dry-run', '-n'} for a in rest[:1]):
                return True, None
            return True, mise_task(worker, cwd, rest[0], depth + 1) if rest and not rest[0].startswith('-') else None
        return True, 'known-workload' if action in {'install', 'upgrade'} and not any(a in {'--help', '-h', '--dry-run'} for a in rest) else None
    if name == 'just':
        if any(a in {'--list', '-l', '--summary', '--show', '--dump', '--evaluate', '--dry-run', '-n', '--help', '-h', '--version'} for a in own_args):
            return True, None
        args, filename = list(args), None
        while len(args) > 1 and args[0] in {'-f', '--justfile', '-d', '--working-directory'}:
            if args[0] in {'-f', '--justfile'}:
                filename = args[1]
            else:
                cwd /= args[1]
            args = args[2:]
        if args and args[0].startswith('-'):
            worker.uncertain = True
            return True, None
        return True, just_task(worker, cwd, args[0] if args else '', depth + 1, filename)
    if name == 'hyperfine':
        valued = {'-w', '--warmup', '-r', '--runs', '-m', '--min-runs', '-M', '--max-runs',
                  '-n', '--command-name', '--export-json', '--export-csv', '--export-markdown',
                  '--export-asciidoc', '--style', '-S', '--shell', '--time-unit'}
        executable = {'-p', '--prepare', '--setup', '-c', '--cleanup'}
        remaining = list(args)
        while remaining:
            arg = remaining.pop(0)
            key, equal, value = arg.partition('=')
            if key in valued | executable:
                value = value if equal else remaining.pop(0) if remaining else ''
                if key in executable:
                    reason = worker.shell(value, cwd, depth + 1)
                    if reason:
                        return True, reason
            elif arg.startswith('-'):
                if arg not in {'--', '-i', '--ignore-failure', '-N'}:
                    worker.uncertain = True
                    return True, None
            else:
                reason = worker.shell(arg, cwd, depth + 1)
                if reason:
                    return True, reason
        return True, None
    if name == 'docker':
        rest = options(args, {'--context', '-c', '--host', '-H', '--config', '--log-level', '-l',
                              '--tlscacert', '--tlscert', '--tlskey'}, {'--debug', '-D', '--tls', '--tlsverify'})
        if rest != args:
            if rest is None:
                worker.uncertain = True
                return True, None
            return True, worker.argv(['docker', *rest], cwd, depth + 1)
        return False, None
    if name == 'gotestsum':
        if '--raw-command' in own_args and '--' in args:
            return True, worker.argv(args[args.index('--')+1:], cwd, depth + 1)
        return True, None if args[:1] == ['tool'] else 'known-workload'
    if name in {'staticcheck', 'deadcode', 'vhs'}:
        return True, None if args[:1] in (['-version'], ['version'], ['-list-checks']) else 'known-workload'
    if name == 'air':
        return True, None if args[:1] in (['init'], ['-v'], ['version']) else 'known-workload'
    if name == 'trivy':
        rest = options(args, {'--cache-dir', '--config', '--timeout'}, {'--quiet', '-q', '--debug'})
        return True, 'known-workload' if rest and rest[0] in {'fs', 'filesystem', 'repo', 'repository', 'image', 'rootfs', 'sbom'} else None
    if name == 'k6':
        return True, 'known-workload' if args[:1] == ['run'] else None
    if name in {'whisper-cli', 'whisper-server'}:
        return True, 'known-workload' if args and not any(a in {'-h', '--help', '--version'} for a in args) else None
    if name == 'ffmpeg':
        informational = {'-version', '-h', '-help', '--help', '-formats', '-codecs', '-encoders',
                         '-decoders', '-filters', '-devices', '-protocols', '-buildconf'}
        return True, 'known-workload' if args and not any(a in informational for a in args) else None
    if name == 'fastlane':
        return True, 'known-workload' if args and args[0] not in {'help', 'lanes', 'actions', 'env', '--help', '--version'} else None
    if name == 'pod':
        return True, 'known-workload' if args[:1] in (['install'], ['update']) else None
    if name == 'sqlc':
        return True, 'known-workload' if args[:1] in (['generate'], ['compile']) else None
    if name == 'eas':
        return True, 'known-workload' if args[:1] == ['build'] and '--local' in args else None
    if name in {'orb', 'orbctl'}:
        # Orb runs commands in a local Linux machine, not a remote host. Container
        # management itself remains native except for explicit VM startup.
        if args[:1] in (['start'], ['restart']):
            return True, 'container-workload'
        rest = options(args, {'-m', '--machine', '-u', '--user', '-w', '--workdir', '-a', '--arch'})
        if rest and (args[:1] == ['run'] or rest != args):
            return True, worker.argv(rest[1:] if rest[:1] == ['run'] else rest, cwd, depth + 1)
        if name == 'orb' and args and args[0] in {'go', 'cargo', 'make', 'npm', 'python', 'python3'}:
            return True, worker.argv(args, cwd, depth + 1)
        worker.uncertain = True
        return True, None
    return False, None
