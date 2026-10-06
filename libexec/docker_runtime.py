"""Read Docker's selected endpoint without starting/contacting any container engine."""
import hashlib
import json
import os
from pathlib import Path
import stat


def read_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            raise ValueError('not bounded metadata')
        value = json.loads(os.read(fd, 1024 * 1024 + 1))
        if not isinstance(value, dict):
            raise ValueError('not metadata')
        return value
    finally:
        os.close(fd)


def endpoint_runtime(host, home):
    if not isinstance(host, str):
        return 'unknown'
    if host.startswith(('ssh://', 'tcp://', 'http://', 'https://')):
        return 'remote'
    if not host.startswith('unix:///'):
        return 'unknown'
    try:
        path = Path(host[7:]).resolve()
    except (OSError, RuntimeError):
        return 'unknown'
    for relative, runtime in (('.orbstack/run/docker.sock', 'orbstack'),
                              ('.docker/run/docker.sock', 'desktop')):
        if path == (home / relative).resolve():
            return runtime
    if str(path).startswith(str((home / '.colima').resolve()) + '/') and path.name == 'docker.sock':
        return 'colima'
    return 'unknown'


def selected_runtime(environ=None):
    env = os.environ if environ is None else environ
    home = Path(env.get('HOME', str(Path.home())))
    directory = Path(env.get('DOCKER_CONFIG') or home / '.docker')
    context = env.get('DOCKER_CONTEXT')
    # Docker's explicit context overrides DOCKER_HOST, which overrides saved context.
    if not context and env.get('DOCKER_HOST'):
        return endpoint_runtime(env['DOCKER_HOST'], home)
    if not context:
        try:
            context = read_json(directory / 'config.json').get('currentContext', '')
        except FileNotFoundError:
            return ''  # No selected context; caller may report installed runtime.
        except (OSError, ValueError):
            return 'unknown'
    if not isinstance(context, str) or len(context) > 1024:
        return 'unknown'
    if not context or context == 'default':
        # Existing configuration with a default selection follows the conventional
        # socket, including OrbStack's symlink. No file retains install detection.
        return endpoint_runtime('unix:///var/run/docker.sock', home)
    try:
        key = hashlib.sha256(context.encode()).hexdigest()
        metadata = read_json(directory / 'contexts' / 'meta' / key / 'meta.json')
        if metadata.get('Name') != context:
            return 'unknown'
        return endpoint_runtime(metadata['Endpoints']['docker']['Host'], home)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return 'unknown'


if __name__ == '__main__':
    print(selected_runtime())
