"""job-feedback pruning: stale orphans go; recent, live-job and held files stay."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from feedback_prune import prune

NOW = 2_000_000_000
OLD = NOW - 8 * 86400
RECENT = NOW - 86400
SESSION = 'a' * 64
SUBAGENT = 'b' * 64
HOLDER = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
fcntl.flock(fd, fcntl.LOCK_EX)
print('held', flush=True)
sys.stdin.read()
"""


class Prune(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, HOME=self.tmp.name, MC_DRY_RUN='1',
                         MEMCAP_STATE_HOME=self.tmp.name + '/state',
                         MEMCAP_CONFIG_HOME=self.tmp.name + '/config')
        env.start()
        self.addCleanup(env.stop)
        self.state = Path(self.tmp.name) / 'state' / 'memcap'
        self.dir = self.state / 'job-feedback'
        self.dir.mkdir(parents=True, mode=0o700)
        (self.state / 'queue').mkdir(mode=0o700)
        self.registry([])

    def registry(self, ids, raw=None):
        path = self.state / 'queue' / 'jobs.json'
        path.write_text(raw if raw is not None else json.dumps(
            {'jobs': [{'id': i, 'status': s} for i, s in ids]}))
        path.chmod(0o600)

    def file(self, name, mtime, body='x'):
        path = self.dir / name
        path.write_text(body)
        path.chmod(0o600)
        os.utime(path, (mtime, mtime))
        return path

    def notice(self, name, mtime, job=None):
        row = {'at': str(int(mtime)), 'pid': name.split('.')[0], 'cwd': '/p', 'message': 'm'}
        if job:
            row.update(kind='boot-timeout', job_id=job)
        return self.file(name, mtime, json.dumps(row))

    def hold(self, lock):
        child = subprocess.Popen([sys.executable, '-c', HOLDER, str(lock)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.addCleanup(lambda: (child.stdin.close(), child.wait()))
        self.assertEqual(child.stdout.readline().strip(), b'held')
        return child

    def test_old_orphans_are_removed(self):
        stale = [
            self.file(f'guidance-{SESSION}.receipt', OLD),
            self.file(f'guidance-{SESSION}.receipt.lock', OLD, ''),
            self.file(f'guidance-{SESSION}-{SUBAGENT}.receipt', OLD),
            self.file(f'guidance-{SESSION}-{SUBAGENT}.receipt.lock', OLD, ''),
            self.file(f'guidance-{SUBAGENT}.receipt.lock', OLD, ''),  # orphan lock
            self.file(f'seen-{SESSION}-123.json.receipt', OLD),
            self.notice('123.json', OLD),
            self.notice('456.json', OLD, job='f' * 32),  # job left the registry
            self.file('.guidance-abc123', NOW - 2 * 86400),
        ]
        self.assertEqual(prune(self.state, now=NOW), len(stale))
        for path in stale:
            self.assertFalse(path.exists(), path.name)

    def test_recent_live_and_foreign_files_are_kept(self):
        kept = [
            self.file(f'guidance-{SESSION}.receipt', RECENT),
            self.file(f'guidance-{SESSION}.receipt.lock', OLD, ''),
            self.file(f'seen-{SESSION}-9.json.receipt', RECENT),
            self.notice('9.json', RECENT),
            self.notice('10.json', OLD, job='e' * 32),
            self.notice('11.json', OLD, job='d' * 32),
            self.file('notes.txt', OLD),
            self.file('.guidance-recent', NOW - 3600),
        ]
        # A job in any registry state keeps its notice, however old.
        self.registry([('e' * 32, 'waiting'), ('d' * 32, 'completed')])
        target = self.file('target', OLD)
        (self.dir / f'seen-{SESSION}-77.json.receipt').symlink_to(target)
        self.assertEqual(prune(self.state, now=NOW), 0)
        for path in kept + [target, self.dir / f'seen-{SESSION}-77.json.receipt']:
            self.assertTrue(os.path.lexists(path), path.name)

    def test_unreadable_registry_keeps_job_notices(self):
        job_notice = self.notice('12.json', OLD, job='c' * 32)
        plain = self.notice('13.json', OLD)
        self.registry([], raw='{not json')
        self.assertEqual(prune(self.state, now=NOW), 1)
        self.assertTrue(job_notice.exists())
        self.assertFalse(plain.exists())

    def test_held_lock_and_its_receipt_are_kept_until_released(self):
        receipt = self.file(f'guidance-{SESSION}.receipt', OLD)
        lock = self.file(f'guidance-{SESSION}.receipt.lock', OLD, '')
        orphan = self.file(f'guidance-{SUBAGENT}.receipt.lock', OLD, '')
        holders = [self.hold(lock), self.hold(orphan)]
        self.assertEqual(prune(self.state, now=NOW), 0)
        self.assertTrue(receipt.exists() and lock.exists() and orphan.exists())
        # Negative control: the same files go once nothing holds the locks.
        for child in holders:
            child.stdin.close()
            child.wait()
        self.assertEqual(prune(self.state, now=NOW), 3)
        self.assertFalse(receipt.exists() or lock.exists() or orphan.exists())

    def test_removals_are_bounded_per_pass(self):
        for n in range(10):
            self.notice(f'{n}.json', OLD)
        self.assertEqual(prune(self.state, now=NOW, limit=4), 4)
        self.assertEqual(len(list(self.dir.glob('*.json'))), 6)

    def test_foreign_owned_or_symlinked_directory_is_untouched(self):
        self.notice('1.json', OLD)
        moved = self.state / 'elsewhere'
        self.dir.rename(moved)
        self.dir.symlink_to(moved)
        self.assertEqual(prune(self.state, now=NOW), 0)
        self.assertTrue((moved / '1.json').exists())


if __name__ == '__main__':
    unittest.main()
