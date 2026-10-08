"""Bounded private incident snapshots. Never publish, lock the queue, or signal."""
import gzip
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time


def maintain(state, now=None):
    """Age out private bundles even when no subsequent report is captured."""
    try:
        from command_trace import private_directory
        directory = Path(state)/'incidents'
        if not directory.exists():
            return
        private_directory(directory)
        now = time.time() if now is None else now
        for path in directory.glob('incident-*.json.gz'):
            if not path.is_symlink() and path.is_file() and now-path.stat().st_mtime>7*86400:
                path.unlink()
    except (OSError, ValueError):
        pass


def capture(state, now=None):
    now = time.time() if now is None else now
    try:
        from analytics_store import read_rows, EVIDENCE_EVENTS
        from analytics_events import sanitize
        from command_trace import private_directory, status
        state = Path(state)
        if not (state/'analytics/history.sqlite').is_file():
            return None
        rows, health = read_rows(state/'analytics', now-1800, limit=5000)
        rows = [clean for row in rows if row.get('event') in EVIDENCE_EVENTS and row.get('wall', 0)>=now-1800
                and (clean := sanitize(row))]
        truncated = len(rows)>2000 or health.get('possibly_truncated',False)
        rows = rows[-2000:]
        directory = state/'incidents'
        private_directory(directory, create=True)
        # At most twenty 1MiB compressed bundles, each covering a bounded slice.
        # Unknown gaps remain in health; this never promises complete evidence.
        bundle = dict(schema=1, at=now, since=now-1800, coverage=health, truncated=truncated,
                      command_trace=status(state/'command-trace', now=now), events=rows)
        payload = gzip.compress(json.dumps(bundle, separators=(',', ':')).encode())
        while len(payload)>1024*1024 and bundle['events']:
            bundle['events'] = bundle['events'][len(bundle['events'])//2+1:]
            bundle['truncated'] = True
            payload = gzip.compress(json.dumps(bundle, separators=(',', ':')).encode())
        files = sorted((p for p in directory.glob('incident-*.json.gz')
                        if not p.is_symlink() and p.is_file()), key=lambda p:p.stat().st_mtime)
        for index, path in enumerate(files):
            if index<len(files)-19 or now-path.stat().st_mtime>7*86400:
                path.unlink()
        fd, path = tempfile.mkstemp(prefix='incident-', suffix='.json.gz', dir=directory)
        with os.fdopen(fd,'wb') as stream:
            stream.write(payload)
        return path
    except (OSError, ValueError, TypeError, sqlite3.Error):
        # Evidence failure cannot break reporting, admission or workload results.
        return None
