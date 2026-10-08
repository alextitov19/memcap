"""Fast owned-workload physical observations. Never signals or admissions.

Host admission keeps its own full snapshot and freshness. A process measurement
cannot establish host headroom, release a lease, or authorize cancellation.
"""
import os
import time

REASONS = ('anchor', 'members', 'usage', 'identity', 'refresh', 'gap', 'fault')


def members(job, table, uid):
    excluded = job.get('observation_exclusions', {})
    table = {p:r for p,r in table.items() if excluded.get(p) != r.get('start')}
    anchors = {p:s for p,s in job.get('footprint_members', job.get('members', {})).items()
               if p in table and table[p].get('start') == s and table[p].get('uid') == uid}
    if not anchors:
        return {}
    owned = dict(anchors)
    for pid, row in table.items():
        if row.get('uid') == uid and row.get('group') == job.get('group') and pid != str(job.get('owner')):
            owned[pid] = row['start']
    changed = True
    while changed:
        changed = False
        for pid, row in table.items():
            if pid not in owned and row.get('uid') == uid and str(row.get('ppid')) in owned and pid != str(job.get('owner')):
                owned[pid] = row['start']
                changed = True
    return owned


def sample_job(job, table_reader, usage_reader, *, uid=None):
    uid = os.getuid() if uid is None else uid
    began = time.monotonic()
    result = dict(complete=False, peak_kb=0, identities={}, footprints={}, at=began,
                  usage_identities={}, missing=0, fault=0, duration_ms=0, reasons={})
    try:
        before = members(job, table_reader(), uid)
        if not before:
            result['reasons']['anchor'] = 1
            return result
        reads = {pid:usage_reader(int(pid)) for pid in before}
        after = members({**job, 'footprint_members':before}, table_reader(), uid)
        result['identities'] = before
        result['complete'] = before == after
        if before != after:
            result['reasons']['members'] = 1
        for pid in before:
            first = reads[pid]
            second = usage_reader(int(pid)) if after.get(pid) == before[pid] else None
            previous = job.get('owned_observation',{}).get('usage_identities',{}).get(pid)
            if (not first or not second or first.get('identity') != second.get('identity')
                    or previous is not None and previous != first.get('identity')
                    or type(first.get('identity')) is not int or first['identity'] <= 0
                    or any(type(v.get('footprint_kb')) is not int or v['footprint_kb'] < 0 for v in (first, second))):
                result['complete'] = False
                result['missing'] += 1
                reason = 'usage' if not first or not second else 'identity'
                result['reasons'][reason] = result['reasons'].get(reason, 0) + 1
                # A prior paired kernel identity can authenticate this first
                # read even when the process exits before the second. Retain
                # upward evidence only; it never certifies a complete interval.
                if (first and type(first.get('identity')) is int
                        and first['identity'] > 0 and previous == first['identity']
                        and type(first.get('footprint_kb')) is int and first['footprint_kb'] >= 0):
                    result['footprints'][pid] = first['footprint_kb']
                continue
            result['usage_identities'][pid] = first['identity']
            # A sum of per-process maxima can overestimate this short interval,
            # which is preferable to discarding observed growth.
            result['footprints'][pid] = max(first['footprint_kb'], second['footprint_kb'])
        result['peak_kb'] = sum(result['footprints'].values())
        # Additions after the first process snapshot are observation obligations,
        # just like children found by the later registry refresh. They do not
        # prove an unmeasured child disappeared. Preserve their identities even
        # if they exit before refresh, so reconcile cannot silently forget them.
        births = {pid:start for pid,start in after.items() if pid not in before}
        if (births and not result['missing']
                and all(after.get(pid)==start for pid,start in before.items())):
            result['births'] = births
    except Exception:
        # Probe adapters also raise their own errors (including QueueError and
        # subprocess timeouts). Observation is optional evidence, never grounds
        # for abandoning supervision of an already launched child. Cancellation
        # and interpreter exit are BaseException subclasses and still propagate.
        result['complete'] = False
        result['fault'] = 1
        result['reasons']['fault'] = 1
    result['at'] = time.monotonic()
    result['duration_ms'] = (result['at'] - began)*1000
    return result


def complete_at(state, now):
    """A terminal interval without fresh observation cannot certify a low peak."""
    last = state.get('last')
    return bool(state.get('complete') and not state.get('pending') and isinstance(last, (int, float))
                and 0 <= now-last <= 5)


def reconcile(state, sample, current):
    """Account for identities found after a paired observation finished.

    A new child is an evidence obligation, not a retrospectively failed probe.
    Losing that identity before a complete paired read permanently poisons
    downward learning. Stale observations cannot settle an obligation.
    """
    if sample['at'] <= state.get('last', -1):
        return state, False
    state = dict(state)
    pending = dict(state.get('pending', {}))
    identities = sample.get('identities', {})
    failed = resolved = seen = 0
    for pid,start in sample.get('births', {}).items():
        if pending.get(pid) != start:
            # PID reuse must not erase the older child's unmeasured lifetime.
            if pid in pending:
                failed += 1
            pending[pid] = start
            seen += 1
    for pid, start in list(pending.items()):
        if (sample['complete'] and identities.get(pid) == start
                and pid in sample.get('footprints', {})):
            del pending[pid]
            resolved += 1
        elif current.get(pid) != start:
            del pending[pid]
            failed += 1
    for pid, start in current.items():
        if identities.get(pid) != start and pending.get(pid) != start:
            pending[pid] = start
            seen += 1
    if failed:
        sample = {**sample, 'complete': False,
                  'missing': sample.get('missing', 0) + failed,
                  'reasons': {**sample.get('reasons', {}), 'refresh': failed}}
    state.update(pending=pending, pending_seen=state.get('pending_seen', 0)+seen,
                 pending_resolved=state.get('pending_resolved', 0)+resolved)
    state = accumulate(state, sample)
    state['complete'] = state['complete'] and not pending
    return state, bool(sample['complete'] and not pending)


def terminal_empty(sample, result, current_members):
    """A verified completed group has no new live interval to measure.

    This only selects prior evidence; complete_at still checks its freshness.
    Any identities or probe fault make the sample real uncertainty to retain.
    """
    return bool(sample is not None and result is not None and not current_members
                and not sample.get('identities') and not sample.get('fault'))


def accumulate(state, sample):
    result = dict(state)
    at = sample['at']
    if at <= result.get('last', -1):
        return result
    gap = at-result.get('last',result.get('began',at))
    reasons = dict(result.get('reasons', {}))
    for reason in REASONS:
        reasons[reason] = reasons.get(reason, 0) + sample.get('reasons', {}).get(reason, 0)
    reasons['gap'] += int(gap > 5)
    birth_only = bool(sample.get('births')) and not sample.get('missing') and not sample.get('fault')
    incomplete = result.get('incomplete', False) or (not sample['complete'] and not birth_only) or gap > 5
    result.update(last=at, incomplete=incomplete, reasons=reasons,
                  samples=result.get('samples', 0)+int(sample['complete']),
                  peak_kb=max(result.get('peak_kb',0),sample['peak_kb']),
                  missing=result.get('missing',0)+sample.get('missing',0),
                  faults=result.get('faults',0)+sample.get('fault',0),
                  probe_ms=result.get('probe_ms',0)+sample.get('duration_ms',0))
    result['usage_identities'] = sample.get('usage_identities',{})
    result['complete'] = result['samples'] >= 2 and not incomplete
    return result
