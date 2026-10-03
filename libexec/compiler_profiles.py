"""Bounded compiler predictions across edits, never permission or a hard cap.

Only direct compiler invocations have a reusable local observation scope. Tests,
scripts, wrappers and external build environments retain their ordinary priors.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import time

GIB = 1048576
MAX_AGE = 7 * 86400
SOURCE_SUFFIXES = {'.go', '.ts', '.tsx', '.js', '.jsx', '.c', '.cc', '.cpp', '.h', '.s'}
SKIP = {'.git', 'node_modules', 'vendor', 'target', 'build', '.build', '.venv', 'venv', '__pycache__', '.next'}


def compiler_profile(argv, cwd, workers, env):
    """Return a bounded private context/envelope, or None on uncertainty."""
    if not argv or type(workers) is not int or workers < 1:
        return None
    original = list(argv)
    if len(argv)==3 and argv[0] in {'/bin/bash','/bin/sh'} and argv[1]=='-c':
        if any(env.get(k) for k in ('BASH_ENV','ENV')) or any(k.startswith('BASH_FUNC_') for k in env):
            return None
        from scheduler_policy import simple_words
        argv = simple_words(argv[2])
        if not argv:
            return None
    name = Path(argv[0]).name
    if not (name == 'tsc' or name == 'go' and len(argv) > 1 and argv[1] == 'build'):
        return None
    if any(x.startswith(('-toolexec', '-overlay', '-modfile', '-compiler')) for x in argv):
        return None
    if any('..' in Path(x).parts for x in argv[1:]):
        return None
    if name == 'tsc' and env.get('NODE_OPTIONS'):
        return None
    if any(x in env.get('GOFLAGS', '') for x in ('-toolexec', '-overlay', '-modfile', '-compiler')):
        return None
    cwd = Path(cwd).resolve()
    try:
        # tsc otherwise searches parent directories for its project config.
        if name == 'tsc' and not (cwd/'tsconfig.json').is_file():
            return None
        if name == 'go':
            if env.get('GOWORK') not in (None,'','off','auto'):
                return None
            if env.get('GOWORK')!='off' and any((p/'go.work').exists() for p in (cwd,*cwd.parents)):
                return None
            # Local replacements can change outside this enumerated project.
            mod = cwd/'go.mod'
            # A subdirectory/GOPATH build can import sibling inputs and inherit
            # manifests outside cwd. Do not certify that unenumerated scope.
            if not mod.is_file() or re.search(r'=>\s*(?:\./|\.\./|/)',mod.read_text()):
                return None
        executable = Path(shutil.which(argv[0]) or argv[0]).resolve(strict=True)
        info = executable.stat()
        # Commands may name an output outside cwd, but external compiler inputs
        # and config references have no bounded source envelope here.
        for i, word in enumerate(argv[1:], 1):
            if word.startswith('/') and argv[i-1] not in {'-o', '--outDir', '--outFile'}:
                return None
        deadline = time.monotonic() + .15
        size = source_size = files = scanned = 0
        context_files = {}
        def walk_failed(error):
            raise error
        for directory, dirs, names in os.walk(cwd, followlinks=False, onerror=walk_failed):
            if time.monotonic() > deadline:
                return None
            if any((Path(directory)/d).is_symlink() for d in dirs if d not in SKIP):
                return None
            dirs[:] = sorted(d for d in dirs if d not in SKIP)
            for filename in sorted(names):
                path = Path(directory)/filename
                if path.suffix not in SOURCE_SUFFIXES | {'.json', '.yaml', '.yml', '.mod', '.sum', '.lock'}:
                    continue
                scanned += 1
                if scanned > 4096 or time.monotonic() > deadline or path.is_symlink():
                    return None
                before = path.stat()
                if before.st_size > 8 * 1024 * 1024 - size:
                    return None
                data = path.read_bytes()
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(data) != before.st_size:
                    return None
                size += len(data)
                if size > 8 * 1024 * 1024:
                    return None
                if path.suffix in SOURCE_SUFFIXES:
                    files += 1
                    source_size += len(data)
                else:
                    if filename.startswith('tsconfig') and re.search(rb'"(?:\.\./|/)',data):
                        return None
                    context_files[str(path.relative_to(cwd))] = hashlib.sha256(data).hexdigest()
        if not files:
            return None
        if name == 'go' and env.get('GOENV') != 'off':
            config_path = env.get('GOENV')
            if not config_path and env.get('HOME'):
                config_path = str(Path(env['HOME'])/'Library/Application Support/go/env')
            if config_path:
                goenv = Path(config_path)
                if goenv.exists():
                    if goenv.is_symlink() or goenv.stat().st_size > 65536:
                        return None
                    config = goenv.read_bytes()
                    if any(flag in config for flag in (b'-toolexec', b'-overlay', b'-modfile', b'GOWORK=', b'CC=', b'CXX=')):
                        return None
                    context_files['external-goenv'] = hashlib.sha256(config).hexdigest()
        relevant_env = {k:v for k,v in env.items() if k.startswith(('GO', 'CGO_', 'TS_')) or k in
                        {'CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'LDFLAGS', 'NODE_OPTIONS', 'NODE_ENV'}}
        description = dict(version=2, argv=original, cwd=str(cwd), workers=workers,
                           executable=str(executable), tool=[info.st_size, info.st_mtime_ns],
                           configs=context_files, env=relevant_env)
        key = hashlib.sha256(json.dumps(description, sort_keys=True).encode()).hexdigest()
        digest = lambda value: hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
        return dict(key=key, source_bytes=source_size, source_files=files,
                    family_key=digest([original,str(cwd)]), components={
                        'workers':digest(workers), 'tool':digest([str(executable),info.st_size,info.st_mtime_ns]),
                        'config':digest(context_files), 'env':digest(relevant_env)})
    except (OSError, ValueError, UnicodeError):
        return None


def predict(history, profile, prior, now):
    """Reason: 0 unsupported, 1 reused, 2 cold, 3 insufficient, 4 stale, 5 growth.

    Never shrink from partial evidence. Partial growth stays a floor until three
    subsequent complete runs; the complete-run maximum has a 50% prediction margin.
    """
    if not profile:
        return prior, 0
    row = history.get(profile['key'], {})
    if not row:
        previous = next((r for r in reversed(list(history.values()))
                         if r.get('family_key') == profile.get('family_key') and r.get('components')),None)
        if previous:
            for field,code in [('workers',6),('tool',7),('config',8),('env',9)]:
                if previous['components'].get(field) != profile.get('components',{}).get(field):
                    return prior,code
        return prior, 2
    samples = [s for s in row.get('samples', []) if 0 <= now - s['at'] <= MAX_AGE]
    floor = row.get('partial_floor_kb', 0)
    if sum(s['at'] > row.get('partial_at', math.inf) for s in samples) >= 3:
        floor = 0
    raised = max(prior, floor)
    if len(samples) < 3:
        return raised, 4 if len(row.get('samples', [])) >= 3 else 3
    peak = max(s['peak_kb'] for s in samples)
    target = max(GIB//2, math.ceil(peak*1.5), floor)
    # An upward prediction does not need a stable source envelope.
    if target >= prior:
        return target, 1
    minimum_bytes = min(s['source_bytes'] for s in samples)
    minimum_files = min(s['source_files'] for s in samples)
    if profile['source_bytes'] > minimum_bytes*1.2 or profile['source_files'] > minimum_files*1.2:
        return raised, 5
    growth = max(1, profile['source_bytes']/max(1, minimum_bytes), profile['source_files']/max(1, minimum_files))
    return max(GIB//2, math.ceil(peak*1.5*growth), floor), 1


def record_profile(history, profile, peak, complete, now):
    if not profile or type(peak) is not int or peak < 0:
        return
    key = profile['key']
    row = dict(history.pop(key, {}))
    row.update(family_key=profile.get('family_key'),components=profile.get('components',{}))
    if complete:
        samples = [s for s in row.get('samples', []) if 0 <= now-s['at'] <= MAX_AGE]
        row['samples'] = (samples + [dict(peak_kb=peak, at=now,
                                        source_bytes=profile['source_bytes'], source_files=profile['source_files'])])[-20:]
    elif math.ceil(peak*1.5) > row.get('partial_floor_kb', 0):
        row.update(partial_floor_kb=math.ceil(peak*1.5), partial_at=now)
    history[key] = row
    while len(history) > 256:
        del history[next(iter(history))]
