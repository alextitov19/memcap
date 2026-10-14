"""Analytics retention under the page cap: dense layout, reclaim and eviction tiers."""
import gzip
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from analytics_events import make_event
from analytics_store import Store, read_rows

LIMIT = 2 * 1024 * 1024
CAP = int(LIMIT * .65) // 4096
# The pre-fix layout, copied verbatim, so the regression starts from a real
# legacy file rather than from whatever the current schema happens to be.
LEGACY_SCHEMA = """
CREATE TABLE events (
  producer TEXT NOT NULL, seq INTEGER NOT NULL, wall REAL NOT NULL,
  mono REAL NOT NULL, boot TEXT NOT NULL, event TEXT NOT NULL,
  session TEXT, job TEXT, operation TEXT, build TEXT NOT NULL, policy TEXT NOT NULL,
  data TEXT NOT NULL, delivery TEXT, PRIMARY KEY(producer, seq)
) WITHOUT ROWID;
CREATE INDEX events_time ON events(wall);
CREATE INDEX events_session ON events(session, wall);
CREATE INDEX events_job ON events(job, wall);
CREATE UNIQUE INDEX events_delivery ON events(delivery) WHERE delivery IS NOT NULL;
CREATE TABLE counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE hourly (
  hour INTEGER NOT NULL, build TEXT NOT NULL, policy TEXT NOT NULL,
  family TEXT NOT NULL, metric TEXT NOT NULL, bucket INTEGER NOT NULL,
  count INTEGER NOT NULL, total REAL NOT NULL, minimum REAL NOT NULL, maximum REAL NOT NULL,
  PRIMARY KEY(hour, build, policy, family, metric, bucket)
) WITHOUT ROWID;
CREATE TABLE job_events (
  job TEXT NOT NULL, event TEXT NOT NULL, wall REAL NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY(job,event)
) WITHOUT ROWID;
CREATE TABLE diagnostics (
  producer TEXT NOT NULL, seq INTEGER NOT NULL, wall REAL NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY(producer,seq)
) WITHOUT ROWID;
PRAGMA user_version=1;
"""


def raw(seq, event='route', size=600):
    """An event row sized like the live median (~0.9 KB of JSON), below the Store API."""
    data = dict(schema=1, event=event, producer='a' * 32, seq=seq, wall=1000.0 + seq,
                mono=float(seq), boot='d' * 32, build='b' * 64, policy='c' * 64, pad='x' * size)
    return ('a' * 32, seq, 1000.0 + seq, float(seq), 'd' * 32, event, 's' * 64, 'j' * 64,
            'o' * 64, 'b' * 64, 'c' * 64, json.dumps(data, separators=(',', ':')), None)


def put(db, seq, event='route'):
    db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', raw(seq, event))


class Retention(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, HOME=self.tmp.name, MC_DRY_RUN='1',
                         MEMCAP_STATE_HOME=str(self.root / 'state'),
                         MEMCAP_CONFIG_HOME=str(self.root / 'config'))
        env.start()
        self.addCleanup(env.stop)
        self.analytics = self.root / 'state' / 'memcap' / 'analytics'
        self.analytics.mkdir(parents=True, mode=0o700)
        self.analytics.chmod(0o700)
        self.analytics.parent.chmod(0o700)

    def legacy_fill(self):
        db = sqlite3.connect(self.analytics / 'history.sqlite')
        os.chmod(self.analytics / 'history.sqlite', 0o600)
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(LEGACY_SCHEMA)
        db.execute(f'PRAGMA max_page_count={CAP}')
        seq = 0
        try:
            while True:
                seq += 1
                put(db, seq)
                db.commit()
        except sqlite3.Error as exc:
            db.rollback()
            self.assertEqual(exc.sqlite_errorcode, sqlite3.SQLITE_FULL)
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        retained = db.execute('SELECT count(*) FROM events').fetchone()[0]
        pages = db.execute('PRAGMA page_count').fetchone()[0]
        db.close()
        return retained, pages, seq

    def test_legacy_file_compacts_and_retains_a_longer_span_under_the_same_cap(self):
        legacy_retained, legacy_pages, seq = self.legacy_fill()
        self.assertEqual(legacy_pages, CAP)
        legacy_bytes = (self.analytics / 'history.sqlite').stat().st_size
        with Store(self.analytics, limit=LIMIT) as store:
            db = store.db
            layouts = [r[0] for r in db.execute(
                "SELECT sql FROM sqlite_master WHERE name IN ('events','job_events','diagnostics')")]
            self.assertEqual(len(layouts), 3)
            for sql in layouts:
                self.assertNotIn('WITHOUT ROWID', sql.upper())
            self.assertEqual(db.execute('PRAGMA auto_vacuum').fetchone()[0], 2)
            # Every legacy row survives the conversion.
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], legacy_retained)
            compacted = db.execute('PRAGMA page_count').fetchone()[0]
            self.assertLess(compacted, CAP * .6, 'converted file still pinned near its cap')
            self.assertLess((self.analytics / 'history.sqlite').stat().st_size, legacy_bytes * .6)
            self.assertEqual(db.execute('PRAGMA max_page_count').fetchone()[0], CAP)
            # Keep streaming the same rows; maintenance runs as the collector does.
            target = seq + 6 * legacy_retained
            while seq < target:
                seq += 1
                try:
                    put(db, seq)
                    db.commit()
                except sqlite3.Error as exc:
                    db.rollback()
                    self.assertEqual(exc.sqlite_errorcode, sqlite3.SQLITE_FULL)
                    store.maintain(now=1000.0 + seq)
                    put(db, seq)
                    db.commit()
                if seq % 25 == 0:
                    store.maintain(now=1000.0 + seq)
            retained = db.execute('SELECT count(*) FROM events').fetchone()[0]
            oldest, newest = db.execute('SELECT min(wall),max(wall) FROM events').fetchone()
            # Eviction keeps the store between ~70% and 90% of its cap, so even
            # the trough of the new layout must beat a completely full legacy file.
            self.assertGreater(retained, 1.5 * legacy_retained)
            self.assertGreater(newest - oldest, 1.5 * (legacy_retained - 1))
            self.assertGreater(store.health()['evicted_events'], 0)

    def test_eviction_returns_free_pages_instead_of_pinning_the_file(self):
        with Store(self.analytics, limit=LIMIT) as store:
            db = store.db
            seq = 0
            try:
                while True:
                    seq += 1
                    put(db, seq)
                    db.commit()
            except sqlite3.Error:
                db.rollback()
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            before = db.execute('PRAGMA page_count').fetchone()[0]
            self.assertGreaterEqual(before, CAP * .9)
            store.maintain(now=1000.0 + seq)
            after = db.execute('PRAGMA page_count').fetchone()[0]
            self.assertEqual(db.execute('PRAGMA freelist_count').fetchone()[0], 0)
            self.assertLess(after, before * .9)
            self.assertLess((self.analytics / 'history.sqlite').stat().st_size, before * 4096 * .9)
            # The cap itself is never raised to make room.
            self.assertEqual(db.execute('PRAGMA max_page_count').fetchone()[0], CAP)

    def row(self, event, seq, **fields):
        return make_event(event, fields, b'x' * 32, 'a' * 32, seq, build='b' * 64,
                          policy='c' * 64, boot='d' * 32, wall=1000 + seq, mono=seq)

    def test_routine_sampling_is_evicted_before_incident_evidence(self):
        protected = [('action', dict(action='signal', scope='oversized', signal=15)),
                     ('cancellation', dict(job='j', cancellation_stage=1, cancellation_signal=15)),
                     ('stalled', dict(job='j')), ('queued', dict(job='j')),
                     ('admitted', dict(job='j')), ('route', dict(route='native')),
                     ('classification', dict(demand='light')), ('sample', dict(pressure=1)),
                     ('reservation', dict(job='j'))]
        with Store(self.analytics) as store:
            # Incident evidence is the OLDEST data, so age alone would evict it first.
            for seq, (event, fields) in enumerate(protected, 1):
                self.assertTrue(store.insert(self.row(event, seq, **fields)))
            for seq in range(100, 400):
                self.assertTrue(store.insert(self.row('sampling', seq, sample_call_ms=3)))
            for seq in range(400, 450):
                self.assertTrue(store.insert(self.row('hook', seq, phase='PreToolUse')))
            # Unclassified volume goes first, even though it is newest.
            self.assertEqual(store.evict(50), 50)
            kinds = [r['event'] for r in store.rows()]
            self.assertNotIn('hook', kinds)
            self.assertEqual(kinds.count('sampling'), 300)
            # Then routine sampling, before any rarer decision.
            self.assertEqual(store.evict(300), 300)
            remaining = sorted(r['event'] for r in store.rows())
            self.assertEqual(remaining, sorted(e for e, _ in protected))
            # Protected evidence is still bounded: it goes only when nothing else is left.
            self.assertEqual(store.evict(1), 1)
            self.assertNotIn('action', [r['event'] for r in store.rows()])

    def test_incident_bundles_still_carry_retained_sampling_rows(self):
        from incident_evidence import capture
        state = self.analytics.parent
        with Store(self.analytics) as store:
            store.insert(self.row('stalled', 1, job='j'))
            store.insert(self.row('sampling', 2, sample_call_ms=4))
        path = capture(state, now=1100)
        self.assertIsNotNone(path)
        bundle = json.loads(gzip.decompress(Path(path).read_bytes()))
        self.assertEqual(sorted(r['event'] for r in bundle['events']), ['sampling', 'stalled'])

    def test_compaction_failure_keeps_the_legacy_layout_working(self):
        self.legacy_fill()
        with patch.object(Store, 'compact', lambda self: None):
            with Store(self.analytics, limit=LIMIT) as store:
                self.assertIn('WITHOUT ROWID', store.db.execute(
                    "SELECT sql FROM sqlite_master WHERE name='events'").fetchone()[0].upper())
                store.maintain(now=10 ** 6)
                self.assertTrue(store.insert(self.row('action', 10 ** 6, action='signal')))
        rows, _ = read_rows(self.analytics)
        self.assertTrue(any(r['event'] == 'action' for r in rows))


if __name__ == '__main__':
    unittest.main()
