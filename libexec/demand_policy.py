"""Memory demand evidence, not a shell authorization policy.

Only positive workload evidence enters admission. Unknown syntax is lightweight
with uncertain demand. Nothing here evaluates shell text, imports target modules,
executes scripts/hooks, grants permission, changes argv or signals a process.
"""
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import time

VERSION = 1
MAX_TEXT = 256 * 1024
MAX_DEPTH = 8
HEAVY = {
    'go': {'build', 'test', 'run', 'install', 'vet', 'generate'},
    'cargo': {'build', 'test', 'run', 'check', 'clippy', 'install', 'bench'},
    'swift': {'build', 'test', 'run', 'package'},
    'docker': {'build', 'run', 'start'},
    'podman': {'build', 'run', 'compose'},
    'playwright': {'test', 'install', 'codegen'},
    'next': {'build', 'dev', 'start'},
    'vite': {'build', 'dev', 'serve', 'preview'},
}
WORKLOADS = {'xcodebuild', 'make', 'gmake', 'cmake', 'ninja', 'gcc', 'g++',
             'clang', 'clang++', 'rustc', 'tsc', 'webpack', 'rollup', 'esbuild',
             'pytest', 'jest', 'vitest', 'bats', 'golangci-lint', 'gosec',
             'govulncheck', 'pre-commit', 'maestro', 'emulator', 'gradle',
             'gradlew', 'mvn', 'mvnw', 'bazel', 'buck', 'dotnet'}
SHELLS = {'bash', 'sh', 'zsh', 'dash', 'ksh'}
ORDINARY = {'git', 'gh', 'aws', 'gcloud', 'az', 'curl', 'wget', 'ssh', 'scp',
            'rg', 'grep', 'sed', 'awk', 'jq', 'cat', 'head', 'tail', 'sort', 'uniq',
            'ls', 'find', 'fd', 'pwd', 'wc', 'printf', 'echo', 'true', 'false',
            'test', '[', 'stat', 'du', 'df', 'ps', 'pgrep', 'kill', 'date',
            'cp', 'mv', 'rm', 'mkdir', 'touch', 'chmod', 'ln', 'readlink',
            'memcap', 'pdftotext', 'pdfinfo', 'open'}


@dataclass(frozen=True)
class Decision:
    kind: str
    reason: str
    confidence: str
    fingerprint: str
    dependency_count: int

    def as_dict(self):
        return dict(kind=self.kind, reason=self.reason, confidence=self.confidence,
                    fingerprint=self.fingerprint, dependency_count=self.dependency_count,
                    classifier_version=VERSION)


def substitutions(text):
    """Find executable substitutions, excluding single-quoted literal data."""
    i, quote = 0, ''
    while i < len(text):
        c = text[i]
        if c == '\\' and quote != "'":
            i += 2
            continue
        if c in "\"'":
            if not quote:
                quote = c
            elif quote == c:
                quote = ''
        if ((quote != "'" and text.startswith('$(', i) and not text.startswith('$((', i))
                or (not quote and text[i:i+2] in {'<(', '>('})):
            start, j, depth, inner = i + 2, i + 2, 1, ''
            while j < len(text) and depth:
                ch = text[j]
                if ch == '\\' and inner != "'":
                    j += 2
                    continue
                if ch in "\"'":
                    if not inner:
                        inner = ch
                    elif inner == ch:
                        inner = ''
                elif not inner:
                    depth += (ch == '(') - (ch == ')')
                j += 1
            if not depth:
                yield text[start:j-1]
                i = j
                continue
        if c == '`' and quote != "'":
            j = i + 1
            while j < len(text) and text[j] != '`':
                j += 2 if text[j] == '\\' else 1
            if j < len(text):
                yield text[i+1:j]
                i = j + 1
                continue
        i += 1


class Classifier:
    def __init__(self, cwd, environ=None):
        self.cwd = Path(cwd or os.getcwd()).absolute()
        self.environ = os.environ if environ is None else environ
        self.dependencies = {}
        self.uncertain = False
        self.deadline = time.monotonic() + .2
        self.seen = set()
        self.functions = {}

    def text(self, path):
        """Bounded regular-file reads; never block on a FIFO or device."""
        try:
            path = Path(path).resolve()
            if len(self.dependencies) >= 32 or time.monotonic() > self.deadline:
                self.uncertain = True
                return None
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_TEXT:
                self.uncertain = True
                return None
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    return None
                raw = os.read(fd, MAX_TEXT + 1)
            finally:
                os.close(fd)
            if len(raw) > MAX_TEXT:
                self.uncertain = True
                return None
            self.dependencies[str(path)] = hashlib.sha256(raw).hexdigest()
            return raw.decode('utf-8')
        except (OSError, ValueError, UnicodeError):
            self.uncertain = True
            return None

    def git_hooks(self, cwd):
        # Git's config/ref parsers do not run repository hooks. No shell, editor,
        # aliases, remote helpers, signing or worktree mutation is invoked here.
        try:
            left = self.deadline - time.monotonic()
            if left <= 0:
                return None
            result = subprocess.run(['/usr/bin/git', '-C', str(cwd), 'rev-parse', '--path-format=absolute', '--git-path', 'hooks'],
                                    capture_output=True, text=True, timeout=left,
                                    env={**self.environ, 'GIT_OPTIONAL_LOCKS': '0'})
            if result.returncode == 0 and result.stdout.strip():
                return Path(result.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            self.uncertain = True
        return None

    def script(self, path, cwd, depth, interpreter=''):
        path = Path(path)
        if not path.is_absolute():
            path = cwd / path
        text = self.text(path)
        if text is None:
            return None
        identity = (str(path.absolute()), hashlib.sha256(text.encode()).hexdigest())
        if identity in self.seen:
            self.uncertain = True
            return None
        self.seen.add(identity)
        try:
            first = text.splitlines()[0] if text else ''
            if 'python' in interpreter or path.suffix == '.py' or re.search(r'^#!.*\bpython', first):
                return self.python(text, cwd, depth + 1)
            if interpreter in SHELLS or path.suffix in {'.sh', '.bash', '.zsh'} or re.search(r'^#!.*\b(?:ba|z|da|k)?sh\b', first):
                return self.shell(text, cwd, depth + 1)
            if path.suffix in {'.js', '.mjs', '.cjs', '.ts', '.tsx'}:
                if re.search(r'(?:require\s*\(|from\s+|import\s*\()[\s\'\"]*(?:playwright|puppeteer|@playwright/test)\b', text):
                    return 'browser-script'
            self.uncertain = True
            return None
        finally:
            self.seen.remove(identity)

    def python(self, text, cwd, depth):
        if depth > MAX_DEPTH:
            self.uncertain = True
            return None
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            self.uncertain = True
            return None
        aliases, functions, called = {}, {}, set()
        pending = [tree]
        while pending:
            node = pending.pop(0)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[node.name] = node
                pending += node.decorator_list + node.args.defaults + [n for n in node.args.kw_defaults if n]
                continue
            if isinstance(node, ast.Lambda):
                continue
            if isinstance(node, ast.If) and isinstance(node.test, ast.Constant):
                pending += node.body if node.test.value else node.orelse
                continue
            pending += list(ast.iter_child_nodes(node))
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or '']
                if any(m.split('.')[0] in {'playwright', 'selenium', 'torch', 'tensorflow'} for m in modules):
                    return 'memory-workload-import'
                for alias in node.names:
                    aliases[alias.asname or alias.name] = alias.name if isinstance(node, ast.Import) else (node.module or '') + '.' + alias.name
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in functions and node.func.id not in called:
                called.add(node.func.id)
                pending += functions[node.func.id].body
            name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ''
            target = aliases.get(node.func.value.id, '') + '.' + name if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) else aliases.get(name, '')
            if target in {'subprocess.run', 'subprocess.Popen', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output', 'os.system', 'os.execv', 'os.execvp', 'os.execvpe'} and node.args:
                try:
                    value = ast.literal_eval(node.args[0])
                except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                    continue
                if isinstance(value, str):
                    reason = self.shell(value, cwd, depth + 1)
                elif isinstance(value, (list, tuple)) and all(isinstance(x, str) for x in value):
                    reason = self.argv(list(value), cwd, depth + 1)
                else:
                    reason = None
                if reason:
                    return reason
        self.uncertain = True
        return None

    def argv(self, words, cwd, depth=0):
        # The I/O budget bounds dependency inspection, not cheap command evidence.
        # A slow Git config probe must not hide a later explicit build.
        if depth > MAX_DEPTH:
            self.uncertain = True
            return None
        words = list(words)
        while words and (re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', words[0]) or words[0] in {'!', '{', '}', 'then', 'do', 'else', 'if', 'elif', 'while', 'until'}):
            words.pop(0)
        if not words:
            return None
        name, args = Path(words[0]).name, words[1:]
        if words[0] in self.functions:
            return self.shell(self.functions[words[0]], cwd, depth + 1)
        if name in {'env', 'command', 'exec', 'nohup', 'time', 'nice', 'sudo'}:
            if name == 'env' and args[:1] in (['-S'], ['--split-string']) and len(args) > 1:
                try:
                    return self.argv(shlex.split(args[1]) + args[2:], cwd, depth + 1)
                except ValueError:
                    self.uncertain = True
                    return None
            while args and args[0].startswith('-'):
                option = args.pop(0)
                if option in {'-u', '-C', '-n', '-o', '--chdir', '--unset', '--user'} and args:
                    args.pop(0)
            return self.argv(args, cwd, depth + 1)
        if args in (['--help'], ['-h'], ['--version'], ['-V']):
            return None
        if name in SHELLS:
            for i, arg in enumerate(args):
                if arg.startswith('-') and 'c' in arg[1:] and i + 1 < len(args):
                    return self.shell(args[i+1], cwd, depth + 1)
                if not arg.startswith('-'):
                    return self.script(arg, cwd, depth, name)
            self.uncertain = True
            return None
        if name in {'source', '.'} and args:
            return self.script(args[0], cwd, depth, 'sh')
        if name in {'ssh', 'aws', 'gh', 'gcloud', 'az'}:
            return None  # Remote command payloads do not run on this Mac.
        if name == 'memcap' and args[:1] == ['run']:
            if '--shell-command' in args and args.index('--shell-command') + 1 < len(args):
                return self.shell(args[args.index('--shell-command') + 1], cwd, depth + 1)
            if '--' in args:
                return self.argv(args[args.index('--') + 1:], cwd, depth + 1)
        if name == 'memcap' and args[:2] == ['environment', 'run']:
            return 'container-workload'
        if name == 'jq':
            # Explicit materialization is evidence; unfamiliar/streaming filters are not.
            if any(re.search(r'\[\s*range\(\s*[0-9]{8,}\s*\)\s*\]', a) for a in args):
                return 'known-workload'
        if name == 'sed':
            for arg in args:
                for match in re.finditer(r'(?:^|;)\s*(?:[0-9]+)?e\s+([^;]+)', arg):
                    reason = self.shell(match[1], cwd, depth + 1)
                    if reason:
                        return reason
        if name == 'rg':
            for i, arg in enumerate(args):
                helper = arg.split('=', 1)[1] if arg.startswith('--pre=') else args[i+1] if arg == '--pre' and i+1 < len(args) else ''
                if helper:
                    reason = self.argv([helper], cwd, depth + 1)
                    if reason:
                        return reason
        if name in {'awk', 'gawk', 'mawk'}:
            for arg in args:
                for match in re.finditer(r'\bsystem\(\s*"([^"\\]*)"\s*\)', arg):
                    reason = self.shell(match[1], cwd, depth + 1)
                    if reason:
                        return reason
        if name in {'npm', 'pnpm', 'yarn', 'npx', 'bun'}:
            while args and args[0].startswith('-'):
                option = args.pop(0)
                if option in {'--prefix', '--dir', '-C', '--cwd'} and args:
                    cwd = cwd / args.pop(0)
            if not args:
                return 'package-install' if name in {'yarn', 'bun'} else None
            if args[0] in {'install', 'i', 'ci', 'add', 'rebuild'}:
                return 'package-install'
            if args[0] in {'exec', 'dlx', 'x'} or name == 'npx':
                return self.argv(args[1:] if name != 'npx' else args, cwd, depth + 1)
            script = args[1] if args[0] in {'run', 'run-script'} and len(args) > 1 else args[0]
            body = self.text(cwd / 'package.json')
            try:
                scripts = json.loads(body).get('scripts', {}) if body else {}
                if script in scripts:
                    for key in ('pre' + script, script, 'post' + script):
                        if isinstance(scripts.get(key), str):
                            reason = self.shell(scripts[key], cwd, depth + 1)
                            if reason:
                                return reason
                    return None
            except (ValueError, TypeError, AttributeError):
                pass
            return 'package-workload' if script in {'build', 'test', 'lint', 'typecheck', 'dev', 'start', 'bench'} else None
        if name == 'git':
            directory = None
            while args and args[0] in {'-C', '-c'} and len(args) > 1:
                flag, value, *args = args
                if flag == '-C':
                    cwd = cwd / value
                elif value.lower().startswith('core.hookspath='):
                    # This command's override is evidence, never rewritten.
                    directory = Path(value.split('=', 1)[1])
            if args and args[0] in {'commit', 'push'}:
                if directory is not None:
                    directory = directory if directory.is_absolute() else cwd / directory
                return self.hooks(directory if directory is not None else self.git_hooks(cwd), args, cwd, depth)
            if args and args[0] in {'gc', 'repack'}:
                return 'repository-maintenance'
            return None
        if re.fullmatch(r'python(?:[23](?:\.[0-9]+)?)?', name):
            if args[:1] == ['-m'] and len(args) > 1:
                if args[1] in {'pytest', 'unittest', 'pip', 'build', 'compileall'}:
                    return 'python-workload'
                if args[1] == 'django' and args[2:3] == ['test']:
                    return 'python-workload'
                if args[1] == 'http.server':
                    return None
            if '-c' in args and args.index('-c') + 1 < len(args):
                return self.python(args[args.index('-c') + 1], cwd, depth + 1)
            script = next((a for a in args if not a.startswith('-')), None)
            if script:
                if Path(script).name == 'manage.py' and args[args.index(script)+1:args.index(script)+2] == ['test']:
                    return 'python-workload'
                if Path(script).name.startswith('test_'):
                    return 'test-script'
                return self.script(script, cwd, depth, name)
            self.uncertain = True
            return None
        if name == 'node':
            # A heap ceiling does not establish memory demand.
            if any(a in {'-e', '--eval', '-p', '--print'} for a in args):
                self.uncertain = True
                return None
            script = next((a for a in args if not a.startswith('-')), None)
            return self.script(script, cwd, depth, name) if script else None
        if name == 'xcrun' and args:
            return self.argv(args, cwd, depth + 1)
        if name == 'simctl' and args and args[0] in {'boot', 'bootstatus'}:
            return 'simulator-start'
        if name == 'docker' and args[:1] == ['compose']:
            return 'container-workload' if any(a in {'up', 'build', 'run', 'start'} for a in args[1:]) else None
        if name == 'docker' and args[:1] == ['buildx']:
            return 'container-workload' if 'build' in args[1:] or '--bootstrap' in args[1:] else None
        if name == 'go' and args[:1] == ['-C'] and len(args) > 2:
            return self.argv(['go'] + args[2:], cwd / args[1], depth + 1)
        if name in {'uv', 'poetry'} and args[:1] == ['run']:
            return self.argv(args[1:], cwd, depth + 1)
        if name in WORKLOADS or (name in HEAVY and args and args[0] in HEAVY[name]):
            return 'known-workload'
        if name == 'vite' and not args:
            return 'known-workload'
        if name == 'find':
            for flag in ('-exec', '-execdir'):
                if flag in args:
                    reason = self.argv(args[args.index(flag)+1:], cwd, depth + 1)
                    if reason:
                        return reason
        if name == 'xargs':
            while args and args[0].startswith('-'):
                option = args.pop(0)
                if option in {'-I', '-n', '-P', '-L', '-s', '-E'} and args:
                    args.pop(0)
            return self.argv(args, cwd, depth + 1)
        if name not in ORDINARY and name not in {'cd', 'export', 'set', 'read', 'for', 'done', 'fi', 'case', 'esac', 'sleep'}:
            if '/' in words[0] or Path(words[0]).suffix in {'.sh', '.py', '.js'}:
                return self.script(words[0], cwd, depth)
            self.uncertain = True
        return None

    def hooks(self, directory, args, cwd, depth):
        if directory is None or not args:
            return None
        names = {'commit': ('pre-commit', 'prepare-commit-msg', 'commit-msg', 'post-commit'),
                 'push': ('pre-push',)}.get(args[0], ())
        if '--no-verify' in args or (args[0] == 'commit' and '-n' in args):
            names = tuple(n for n in names if n not in {'pre-commit', 'commit-msg', 'pre-push'})
        for name in names:
            path = directory / name
            if path.is_file() and os.access(path, os.X_OK):
                reason = self.script(path, cwd, depth, 'sh')
                if reason:
                    return 'git-hook-workload'
        return None

    def shell(self, text, cwd=None, depth=0):
        cwd = self.cwd if cwd is None else cwd
        if depth > MAX_DEPTH or len(text) > MAX_TEXT:
            self.uncertain = True
            return None
        # Heredoc bodies are data for their consumer, not shell commands. Inspect
        # executable interpreter bodies without treating quoted documentation as work.
        lines, cleaned, i = text.splitlines(keepends=True), [], 0
        while i < len(lines):
            line = lines[i]
            match = re.search(r'<<-?\s*([\'\"]?)([A-Za-z_][A-Za-z0-9_]*)\1\s*$', line.rstrip('\n'))
            if match:
                prefix = line[:match.start()]
                body, i = [], i + 1
                while i < len(lines) and lines[i].strip() != match[2]:
                    body.append(lines[i]); i += 1
                contents = ''.join(body)
                try:
                    # Inspect the final consumer, not an earlier cd/assignment.
                    words = shlex.split(re.split(r'&&|;|\|\|', prefix)[-1])
                except ValueError:
                    words = []
                if words and re.fullmatch(r'python(?:[23](?:\.[0-9]+)?)?', Path(words[0]).name):
                    reason = self.python(contents, cwd, depth + 1)
                    if reason:
                        return reason
                elif words and Path(words[0]).name in SHELLS:
                    reason = self.shell(contents, cwd, depth + 1)
                    if reason:
                        return reason
                if not match[1]:
                    for sub in substitutions(contents):
                        reason = self.shell(sub, cwd, depth + 1)
                        if reason:
                            return reason
                cleaned.append(prefix + '\n')
            else:
                cleaned.append(line)
            i += 1
        text = ''.join(cleaned)
        # Function definitions are inert until called. Retain the original body
        # text, including quoting; do not interpret substitutions in an unused helper.
        from inspection import spans
        parts = list(spans(text))
        replacements, index = [], 0
        while index + 1 < len(parts):
            start, _, token, _ = parts[index]
            match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)\(\)', token)
            if match and parts[index+1][2] == '{':
                level, end = 1, index + 2
                while end < len(parts):
                    level += (parts[end][2] == '{') - (parts[end][2] == '}')
                    if level == 0:
                        break
                    end += 1
                if level == 0:
                    self.functions[match[1]] = text[parts[index+1][1]:parts[end][0]]
                    replacements.append((start, parts[end][1]))
                    index = end
            index += 1
        for start, end in reversed(replacements):
            text = text[:start] + 'true' + text[end:]
        for sub in substitutions(text):
            reason = self.shell(sub, cwd, depth + 1)
            if reason:
                return reason
        from scheduler_policy import normalized_lines
        try:
            lexer = shlex.shlex(normalized_lines(text), posix=True, punctuation_chars=';&|()<>')
            lexer.whitespace_split = True
            lexer.commenters = ''  # normalized_lines already applied shell comment rules.
            tokens = list(lexer)
        except (ValueError, RecursionError):
            self.uncertain = True
            return None
        words, skip = [], False
        for token in tokens + [';']:
            if token and all(c in ';&|()' for c in token):
                if words[:1] == ['cd'] and len(words) == 2:
                    cwd = cwd / words[1]
                reason = self.argv(words, cwd, depth)
                if reason:
                    return reason
                words, skip = [], False
            elif token and all(c in '<>' for c in token):
                if words and words[-1].isdigit():
                    words.pop()
                skip = True
            elif skip:
                skip = False
            else:
                words.append(token)
        return None


def classify(command, cwd=None, environ=None):
    worker = Classifier(cwd, environ)
    reason = worker.shell(command)
    fingerprint = hashlib.sha256(json.dumps(dict(command=command, cwd=str(worker.cwd),
        dependencies=worker.dependencies, version=VERSION,
        workers={k: worker.environ.get(k) for k in ('GOMAXPROCS', 'GOFLAGS', 'CARGO_BUILD_JOBS', 'NODE_OPTIONS', 'OMP_NUM_THREADS')}),
        sort_keys=True).encode()).hexdigest()
    if not reason:
        from native_observer import learned
        if learned(fingerprint):
            reason = 'observed-high-memory'
    return Decision('heavy' if reason else 'light', reason or ('unknown-demand' if worker.uncertain else 'ordinary-command'),
                    'evidence' if reason else 'unknown' if worker.uncertain else 'ordinary', fingerprint, len(worker.dependencies))


def main():
    import argparse
    parser = argparse.ArgumentParser(prog='memcap classify')
    parser.add_argument('--cwd')
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--command')
    choice.add_argument('--replay', help='JSON cases with command and expected light/heavy; no commands executed')
    parser.add_argument('--repetitions', type=int, default=5)
    args = parser.parse_args()
    if args.replay:
        from demand_replay import replay
        try:
            result = replay(args.replay, args.cwd, args.repetitions)
        except (ValueError, OSError) as error:
            parser.error(str(error))
    else:
        result = classify(args.command, args.cwd).as_dict()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
