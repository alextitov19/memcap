"""Observation clocks only: never used for admission deadlines or ownership."""
import math
import sys
import time

FIELDS = {phase + '_' + clock + '_ms' for phase in ('queue_wait', 'runtime')
          for clock in ('awake', 'elapsed', 'sleep')}


def continuous():
    """Monotonic elapsed time including suspend; unavailable stays unknown."""
    name = 'CLOCK_MONOTONIC_RAW' if sys.platform == 'darwin' else 'CLOCK_BOOTTIME'
    try:
        value = time.clock_gettime(getattr(time, name))
        return value if math.isfinite(value) else None
    except (AttributeError, OSError, ValueError):
        return None


def phase_fields(job, phase):
    if not isinstance(job, dict):
        return {}
    prefix = 'enqueued' if phase == 'queue_wait' else 'start'
    awake_start = job.get(prefix + '_monotonic')
    elapsed_start = job.get(prefix + '_continuous')
    awake_end, elapsed_end = time.monotonic(), continuous()
    fields = {}
    if not isinstance(awake_start, (int, float)) or not math.isfinite(awake_start):
        return fields
    awake = awake_end - awake_start
    if awake < 0:
        return fields
    fields[phase + '_awake_ms'] = int(awake * 1000)
    if not isinstance(elapsed_start, (int, float)) or elapsed_end is None:
        return fields
    elapsed = elapsed_end - elapsed_start
    # Small skew is possible between the two adjacent clock reads. Larger
    # disagreement is uncertainty, never evidence of negative sleep.
    if not math.isfinite(elapsed) or elapsed < 0 or elapsed + .01 < awake:
        return fields
    fields[phase + '_elapsed_ms'] = int(elapsed * 1000)
    fields[phase + '_sleep_ms'] = int(max(0, elapsed - awake) * 1000)
    return fields
