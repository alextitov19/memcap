"""Bounded pruning of stale job-feedback notices and receipts.

Never signals, never takes the queue registry lock and never removes a receipt
whose lock another hook currently holds. Only memcap-created basenames match.
"""
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

MAX_AGE = 7 * 86400
TEMP_AGE = 86400
MAX_REMOVALS = 2000
NOTICE = re.compile(r'[0-9]+\.json')
SEEN = re.compile(r'seen-[0-9a-f]{64}-[0-9]+\.json\.receipt')
GUIDANCE = re.compile(r'guidance-[0-9a-f]{16,64}(?:-[0-9a-f]{16,64})*\.receipt')
TEMPORARY = re.compile(r'\.guidance-[A-Za-z0-9_]+')


def registry_jobs(state):
    """Every job id still in the registry, or None when that cannot be read."""
    path = Path(state) / 'queue' / 'jobs.json'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return set()
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return None
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            payload = stream.read(4 * 1024 * 1024 + 1)
        if len(payload) > 4 * 1024 * 1024:
            return None
        jobs = json.loads(payload)['jobs']
        if not isinstance(jobs, list):
            return None
        # A job present in any state keeps its notice; absence is the only signal.
        return {job['id'] for job in jobs if isinstance(job, dict) and isinstance(job.get('id'), str)}
    except (OSError, ValueError, TypeError, KeyError):
        return None
    finally:
        if fd is not None:
            os.close(fd)


def regular(path):
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        return None
    return info


def notice_job(path):
    try:
        with open(path, 'rb') as stream:
            row = json.loads(stream.read(64 * 1024))
        job = row.get('job_id') if isinstance(row, dict) else None
        return job if isinstance(job, str) else ''
    except (OSError, ValueError):
        # Unreadable content cannot prove the job is gone.
        return None


def unlink_unheld(receipt, lock, now):
    """Remove a receipt and its lock only while holding that lock ourselves."""
    try:
        fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        fd = None
    except OSError:
        return 0
    removed = 0
    try:
        if fd is not None:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                return 0
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    return 0
                raise
            current = regular(lock)
            if current is None or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                return 0
        if receipt is not None:
            # Re-check age under the lock: a writer may have just refreshed it.
            info = regular(receipt)
            if info is not None and now - info.st_mtime > MAX_AGE:
                os.unlink(receipt)
                removed += 1
            elif info is not None:
                return removed
        if fd is not None:
            os.unlink(lock)
            removed += 1
        return removed
    except OSError:
        return removed
    finally:
        if fd is not None:
            os.close(fd)


def prune(state, now=None, limit=MAX_REMOVALS):
    now = time.time() if now is None else now
    directory = Path(state) / 'job-feedback'
    try:
        info = os.lstat(directory)
    except OSError:
        return 0
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        return 0
    live = registry_jobs(state)
    removed = 0
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return 0
    present = set(names)
    for name in names:
        if removed >= limit:
            break
        path = directory / name
        if GUIDANCE.fullmatch(name):
            info = regular(path)
            if info is not None and now - info.st_mtime > MAX_AGE:
                removed += unlink_unheld(path, directory / (name + '.lock'), now)
        elif name.endswith('.receipt.lock') and GUIDANCE.fullmatch(name[:-5]):
            # A lock whose receipt is gone (never written, or removed above).
            info = regular(path)
            if name[:-5] not in present and info is not None and now - info.st_mtime > MAX_AGE:
                removed += unlink_unheld(None, path, now)
        elif SEEN.fullmatch(name) or NOTICE.fullmatch(name) or TEMPORARY.fullmatch(name):
            info = regular(path)
            age = MAX_AGE if not TEMPORARY.fullmatch(name) else TEMP_AGE
            if info is None or now - info.st_mtime <= age:
                continue
            if NOTICE.fullmatch(name):
                job = notice_job(path)
                # A notice tied to a job is kept while the registry still lists
                # it, and whenever the registry or the notice cannot be read.
                if job is None or (job and (live is None or job in live)):
                    continue
            try:
                os.unlink(path)
                removed += 1
            except OSError:
                pass
    return removed


if __name__ == '__main__':
    try:
        print(prune(Path(sys.argv[1])) if len(sys.argv) == 2 else 0)
    except Exception:  # Housekeeping must never fail a watchdog pass.
        print(0)
