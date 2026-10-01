"""Priority only. Both lanes retain ordinary admission, reservations and ownership."""
from pathlib import Path
import re

GIB = 1048576
# Import compatibility during an in-place maintenance update. New admission
# uses only the global slot limit; older loaded selectors may still import this.
SMALL_SLOTS = 2
SMALL_BURST = 3


def candidate(words):
    # Unparsed shells and known heavyweight families never earn small priority.
    # Other commands still need complete exact-profile evidence before admission.
    name = Path(words[0]).name
    if name in {'bash', 'sh', 'zsh'}:
        return len(words) > 1 and not words[1].startswith('-') and Path(words[1]).suffix == '.sh'
    return name not in {
        'bash', 'sh', 'zsh', 'go', 'cargo', 'rustc', 'gcc', 'g++', 'clang',
        'clang++', 'cc', 'c++', 'make', 'cmake', 'ninja', 'xcodebuild', 'swift',
        'xcrun', 'simctl', 'emulator', 'adb', 'docker', 'podman', 'colima',
        'npm', 'pnpm', 'yarn', 'bun', 'npx', 'tsc', 'jest', 'vitest', 'pytest',
        'playwright', 'chromium', 'chrome', 'vite', 'webpack', 'gradle', 'mvn',
        # These runtimes/wrappers accept external code that demand() does not
        # fingerprint. A previously small script is not evidence after edits.
        'node', 'nodejs', 'ruby', 'perl', 'php', 'lua', 'luajit', 'deno',
        'env', 'uv', 'poetry', 'java', 'dotnet', 'pwsh', 'powershell',
        'R', 'Rscript', 'julia', 'groovy', 'python2', 'python2.7',
        'dash', 'fish', 'ksh', 'csh', 'tcsh',
    }


def script_index(words):
    name = Path(words[0]).name
    return 1 if name in {'bash', 'sh', 'zsh'} or re.fullmatch(r'python(?:3(?:\.\d+)?)?', name) else 0


def lane(job):
    if (job.get('demand_version') == 1 or job.get('lane_version') != 1 or job.get('resource')
            or job.get('small_candidate') is not True or job.get('elastic') is not True
            or job.get('estimate_source') != 3):
        return 'heavy'
    count = job.get('estimate_complete_runs', 0)
    if type(count) is not int or count < 3:
        return 'heavy'
    for key in ('memory_kb', 'reservation_kb', 'observed_peak_kb'):
        value = job.get(key, job.get('memory_kb', 0) if key == 'reservation_kb' else 0)
        if type(value) is not int or value < 0 or (key == 'memory_kb' and value == 0):
            return 'heavy'
        if value * (1.25 if key == 'observed_peak_kb' else 1) > GIB:
            return 'heavy'
    return 'small'


def lane_code(job):
    return 1 if lane(job) == 'small' else 2
