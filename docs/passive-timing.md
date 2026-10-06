# Passive latency evidence

The October 6 investigation found slot waits and shared-sampler waits during a
busy period, but did not establish that more permissive admission would help.
The next experiment measures those delays without changing admission, freshness,
pressure checks, reservations, cleanup, worker counts or VM settings.

## Queue hook

On macOS, `hook_timing.hook_ms` measures the dispatcher's kernel process birth
through response output flush. The Homebrew wrapper execs Bash in the same PID;
its startup, Bash configuration loading, Python startup/imports, input parsing,
classification and existing route telemetry are included. The kernel's process
start absolute time and Mach absolute clock use the same awake-time domain.
No extra process or wall-clock conversion is used.

This is **not full process-completion time**: final Python/Bash teardown and the
new timing event's own emission follow the endpoint. Feedback/Stop hooks, paused
hooks and early exits before the Python hook are not measured. Missing native
process evidence is omitted, never recorded as zero. The separate `guard_ms`
component and Claude's all-agent-hook aggregate keep their existing meanings.
Session and operation hashes join a timing row to its route. Reports separate
native and managed routes; compare like routes and sample coverage.

## Shared sampler

One `sampling` observation follows each real shared-sampler call outside the
admission lock. No new sampler, wait, cache read or retry is introduced.

| Field | Meaning |
| --- | --- |
| `sampling_path` | 0 unknown/fault, 1 cache, 2 elected probe, 3 busy-lock handoff, 4 busy return |
| `sample_call_ms` | Time inside the shared-sampler call, including handoff waiting and publication/event-writing overhead |
| `sample_ready_age_ms` | Age of the consumed, usable sample at return, when less than the unchanged two-second freshness limit |
| `sampler_retry_ms` | End of the previous busy call to beginning of this supervisor's next call |
| `sampler_ready_to_retry_ms` | Readiness of the consumed sample to retry, only when it became ready after the prior busy return and before this call |

Readiness is stamped before JSON serialization and atomic publication. Thus
ready-to-retry is an **upper bound for the consumed sample**, not an exact
publication delay. Another sample may have been published and replaced between
calls. It does not prove first availability or avoidable latency, and may include
other supervisor work. Both waiting and running supervisors contribute. Probe
duration remains a separate existing metric. Per-call paths are not persisted in
the shared cache or used for admission.

Records use the existing private, bounded local analytics store and numeric
allowlists. They add no commands, paths or public reporting. Recorder errors
cannot change admission or hook results. Historical missing values remain unknown
in release comparisons; no speedup is claimed by adding measurement.

Existing stable trusted hooks load the new code on their next invocation. No
profile rewrite, trust change or session restart is required. Already-running
supervisors retain their loaded code and will lack these measurements. An enabled
long-lived analytics collector must load the new event allowlist to retain them.

## Validation and comparison

Keep the saved v0.28.1 baseline and collect a comparable busy workday before
deciding whether to optimize. Inspect route-specific hook latency, sampler path
counts and retry distributions together with queue waits, pressure, job mix and
coverage. Use the existing immutable `analytics snapshot` and `release-compare`
commands. No historical metric is reset or relabeled.

Tests cover a deterministic busy/publication/retry timeline, stale/foreign samples,
unchanged probe count and cache freshness, recorder failure, missing process
clocks, numeric privacy, old reports, and the real sandboxed macOS hook process.
The initial seven tests failed against the old implementation (one missing-path
assertion and six missing-API/report errors) before implementation.
