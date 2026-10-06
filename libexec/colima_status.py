"""Read-only status label; never feeds admission, cleanup or engine configuration."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from docker_runtime import selected_endpoint


def ceiling_label():
    host = selected_endpoint()
    if not isinstance(host, str) or not host.startswith('unix:///'):
        return ''
    try:
        socket = Path(host[7:]).resolve()
        root = Path(os.environ.get('COLIMA_HOME') or Path.home() / '.colima').resolve()
        relative = socket.relative_to(root)
        if len(relative.parts) != 2 or relative.name != 'docker.sock':
            return ''
        profile = relative.parts[0]
        result = subprocess.run(['colima', 'list', '--json'], capture_output=True,
                                text=True, timeout=2, check=True)
        if len(result.stdout) > 1024 * 1024:
            return ''
        rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        matches = [row for row in rows if isinstance(row, dict) and row.get('name') == profile]
        if len(matches) != 1:
            return ''
        row = matches[0]
        memory = row.get('memory')
        if (row.get('status') != 'Running' or row.get('runtime') != 'docker'
                or type(memory) is not int or not 0 < memory <= 2**50):
            return ''
        return f'{memory / 1024**3:g} GiB VM ceiling (reported by running Colima profile)'
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return ''


if __name__ == '__main__':
    print(ceiling_label())
