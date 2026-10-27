# Release notes archive

Release notes that previously opened the README, kept verbatim for reference.
Current changes are in [CHANGELOG.md](../CHANGELOG.md).

## Incident diagnostics (v0.29–v0.31)


Android emulator listings (`emulator -list-avds`) stay native; real boots still
require admission. Heavy hook feedback names the positive demand evidence and
explains compound commands. An explicit `memcap wait; cat ...` waits for earlier
work before reading; use a separate call for independent inspection.

Cancellation telemetry records the signal received by the supervisor and whether
guarded cancellation succeeded. Scheduled signal records share its opaque job
identity. This does not identify the signal sender or prove a user cancellation,
host timeout, or final workload exit. Existing signal/ownership checks are intact.

Reports accept `--operation TOOL_CALL_ID` when the original host ID is available.
It selects private local evidence only; neither that ID nor commands are published.
Incident bundles prioritize matching decisions and lifecycle events over sample
volume, and explicitly mark truncation. Raw tracing still requires owner opt-in.
Compact lifecycle and termination checkpoints retain up to 90 days, bounded by
4,096 jobs and 4,096 diagnostic events and the existing database size limit.
Release comparisons now include matching-workload cohorts, and long-wait examples
include observed blocker durations. These are observations, not causal claims.

Direct local `node node_modules/.bin/tsc` invocations can learn a reusable compiler
profile when their configuration is supported; arbitrary JS/preloads remain
unsupported. Incomplete measurements cannot lower predictions. Learning reports
separate short runs without samples, probe faults, missing process observations
and complete runs. Sample-unavailability retries use a bounded shorter backoff;
the two-second freshness limit and final pressure check are unchanged.

The configured per-process oversized limit still applies in green pressure. A
single TypeScript checker is not made smaller by limiting parallel workers. After
an oversized termination, inspect its effective project inputs and generated
sources, use incremental/project checks when valid, and report the measured need
if it cannot fit. Do not repeat the identical failing launch or raise Node's heap
to evade the footprint limit. This release changes no owner limits or kill eligibility.

Temporary command investigation: `memcap trace on` records shell commands seen by
the installed hooks plus queue requests, blockers and completion statuses locally.
It also snapshots identity-checked existing queue supervisors. Capture expires
after 24 hours; three rotating files retain at most 24 MiB. Old files are pruned
by the analytics collector or the next trace access. `memcap analytics status`
shows whether capture is active, expired or off; expiry never renews itself.
`memcap trace show` displays the latest 200 records;
`memcap trace show JOB_ID` filters them to one queued job.
`memcap trace clear` stops capture and deletes them. Logging is best effort and
never blocks admission on a busy trace lock. Raw trace data is separate from
analytics and is never included in public reports. Existing loaded supervisors
need no cancellation, but only new runners emit subsequent trace transitions.

Local performance analytics can record queue delay, native/paused hook activity,
unfinished work, pressure/paging observations, and code/policy provenance without
changing enforcement. Run `memcap analytics enable --service --claude`, then
`memcap analytics today`. Data stays on your Mac. Existing hooks pick up recording
at their next invocation; native Claude API/token telemetry applies at session
startup. Before upgrading, save an immutable local baseline with
`memcap analytics snapshot ~/memcap-before --days 1`; compare a later snapshot
using `memcap analytics release-compare ~/memcap-before ~/memcap-after`.
See [analytics setup, interpretation, and benchmarks](docs/analytics.md).

Since v0.29.0, reports also preserve a bounded private incident bundle when local
analytics is available: up to 2,000 sanitized decision events from the preceding
30 minutes, with explicit truncation and coverage. At most twenty compressed
bundles of at most 1 MiB are retained, for seven days, under memcap's private state
directory. Raw commands never enter these bundles or public reports. Storage
pressure preferentially evicts routine hook/API records before diagnostic events;
per-event coverage remains visible because neither tier is unlimited.

## Status heartbeat rows (v0.13.0)

```
$ memcap status
memcap 0.13.0 — /Users/you/.config/memcap/memcap.conf

  agents + everything they spawn   6.93 GB / shares 20 GB total (13.53 GB pool after measured Docker use)
    of which leaked/orphaned       0.00 GB
    of which sims/playwright       0.93 GB
    net of sims (drives tier 2)    6.00 GB
  docker VM + helpers              6.47 GB / 6 GB ceiling
  ---------------------------------------------------------
  combined                        13.40 GB / 20 GB budget (shared)
  system memory available          25%
  memory measured by               top footprint (569 processes)
  last enforcement pass            3s ago
  last pass outcome                enforced
  background service               LaunchAgent loaded (com.alextitov19.memcap -- memcap's own, not a brew service)
```

The bottom four rows answer a question the earlier versions could not. A stopped
memcap used to be indistinguishable from a quiet one: `status` printed a budget,
nothing errored, and no notification fired, so a machine that had not enforced
anything in 28 hours looked exactly like one with nothing to do. Worse, the
states where memcap _refuses_ to act — an unparseable config, a Docker ceiling
that leaves agents no budget — still stamped the heartbeat, so the freshness
indicator actively vouched for them.

Each of those now has its own row and its own remedy, because the fixes differ:
a missing LaunchAgent is not a stale heartbeat, and `MC_NO_TOP=1` set on purpose
is not a `top` that failed.

## Faster admission in v0.11.0

Automatic reservations adapt to measured usage after startup, admissions rotate
between sessions, and the watchdog cancels confirmed stuck standalone simulator
boot attempts. Bounded file-reading pipelines stay outside the expensive-work
queue. Stop hooks use `feedback --wait` with `timeout: 75` and wait locally for up
to a minute instead of repeatedly waking the model. `memcap integrate` installs
these entries for both new and previously configured profiles.

Green or yellow pressure does not guarantee admission: measured usage, outstanding
reservations, configured headroom and job slots still matter. The aim is more useful
concurrency, not a promise of zero waits or zero pressure spikes. The boot deadline
releases a reservation only after its managed processes exit; it does not reset a
simulator or prove that app tests ran. See [CHANGELOG.md](CHANGELOG.md) for details.
