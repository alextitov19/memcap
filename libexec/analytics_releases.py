"""Immutable local release evidence. No commands, policy changes or publication."""
import collections
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from analytics_events import sanitize
from analytics_reports import summarize


def version(number):
    if type(number) is not int or number < 0:
        return None
    return f'{number // 1000000}.{number // 1000 % 1000}.{number % 1000}'


def window(rows, since, until):
    selected = [r for r in rows if since <= r['wall'] <= until]
    jobs = {r['job'] for r in selected if r.get('job')}
    # Retain earlier lifecycle endpoints, never later outcomes: waiters remain
    # censored at the historical cutoff instead of becoming tomorrow's success.
    return [r for r in rows if r['wall'] <= until and
            (r['wall'] >= since or (r.get('job') in jobs and r['event'] in
                                   {'queued', 'admitted', 'completed', 'cancelled'}))]


def snapshot(rows, health, output, *, since, until=None):
    until = time.time() if until is None else until
    if not 0 <= since <= until:
        raise ValueError('invalid snapshot time window')
    output = Path(output).absolute()
    # Never replace an earlier baseline, including dangling links.
    if os.path.lexists(output):
        raise ValueError('snapshot output already exists; choose a new name')
    selected = window([clean for r in rows if (clean := sanitize(r))], since, until)
    groups = collections.defaultdict(list)
    for row in selected:
        groups[(row['build'], row['policy'])].append(row)
    cohorts = []
    for (build, policy), events in sorted(groups.items()):
        versions = sorted({version(r['runner_version']) for r in events
                           if type(r.get('runner_version')) is int})
        cohorts.append(dict(build=build, policy=policy,
                            version=versions[0] if len(versions) == 1 else None,
                            observed_versions=versions, events=len(events),
                            summary=summarize(events, health, now=until)))
    payload = gzip.compress((''.join(json.dumps(r, sort_keys=True) + '\n' for r in selected)).encode(), mtime=0)
    manifest = dict(schema=1, since=since, until=until, created=time.time(), events=len(selected),
                    events_sha256=hashlib.sha256(payload).hexdigest(), cohorts=cohorts,
                    summary=summarize(selected, health, now=until),
                    interpretation='Local observational evidence; version comes from recorded runner metadata, never install time. Unknown versions remain unknown. Build and policy hashes are authoritative. Earlier job endpoints may precede the selected window. Retention and delivery gaps limit coverage; raw commands are excluded.')
    # Stage privately, then write the manifest last as the completion marker.
    with tempfile.TemporaryDirectory(prefix='.memcap-snapshot-', dir=output.parent) as tmp:
        stage = Path(tmp) / 'snapshot'
        stage.mkdir(mode=0o700)
        for name, data in [('events.jsonl.gz', payload), ('manifest.json', (json.dumps(manifest, indent=2) + '\n').encode())]:
            with (stage / name).open('xb') as stream:
                os.chmod(stage / name, 0o600)
                stream.write(data)
        # mkdir is exclusive even against a concurrent snapshot publisher.
        output.mkdir(mode=0o700)
        try:
            for name in ('events.jsonl.gz', 'manifest.json'):
                path = stage / name
                os.rename(path, output / path.name)
        except OSError:
            # A manifest is the completion marker; never report success here.
            raise
    return manifest


def load(path):
    path = Path(path)
    manifest = json.loads((path / 'manifest.json').read_text())
    payload = (path / 'events.jsonl.gz').read_bytes()
    if manifest.get('schema') != 1 or hashlib.sha256(payload).hexdigest() != manifest.get('events_sha256'):
        raise ValueError('invalid snapshot or changed event archive')
    return manifest


def compare_snapshots(baseline, candidate):
    a, b = load(baseline), load(candidate)
    def metrics(report):
        done, pending, machine = report['completed_evidence'], report['pending'], report['machine']
        return dict(completed_wait_median_ms=done['queue_wait_ms']['median'],
                    completed_wait_p95_ms=done['queue_wait_ms']['p95'],
                    completed_amplification_median=done['amplification_min_runtime_10ms']['median'],
                    pending_oldest_ms=pending['age_ms']['max'],
                    red_fraction=machine['red_seconds'] / machine['observed_seconds'] if machine['observed_seconds'] else None,
                    guard_p95_ms=report['guard_ms']['p95'],
                    admissions_below_prior=report.get('learning_effectiveness',{}).get('admissions_below_prior'),
                    compiler_profile_admissions=report.get('learning_effectiveness',{}).get('compiler_profile_admissions'),
                    sampling_busy_count=report.get('learning_effectiveness',{}).get('sampling_busy_count'),
                    sampling_expired_count=report.get('learning_effectiveness',{}).get('sampling_expired_count'))
    def changes(left, right):
        before, after = metrics(left), metrics(right)
        return {key: dict(baseline=before[key], candidate=after[key],
                          delta=after[key] - before[key] if before[key] is not None and after[key] is not None else None)
                for key in before}
    return dict(causal=False, deltas=changes(a['summary'], b['summary']),
                baseline=dict(since=a['since'], until=a['until'], cohorts=a['cohorts']),
                candidate=dict(since=b['since'], until=b['until'], cohorts=b['cohorts']),
                interpretation='Descriptive differences, not proof of improvement. Compare equal windows and similar workload/worker, policy, enforcement and host conditions. Positive delay/pressure deltas warrant investigation. p95 needs 20 observations. Pending age is censored and liveness unverified; unknown values stay null.')
