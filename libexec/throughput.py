"""Admission evidence and explanations. No execution or policy overrides."""
from pathlib import Path


def wait_summary(ident, jobs, now):
    """Put the actionable impasse before status text commonly truncated by hosts."""
    from scheduler_metrics import BLOCKERS
    waiting = sum(j['status'] == 'waiting' for j in jobs)
    running = len(jobs) - waiting
    oldest = max((max(0, now - j.get('started', j.get('enqueued', now))) for j in jobs), default=0)
    capacity = ' '.join(message.strip() for j in jobs if (message := capacity_message(j)))
    blockers = sorted({j.get('admission', {}).get('reason') for j in jobs
                       if j['status'] == 'waiting' and j.get('admission', {}).get('reason') in BLOCKERS})
    explanation = ('Last recorded admission blocker(s): ' + ', '.join(blockers) + '. '
                   if blockers else 'Admission blocker unavailable for this runner. ')
    return (f"memcap: {ident} " + (capacity + ' ' if capacity else '')
            + f"pending ({jobs[0]['status'] if jobs else 'unknown'}); {running} running, {waiting} queued IN THIS WAIT SCOPE (not host totals); "
            + f"oldest current phase {int(oldest)}s. " + explanation
            + f"Repeat memcap wait {ident} --timeout 60 only if native completion notification/blocking task polling is unavailable; "
            + "no job or reservation was created. Running work has already passed admission. "
            + "Read original task output for workload progress and final exit status.")


def fixed_learning_scope(argv, resource=False):
    """Only direct compiler invocations have a known local memory boundary.

    Tests, scripts and container clients may allocate in services outside the
    observed process tree. Their peaks remain upward-only evidence.
    """
    return bool(not resource and argv and
                (Path(argv[0]).name == 'tsc' or
                 (Path(argv[0]).name == 'go' and len(argv) > 1 and argv[1] == 'build')))


def pending_request(job, key, updated, exact):
    """Only complete exact evidence for a NEW allocation can lower a waiter.

    Explicit requests never enter this path. Partial observations and same-key
    revisions retain their high-water mark; fewer workers alone proves nothing.
    """
    if (job.get('elastic') and key != job.get('estimate_key')
            and type(exact.get('complete_runs')) is int and exact['complete_runs'] >= 3
            and exact.get('peaks_kb') and updated >= max(exact['peaks_kb']) * 1.25):
        return updated
    return max(job['memory_kb'], updated)


def capacity_progress(job, jobs, decision, now):
    """Detect a persistent deficit with no finite managed work to drain.

    This is diagnostic, not permission to start/stop anything. Fresh non-headroom
    evidence resets the interval; unavailable samples cannot advance it.
    """
    finite = any(j.get('status') == 'running' and not j.get('resource') for j in jobs)
    reason = decision.get('reason')
    state = job.get('capacity_progress', {})
    if reason == 'sampling':
        return state
    if finite or reason != 'headroom':
        job.pop('capacity_progress', None)
        return {}
    if not state or now - state.get('last', now) > 90:
        state = dict(since=now, max_available_kb=0)
    state.update(last=now, max_available_kb=max(state['max_available_kb'], decision.get('available_kb', 0)))
    state['decision'] = {key: decision[key] for key in
                         ('request_kb', 'headroom_kb', 'outstanding_kb', 'available_kb') if key in decision}
    state['stalled'] = now - state['since'] >= 120
    job['capacity_progress'] = state
    return state


def capacity_message(job):
    if not job.get('capacity_progress', {}).get('stalled'):
        return ''
    d = job.get('admission', {})
    if d.get('reason') != 'headroom':
        # Sampling contention is unknown, not a new zero-headroom observation.
        d = job.get('capacity_progress', {}).get('decision', {})
    if not d or 'available_kb' not in d:
        return ' Sustained capacity block recorded; current available memory is unknown. The job remains pending.'
    required = d.get('request_kb', job.get('memory_kb', 0)) + d.get('headroom_kb', 0) + d.get('outstanding_kb', 0)
    return (f" Sustained capacity block: needs {required / 1048576:.2f} GiB available; "
            f"last measured {d['available_kb'] / 1048576:.2f} GiB. "
            "No finite managed job is running that can drain. The job remains pending. "
            "Verified disposable session resources may be retired; shared, pinned or needed resources stay protected. "
            "Use a measured lower-memory workload configuration or release an owned unneeded resource; "
            "repeated status calls cannot create capacity."
            + (" This is a fixed --memory request, not an automatically learned estimate. "
               "For future ordinary builds/tests, omit --memory to use automatic sizing; "
               "do not duplicate or understate this pending request." if job.get('elastic') is False else ''))
