"""Resolve a bounded compiler launch for prediction only; never execute/rewrite it."""
import hashlib
import json
from pathlib import Path
import re
import shlex

ASSIGNMENTS = {'GOMAXPROCS', 'GOOS', 'GOARCH', 'CGO_ENABLED', 'GOFLAGS',
               'LANG', 'LC_ALL', 'CI', 'NODE_ENV'}


def resolve(argv, cwd, env, diagnostics, lookup):
    """Return (compiler argv, cwd, effective env, wrapper identities), or None.

    Scope reasons: 0 supported, 1 command, 2 shell, 3 environment,
    4 package/config, 5 inspection budget, 6 compiler inputs.
    """
    argv, cwd, env = list(argv), Path(cwd).resolve(), dict(env)
    wrappers = {}

    def reject(reason):
        diagnostics['reason'] = reason
        return None

    def shell(text):
        if len(text) > 65536 or any(c in text for c in '$`\n\r;|<>(){}*?~'):
            return None
        lexer = shlex.shlex(text, posix=True, punctuation_chars='&')
        lexer.whitespace_split = True
        lexer.commenters = ''
        words = list(lexer)
        return words

    try:
        if argv and Path(argv[0]).name in {'bash','sh','zsh','dash'}:
            if (len(argv) != 3 or argv[0] not in {'/bin/bash','/bin/sh'} or argv[1] != '-c'):
                return reject(2)
            if any(env.get(k) for k in ('BASH_ENV','ENV')) or any(k.startswith('BASH_FUNC_') for k in env):
                return reject(3)
            argv = shell(argv[2])
            if not argv:
                return reject(2)
            if argv[:1] == ['cd']:
                if len(argv) < 4 or argv[2] != '&&' or argv[1].startswith('-'):
                    return reject(2)
                if env.get('CDPATH') and not Path(argv[1]).is_absolute():
                    return reject(3)
                cwd = (cwd/argv[1]).resolve(strict=True)
                argv = argv[3:]
            if any('&' in word for word in argv):
                return reject(2)
        if argv and argv[0] in {'env','/usr/bin/env'}:
            argv = argv[1:]
        while argv and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=',argv[0]):
            name,value = argv.pop(0).split('=',1)
            if name not in ASSIGNMENTS:
                return reject(3)
            env[name] = value
        if not argv:
            return reject(1)
        name = Path(argv[0]).name
        if name == 'go' and len(argv)>3 and argv[1]=='-C':
            cwd = (cwd/argv[2]).resolve(strict=True)
            argv = [argv[0]]+argv[3:]
        if name in {'npm','pnpm','yarn'}:
            package_executable = argv[0]
            if (any(env.get(k) for k in ('NODE_OPTIONS','BASH_ENV','ENV','PREFIX'))
                    or any(k.lower().startswith(('npm_config_','yarn_','pnpm_','bash_func_')) for k in env)):
                return reject(3)
            if len(argv)>3 and name=='pnpm' and argv[1]=='--dir':
                cwd = (cwd/argv[2]).resolve(strict=True)
                argv = [argv[0]]+argv[3:]
            if len(argv)<3 or argv[1]!='run':
                return reject(4)
            task = argv[2]
            extra = argv[3:]
            if extra and extra[0] == '--':
                extra = extra[1:]
            elif extra:
                return reject(4)
            # A custom script shell/preload/config could execute more than the
            # literal package script. Unknown configuration retains the prior.
            homes = [cwd,*cwd.parents]
            if env.get('HOME'):
                homes.append(Path(env['HOME']))
                homes.append(Path(env['HOME'])/'.config'/'pnpm')
                homes.append(Path(env['HOME'])/'Library'/'Preferences'/'pnpm')
            if env.get('XDG_CONFIG_HOME'):
                homes.append(Path(env['XDG_CONFIG_HOME'])/'pnpm')
            for directory in homes:
                for config in ('.npmrc','.pnpmfile.cjs','.yarnrc','.yarnrc.yml','rc'):
                    path = directory/config
                    if path.exists() and (path.is_symlink() or path.stat().st_size):
                        return reject(4)
            manifest = cwd/'package.json'
            if manifest.is_symlink() or manifest.stat().st_size > 65536:
                return reject(5)
            raw = manifest.read_bytes()
            scripts = json.loads(raw).get('scripts',{})
            if (not isinstance(scripts,dict) or any(scripts.get(k) for k in ('pre'+task,'post'+task))
                    or not isinstance(scripts.get(task),str)):
                return reject(4)
            words = shell(scripts[task])
            if not words or words[0] != 'tsc' or any('&' in word for word in words):
                return reject(4)
            local = cwd/'node_modules'/'.bin'/'tsc'
            if not local.is_file():
                return reject(4)
            # Include the actual wrapper and Node runtime as well as the compiler.
            for tool in (package_executable,'node'):
                executable = Path(lookup(tool, path=env.get('PATH')) or tool).resolve(strict=True)
                stat = executable.stat()
                wrappers[tool] = [str(executable),stat.st_size,stat.st_mtime_ns]
                candidates = {Path('/etc/npmrc')}
                candidates.update(parent.parent/'etc'/'npmrc' for parent in executable.parents
                                  if parent.name == 'node_modules')
                for config in candidates:
                    if config.exists() and (config.is_symlink() or config.stat().st_size):
                        return reject(4)
            wrappers['package_script'] = hashlib.sha256(raw).hexdigest()
            argv = [str(local)]+words[1:]+extra
            name = 'tsc'
        if not (name == 'tsc' or name == 'go' and len(argv)>1 and argv[1]=='build'):
            return reject(1)
        diagnostics['reason'] = 0
        return argv,cwd,env,wrappers
    except (OSError,ValueError,TypeError,AttributeError):
        return reject(4)
