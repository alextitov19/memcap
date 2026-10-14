"""Single-writer bounded SQLite history; never opens enforcement state for write."""
import json
import os
from pathlib import Path
import sqlite3
import time

from analytics_events import sanitize

LIMIT_BYTES = 256 * 1024 * 1024
RAW_DAYS = 14
CHECKPOINT_DAYS = 90
# High-volume host hook/API bookkeeping must not evict the very decisions needed
# to explain a queue incident. This remains a bounded ring, not unlimited history.
EVIDENCE_EVENTS = ('classification', 'route', 'hook_timing', 'sampling', 'sample',
                   'reservation', 'queued', 'admitted', 'completed', 'cancelled',
                   'stalled', 'action', 'stop_wait', 'cancellation')
# Routine high-rate observations stay in incident bundles while retained, but
# under page pressure they are evicted before rarer decisions. Their latency
# metrics are already preserved in the hourly histograms at insertion time.
ROUTINE_EVIDENCE = ('sampling',)
PROTECTED_EVENTS = tuple(e for e in EVIDENCE_EVENTS if e not in ROUTINE_EVIDENCE)
# Free-list pages returned to the filesystem per maintenance pass.
RECLAIM_PAGES = 8192
SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  producer TEXT NOT NULL, seq INTEGER NOT NULL, wall REAL NOT NULL,
  mono REAL NOT NULL, boot TEXT NOT NULL, event TEXT NOT NULL,
  session TEXT, job TEXT, operation TEXT, build TEXT NOT NULL, policy TEXT NOT NULL,
  data TEXT NOT NULL, delivery TEXT, PRIMARY KEY(producer, seq)
);
CREATE INDEX IF NOT EXISTS events_time ON events(wall);
CREATE INDEX IF NOT EXISTS events_session ON events(session, wall);
CREATE INDEX IF NOT EXISTS events_job ON events(job, wall);
CREATE UNIQUE INDEX IF NOT EXISTS events_delivery ON events(delivery) WHERE delivery IS NOT NULL;
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS job_events (
  job TEXT NOT NULL, event TEXT NOT NULL, wall REAL NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY(job,event)
);
CREATE TABLE IF NOT EXISTS hourly (
  hour INTEGER NOT NULL, build TEXT NOT NULL, policy TEXT NOT NULL,
  family TEXT NOT NULL, metric TEXT NOT NULL, bucket INTEGER NOT NULL,
  count INTEGER NOT NULL, total REAL NOT NULL, minimum REAL NOT NULL, maximum REAL NOT NULL,
  PRIMARY KEY(hour, build, policy, family, metric, bucket)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS diagnostics (
  producer TEXT NOT NULL, seq INTEGER NOT NULL, wall REAL NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY(producer,seq)
);
PRAGMA user_version=1;
"""
# Event JSON is usually 0.7-2 KB. In a WITHOUT ROWID (index) b-tree on 4 KiB
# pages, payload above ~1 KB spills to a private overflow page, so each row
# occupied a mostly empty page and the cap held about a quarter of its data.
# Ordinary rowid tables keep these rows on shared leaf pages. The schema
# version is unchanged: older readers and writers accept either layout.
ROWID_TABLES = {
    'events': ('producer,seq,wall,mono,boot,event,session,job,operation,build,policy,data,delivery',
               """CREATE TABLE events_compact (
  producer TEXT NOT NULL, seq INTEGER NOT NULL, wall REAL NOT NULL,
  mono REAL NOT NULL, boot TEXT NOT NULL, event TEXT NOT NULL,
  session TEXT, job TEXT, operation TEXT, build TEXT NOT NULL, policy TEXT NOT NULL,
  data TEXT NOT NULL, delivery TEXT, PRIMARY KEY(producer, seq))"""),
    'job_events': ('job,event,wall,data', """CREATE TABLE job_events_compact (
  job TEXT NOT NULL, event TEXT NOT NULL, wall REAL NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY(job,event))"""),
    'diagnostics': ('producer,seq,wall,data', """CREATE TABLE diagnostics_compact (
  producer TEXT NOT NULL, seq INTEGER NOT NULL, wall REAL NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY(producer,seq))"""),
}


def private_directory(directory):
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = directory.stat()
    if directory.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("analytics directory must be private and owned by this user")
    for name in ("history.sqlite", "history.sqlite-wal", "history.sqlite-shm"):
        path = directory / name
        if path.is_symlink() or (path.exists() and path.stat().st_uid != os.getuid()):
            raise ValueError("unsafe analytics database")


class Store:
    def __init__(self, directory, limit=LIMIT_BYTES):
        self.directory = Path(directory)
        private_directory(self.directory)
        self.limit = max(1024 * 1024, limit)
        self.path = self.directory / "history.sqlite"
        self.db = sqlite3.connect(self.path, timeout=0)
        os.chmod(self.path, 0o600)
        if self.db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
            self.db.close()
            raise ValueError("unsupported analytics schema; existing history preserved")
        # Takes effect immediately for a new file; an existing file converts
        # during the one-time compaction below.
        self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=0")
        self.db.execute("PRAGMA cache_size=-1024")
        self.db.execute("PRAGMA wal_autocheckpoint=256")
        self.db.execute("PRAGMA journal_size_limit=1048576")
        # Compact before applying the cap: a full legacy file must still be
        # able to copy its rows. Failure keeps the legacy layout working.
        self.compact()
        self.db.execute(f"PRAGMA max_page_count={int(self.limit * .65) // 4096}")
        self.db.executescript(SCHEMA)
        self.last_sample = None

    def compact(self):
        """One-time, collector-startup conversion; never runs on the insert path."""
        try:
            legacy = [name for name, sql in self.db.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='table' AND name IN (?,?,?)",
                tuple(ROWID_TABLES)) if sql and 'WITHOUT ROWID' in sql.upper()]
            if legacy:
                self.db.execute("BEGIN IMMEDIATE")
                for name in legacy:
                    columns, create = ROWID_TABLES[name]
                    self.db.execute(f"DROP TABLE IF EXISTS {name}_compact")
                    self.db.execute(create)
                    self.db.execute(f"INSERT OR IGNORE INTO {name}_compact ({columns}) SELECT {columns} FROM {name}")
                    self.db.execute(f"DROP TABLE {name}")
                    self.db.execute(f"ALTER TABLE {name}_compact RENAME TO {name}")
                self.db.commit()
            if legacy or self.db.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:
                # VACUUM applies incremental auto-vacuum to an existing file and
                # returns the converted tables' pages to the filesystem.
                self.db.execute("VACUUM")
                self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            if self.db.in_transaction:
                self.db.rollback()
            try:
                self.db.executescript(SCHEMA)
                self.count("compaction_failures")
                self.db.commit()
            except sqlite3.Error:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.commit()
        self.db.close()

    def count(self, name, amount=1):
        self.db.execute("INSERT INTO counters VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value", (name, amount))

    def insert(self, row):
        # Bound WAL growth even when an external reader pins a checkpoint.
        # The DB itself is capped at 65%; reserve room for indexes and a batch.
        wal = self.directory / "history.sqlite-wal"
        if wal.exists() and wal.stat().st_size >= self.limit * .20:
            raise sqlite3.OperationalError("analytics WAL budget reached; collection paused until checkpoint")
        row = sanitize(row)
        if row is None:
            self.count("rejected_events")
            return False
        values = [row.get(k) for k in ("producer", "seq", "wall", "mono", "boot", "event", "session", "job", "operation", "build", "policy")]
        cursor = self.db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (*values, json.dumps(row, separators=(",", ":")), row.get("delivery")))
        if cursor.rowcount == 0:
            return False
        if row['event'] in {'action', 'cancellation'}:
            self.db.execute('INSERT OR IGNORE INTO diagnostics VALUES (?,?,?,?)',
                            (row['producer'], row['seq'], row['wall'], json.dumps(row, separators=(',', ':'))))
        if row.get("job") and row["event"] in {"queued", "admitted", "completed", "cancelled", "stalled", "claim"}:
            # Small lifecycle checkpoints survive eviction of high-rate samples.
            # One row per job/phase; a stalled job never creates an unbounded log.
            self.db.execute("""INSERT INTO job_events VALUES (?,?,?,?)
                ON CONFLICT(job,event) DO UPDATE SET wall=excluded.wall,data=excluded.data
                WHERE excluded.wall>=job_events.wall""",
                (row["job"], row["event"], row["wall"], json.dumps(row, separators=(",", ":"))))
        if row["event"] == "sample":
            previous = self.last_sample
            if previous and previous["boot"] == row["boot"] and row["boot"] != "0" * 32:
                elapsed = row["mono"] - previous["mono"]
                if 0 < elapsed <= 90 and abs(row["wall"] - previous["wall"] - elapsed) < 2 and previous.get("pressure") in (1, 2, 4) and not previous.get("measurement_fault"):
                    cursor_wall = previous["wall"]
                    while cursor_wall < row["wall"]:
                        end = min(row["wall"], (int(cursor_wall // 3600) + 1) * 3600)
                        self.histogram(previous, "pressure_" + str(previous["pressure"]) + "_ms", (end - cursor_wall) * 1000, hour=int(cursor_wall // 3600))
                        cursor_wall = end
            if not previous or row["wall"] > previous["wall"]:
                self.last_sample = row
        for metric in ("queue_wait_ms", "runtime_ms", "hook_ms", "guard_ms", "duration_ms", "swap_in_kbps", "swap_out_kbps", "available_kb",
                       "sample_call_ms", "sample_ready_age_ms", "sampler_retry_ms", "sampler_ready_to_retry_ms",
                       "wired_kb", "physical_memory_kb", "kernel_data_1024_inuse_kb", "kernel_data_shared_1024_inuse_kb"):
            if metric not in row or (metric == "queue_wait_ms" and row["event"] == "stalled"):
                continue
            value = row[metric]
            # Mergeable logarithmic histogram; no averaging of percentile values.
            self.histogram(row, metric, value)
        return True

    def histogram(self, row, metric, value, hour=None):
        bucket = int(value).bit_length()
        key = (int(row["wall"] // 3600) if hour is None else hour, row["build"], row["policy"], row.get("family", "unknown"), metric, bucket)
        self.db.execute("""INSERT INTO hourly VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(hour,build,policy,family,metric,bucket) DO UPDATE SET
                count=count+1,total=total+excluded.total,
                minimum=min(minimum,excluded.minimum),maximum=max(maximum,excluded.maximum)""",
                        (*key, 1, value, value, value))

    def rows(self, since=0, limit=200000):
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM events WHERE wall>=? ORDER BY wall LIMIT ?", (since, limit))]

    def health(self):
        result = dict(self.db.execute("SELECT name,value FROM counters"))
        result.setdefault("evicted_events", 0)
        result["disk_bytes"] = sum(p.stat().st_size for p in self.directory.iterdir() if p.is_file() and not p.is_symlink())
        result["retained_events"] = self.db.execute("SELECT count(*) FROM events").fetchone()[0]
        result["rollup_bins"] = self.db.execute("SELECT count(*) FROM hourly").fetchone()[0]
        return result

    def evict(self, count):
        # Unclassified volume first, then routine high-rate evidence, then the
        # rare decisions an incident report needs; oldest first in each tier.
        protected = ','.join('?' for _ in PROTECTED_EVENTS)
        routine = ','.join('?' for _ in ROUTINE_EVIDENCE)
        return self.db.execute(
            'DELETE FROM events WHERE (producer,seq) IN '
            '(SELECT producer,seq FROM events ORDER BY '
            f'CASE WHEN event IN ({protected}) THEN 2 WHEN event IN ({routine}) THEN 1 ELSE 0 END,'
            'wall LIMIT ?)',
            (*PROTECTED_EVENTS, *ROUTINE_EVIDENCE, count)).rowcount

    def reclaim(self):
        """Return bounded free-list pages so the file does not stay at its cap."""
        try:
            if self.db.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:
                return 0
            free = self.db.execute("PRAGMA freelist_count").fetchone()[0]
            if not free:
                return 0
            self.db.execute(f"PRAGMA incremental_vacuum({min(free, RECLAIM_PAGES)})").fetchall()
            self.db.commit()
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return free - self.db.execute("PRAGMA freelist_count").fetchone()[0]
        except sqlite3.Error:
            if self.db.in_transaction:
                self.db.rollback()
            return 0

    def maintain(self, now=None):
        now = time.time() if now is None else now
        self.db.execute("DELETE FROM job_events WHERE job IN (SELECT job FROM job_events GROUP BY job HAVING max(wall)<?)", (now - CHECKPOINT_DAYS * 86400,))
        self.db.execute("DELETE FROM job_events WHERE job IN (SELECT job FROM job_events GROUP BY job ORDER BY max(wall) DESC LIMIT -1 OFFSET 4096)")
        self.db.execute('DELETE FROM diagnostics WHERE wall<?', (now-CHECKPOINT_DAYS*86400,))
        self.db.execute('DELETE FROM diagnostics WHERE (producer,seq) IN '
                        '(SELECT producer,seq FROM diagnostics ORDER BY wall DESC LIMIT -1 OFFSET 4096)')
        deleted = 0
        pages = self.db.execute("PRAGMA page_count").fetchone()[0]
        free = self.db.execute("PRAGMA freelist_count").fetchone()[0]
        cap = self.db.execute("PRAGMA max_page_count").fetchone()[0]
        if pages - free >= cap * .9:
            # The SQLite cap is 65% of the total disk allowance. Waiting for
            # 80% disk usage can never repair SQLITE_FULL at that earlier cap.
            # Reuse freed pages; do not raise the cap or unlink live DB/WAL files.
            count = self.db.execute("SELECT count(*) FROM events").fetchone()[0]
            batch = min(20000, max(1, (count + 4) // 5))
            deleted += self.evict(batch)
        deleted += self.db.execute("DELETE FROM events WHERE wall<?", (now - RAW_DAYS * 86400,)).rowcount
        self.db.execute("DELETE FROM hourly WHERE hour<?", (int((now - 365 * 86400) // 3600),))
        # Bound dimension cardinality as well as elapsed retention.
        self.db.execute("""DELETE FROM hourly WHERE (hour,build,policy,family,metric,bucket) IN
            (SELECT hour,build,policy,family,metric,bucket FROM hourly ORDER BY hour DESC LIMIT -1 OFFSET 100000)""")
        count = self.db.execute("SELECT count(*) FROM events").fetchone()[0]
        if count > 100000:
            deleted += self.evict(count - 100000)
        self.count("evicted_events", deleted)
        self.db.commit()
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.reclaim()
        size = self.health()["disk_bytes"]
        if size > self.limit * .8:
            deleted = self.evict(20000)
            self.count("evicted_events", deleted)
            self.db.commit()
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.reclaim()
        # Never unlink a WAL or database to reclaim space. A pinned external reader
        # causes collection to drop until its transaction closes.


def read_rows(directory, since=0, limit=100000):
    path = Path(directory) / "history.sqlite"
    if not path.exists():
        return [], {"retained_events": 0, "coverage": "recorder_not_started"}
    private_directory(Path(directory))
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.1)
    try:
        rows = [json.loads(r[0]) for r in db.execute("SELECT data FROM events WHERE wall>=? ORDER BY wall DESC LIMIT ?", (since, limit))]
        health = dict(db.execute("SELECT name,value FROM counters"))
        health.update(retained_events=db.execute("SELECT count(*) FROM events").fetchone()[0],
                      query_limit=limit, possibly_truncated=len(rows) == limit,
                      raw_oldest_wall=db.execute("SELECT min(wall) FROM events").fetchone()[0])
        health['coverage_by_event'] = {event: dict(count=count, oldest_wall=oldest, newest_wall=newest)
            for event, count, oldest, newest in db.execute(
                'SELECT event,count(*),min(wall),max(wall) FROM events GROUP BY event')}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='job_events'").fetchone():
            checkpoints = [json.loads(r[0]) for r in db.execute(
                "SELECT data FROM job_events WHERE job IN (SELECT job FROM job_events WHERE wall>=?)", (since,))]
            merged = {(r['producer'], r['seq']): r for r in rows + checkpoints}
            rows = list(merged.values())
            health['job_checkpoints'] = len(checkpoints)
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='diagnostics'").fetchone():
            diagnostic_rows = [json.loads(r[0]) for r in db.execute(
                'SELECT data FROM diagnostics WHERE wall>=?', (since,))]
            rows = list({(r['producer'],r['seq']):r for r in rows+diagnostic_rows}.values())
            health['diagnostic_checkpoints'] = len(diagnostic_rows)
        return sorted(rows, key=lambda r: (r['wall'], r['seq'])), health
    finally:
        db.close()


def read_rollups(directory, days=30):
    path = Path(directory) / "history.sqlite"
    if not path.exists():
        return []
    private_directory(Path(directory))
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.1)
    try:
        columns = ("utc_day", "build", "policy", "family", "metric", "bucket", "count", "total", "minimum", "maximum")
        result = db.execute("""SELECT hour/24,build,policy,family,metric,bucket,sum(count),sum(total),min(minimum),max(maximum)
            FROM hourly WHERE hour>=? GROUP BY hour/24,build,policy,family,metric,bucket ORDER BY hour/24""", (int((time.time() - days * 86400) // 3600),))
        return [dict(zip(columns, row)) for row in result]
    finally:
        db.close()
