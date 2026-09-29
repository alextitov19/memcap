"""Read Docker's ceiling without hanging diagnostics on macOS file consent.

The reader is a daemon thread in this short-lived helper. A deadline exits this
helper normally, without signalling any process or changing Docker's settings.
"""

import json
import sys
import threading
from pathlib import Path


def read_mib(path, timeout=1.0, reader=None):
    result, ready = [], threading.Event()

    def read():
        try:
            if reader is not None:
                text = reader()
            else:
                with Path(path).open() as source:
                    text = source.read(1048577)
            if len(text) > 1048576:
                result.append((1, ""))
                return
            value = json.loads(text).get("MemoryMiB")
            if type(value) is int and 0 < value < 10**15:
                result.append((0, str(value)))
            else:
                result.append((1, ""))
        except (OSError, UnicodeError):
            result.append((2, ""))
        except (ValueError, AttributeError):
            result.append((1, ""))
        finally:
            ready.set()

    threading.Thread(target=read, daemon=True).start()
    if not ready.wait(timeout):
        return 2, ""
    return result[0]


if __name__ == "__main__":
    status, value = read_mib(sys.argv[1])
    if value:
        print(value)
    sys.exit(status)
