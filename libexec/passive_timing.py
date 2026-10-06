"""Local latency observations only. No admission, ownership or signal authority."""
import ctypes
import math
import os
import sys
import time

from scheduler_metrics import append_event, number

class SamplingTiming:
    def __init__(self):
        self.busy_ended = None

    def observe(self, directory, sample, began, ended):
        try:
            if not number(began) or not number(ended) or ended < began:
                return
            fields = dict(event='sampling', sampling_path=sample.get('sampling_path', 0),
                          sample_call_ms=(ended - began) * 1000)
            stamp = sample.get('monotonic')
            usable = not sample.get('fault') and not sample.get('busy')
            if usable and number(stamp) and 0 <= ended - stamp < 2:
                fields['sample_ready_age_ms'] = (ended - stamp) * 1000
            if self.busy_ended is not None and began >= self.busy_ended:
                fields['sampler_retry_ms'] = (began - self.busy_ended) * 1000
                # The sample actually consumed became ready after the previous
                # busy return. Older cache entries cannot establish a handoff.
                # Readiness precedes serialization/rename, so this is an upper
                # bound on publication-to-retry delay, never proven wasted time.
                if usable and number(stamp) and self.busy_ended <= stamp <= began:
                    fields['sampler_ready_to_retry_ms'] = (began - stamp) * 1000
            self.busy_ended = ended if sample.get('busy') else None
            append_event(directory, fields)
        except Exception:
            # Observation cannot turn an unavailable recorder into a denial.
            pass


def process_start(pid):
    from native_observer import usage
    observation = usage(pid)
    return observation.get('identity') if observation else None


def absolute_clock():
    if sys.platform != 'darwin':
        return None

    class Timebase(ctypes.Structure):
        _fields_ = [('numer', ctypes.c_uint32), ('denom', ctypes.c_uint32)]

    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
    lib.mach_absolute_time.argtypes = []
    lib.mach_absolute_time.restype = ctypes.c_uint64
    lib.mach_timebase_info.argtypes = [ctypes.POINTER(Timebase)]
    lib.mach_timebase_info.restype = ctypes.c_int
    base = Timebase()
    if lib.mach_timebase_info(ctypes.byref(base)) or not base.numer or not base.denom:
        return None
    return lib.mach_absolute_time(), base.numer, base.denom


def queue_hook_fields(response_flushed_ns=None):
    """macOS dispatcher birth through response flush, excluding final teardown.

    proc_start_abstime and mach_absolute_time share the kernel's awake clock.
    The dispatcher's existing Bash process also covers Homebrew's exec wrapper.
    No timer process, wall-clock conversion or shell-script launch is necessary.
    Missing/foreign process evidence stays unmeasured; never time an agent parent.
    """
    try:
        pid = int(os.environ.get('MEMCAP_HOOK_PID', '0'))
        if pid <= 1 or pid != os.getppid():
            return {}
        start = process_start(pid)
        if not start:
            return {}
        clock = absolute_clock()
        if clock is None:
            return {}
        end, numer, denom = clock
        if end < start or denom <= 0:
            return {}
        if response_flushed_ns is None:
            elapsed = (end - start) * numer / denom / 1e6
        else:
            if time.get_clock_info('monotonic').implementation != 'mach_absolute_time()':
                return {}
            elapsed = (response_flushed_ns - start * numer / denom) / 1e6
        if not math.isfinite(elapsed) or elapsed < 0:
            return {}
        return dict(hook_ms=elapsed, hook_timing_version=1)
    except Exception:
        return {}


def emit_queue_hook(payload, agent, route, response_flushed_ns):
    try:
        from analytics_events import emit, hook_fields, producer
        # No additional native probes when analytics is off/unavailable.
        if producer().socket is None:
            return
        fields = queue_hook_fields(response_flushed_ns)
        if fields:
            from scheduler_metrics import runner_version
            emit('hook_timing', **hook_fields(payload), agent=agent, route=route,
                 runner_version=runner_version(), **fields)
    except Exception:
        pass
