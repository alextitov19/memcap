"""Bounded private incident snapshots. Never publish, lock the queue, or signal."""
import gzip
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import re


def queue_evidence(state):
    """Bounded registry observation, never proof of liveness or authority."""
    from analytics_events import opaque
    from command_trace import open_private
    from scheduler_metrics import BLOCKERS, number
    try:
        with os.fdopen(open_private(state/'analytics/key',os.O_RDONLY),'rb') as stream:
            key=stream.read(33)
        if len(key)!=32:
            raise ValueError('key unavailable')
        with os.fdopen(open_private(state/'queue/jobs.json',os.O_RDONLY),'rb') as stream:
            payload=stream.read(4*1024*1024+1)
        if len(payload)>4*1024*1024:
            raise ValueError('registry too large')
        jobs=json.loads(payload)['jobs']
        if not isinstance(jobs,list) or len(jobs)>4096:
            raise ValueError('registry unavailable')
        result=[]
        for job in jobs:
            if (not isinstance(job,dict) or not isinstance(job.get('id'),str)
                    or not re.fullmatch('[a-f0-9]{32}',job['id'])
                    or job.get('status') not in {'waiting','running'}):
                continue
            row=dict(job=opaque(key,str(int(job['id'][:13],16))),status=job['status'],
                     persistent=bool(job.get('resource')))
            decision=job.get('admission',{})
            if isinstance(decision,dict) and decision.get('reason') in BLOCKERS:
                row['blocker']=decision['reason']
            for field in ('enqueued','started','memory_kb','reservation_kb'):
                if number(job.get(field)):
                    row[field]=job[field]
            result.append(row)
        all_occupants=[r['job'] for r in result if r['status']=='running' and not r['persistent']]
        occupants=all_occupants[:32]
        truncated=len(result)>128 or len(all_occupants)>32
        result=result[-128:]
        for row in result:
            if row.get('blocker')=='slots' and row['status']=='waiting':
                row['finite_slot_occupants']=occupants
        return dict(status='observed',jobs=result,truncated=truncated,
                    interpretation='Current stored registry entries, not proven liveness or historical causality. Finite slot occupants exclude persistent resources.')
    except (OSError,ValueError,TypeError,KeyError):
        return dict(status='unavailable')


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


def capture(state, now=None, operation=None):
    now = time.time() if now is None else now
    try:
        from analytics_store import read_rows, EVIDENCE_EVENTS
        from analytics_events import sanitize
        from command_trace import private_directory, status, open_private
        state = Path(state)
        if not (state/'analytics/history.sqlite').is_file():
            return None
        rows, health = read_rows(state/'analytics', now-1800, limit=5000)
        rows = [clean for row in rows if row.get('event') in EVIDENCE_EVENTS and row.get('wall', 0)>=now-1800
                and (clean := sanitize(row))]
        truncated = len(rows)>2000 or health.get('possibly_truncated',False)
        selection = dict(requested=operation is not None, matched_events=0)
        operation_key = None
        if operation is not None:
            try:
                from analytics_events import opaque
                with os.fdopen(open_private(state/'analytics/key',os.O_RDONLY),'rb') as stream:
                    key = stream.read(33)
                if len(key)==32:
                    operation_key = opaque(key,operation)
                    selection['matched_events'] = sum(r.get('operation')==operation_key for r in rows)
            except (OSError,ValueError):
                pass
        # Preserve rare decisions before high-rate observations. Sort the chosen
        # evidence back into time order; truncation remains explicit.
        critical = {'action', 'cancellation', 'queued', 'admitted', 'completed', 'cancelled', 'stalled'}
        def priority(row):
            return (bool(operation_key and row.get('operation')==operation_key), row['event'] in critical, row['wall'])
        rows = sorted(rows, key=priority)[-2000:]
        rows.sort(key=lambda r:(r['wall'],r['seq']))
        directory = state/'incidents'
        private_directory(directory, create=True)
        # At most twenty 1MiB compressed bundles, each covering a bounded slice.
        # Unknown gaps remain in health; this never promises complete evidence.
        bundle = dict(schema=1, at=now, since=now-1800, coverage=health, truncated=truncated, selection=selection,
                      queue=queue_evidence(state),
                      command_trace=status(state/'command-trace', now=now), events=rows)
        payload = gzip.compress(json.dumps(bundle, separators=(',', ':')).encode())
        while len(payload)>1024*1024 and bundle['events']:
            bundle['events'] = sorted(sorted(bundle['events'],key=priority)[len(bundle['events'])//2+1:],
                                      key=lambda r:(r['wall'],r['seq']))
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
