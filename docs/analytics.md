# Local performance analytics

From v0.26, the `timing` report includes separate `queue_wait_*_ms` and
`runtime_*_ms` distributions for awake, elapsed (including system sleep), and
sleep time. The sleep-inclusive clock is monotonic and unaffected by wall-clock
adjustments. Unsupported clocks and older recordings stay unknown. The existing
`queue_wait_ms` and `runtime_ms` fields retain their original clock basis, which
excludes sleep on macOS, so historical comparisons do not silently change meaning.
Admission deadlines also retain their existing clock behavior. A historical
wall/monotonic discrepancy alone cannot distinguish sleep from a clock adjustment;
`wall_divergent_jobs` flags it without assigning a cause. New timing fields are
captured before event emission and carried in local release snapshots.
Pending timing distributions end at the last recorded stall observation and do
not imply current liveness or extrapolate sleep up to the report cutoff.

Analytics measures three separate things: developer delay, machine health, and
the quality/cost of the observations. It does not tune policy, resume enforcement,
release reservations, or send analytics to GitHub. Public incident reporting keeps
its separate consent and numeric allowlist.

## Install and inspect

```sh
memcap analytics enable --service --claude
memcap analytics status
memcap analytics today
memcap analytics today --json
memcap analytics today --build BUILD_PREFIX --enforcement active --json
memcap analytics builds
memcap analytics trends --days 30
memcap analytics explain ANALYTICS_SESSION_OR_JOB_ID
memcap analytics compare BASELINE_BUILD_PREFIX CANDIDATE_BUILD_PREFIX
memcap analytics html ~/Downloads/memcap-performance.html
```

`enable` explicitly creates private analytics state. `--service` installs the
independent `com.memcap.analytics` LaunchAgent; it does not modify the enforcement
service or pause marker. `--claude` configures detected default/personal global
Claude profiles. `--claude-dir PATH` selects another profile. Existing exporters
and content-enabled telemetry settings are preserved and reported as a conflict;
hooks still work. Settings changes use the integration installer's private backups,
concurrency checks, symlink preservation, and rollback. Repeating installation
does not rewrite unchanged profile files. No Codex trust is approved or changed.

The existing stable `feedback` and `queue-hook` commands collect events from the
next invocation after code installation. Current sessions therefore gain hook
coverage without a restart, provided those hooks were already active. Already
loaded supervisors keep their old code/policy; older runners only supply legacy
numeric history. Do not duplicate their jobs. Native Claude API/token events need
the startup environment, so already-open Claude sessions may need a normal restart
after their work finishes. New sessions inherit the configured environment.
Project/managed overrides can still suppress telemetry. `analytics status` reports
observed native events separately from configuration; configuration alone does not
prove delivery. Codex uses its existing hooks; unsupported token/API accounting is
unknown, and no transcript parsing or Claude usage-report hook is used.

`analytics disable` disables only analytics. It leaves the owner's enforcement
state and retained history intact. A running recorder exits normally on observing
the disabled marker. Re-enable with `enable --service` to start it again.

## Interpreting a report

- **Job-wait time** sums observed waits. **Queue exposure** unions concurrent wait
  intervals within each boot. Neither is counterfactual developer time saved.
  Completion-path wait remains unknown without dependency evidence.
- A successful managed child exit is a job success, not accepted development work.
  Stop is an attempted turn completion. Background launch acknowledgements do not
  complete jobs. Missing endpoints remain unfinished, including departed agents.
- Command-family labels describe recognizable syntax; they are not independent
  proof of lightweight classification. Read/search/SSM ground truth comes from
  reviewed fixtures. Unknown/compound forms stay visible.
- Reports break delays down by family and build/policy/enforcement state. Short
  jobs are observed runtimes of 10–5,000 ms; short duration is not proof of low
  memory use. Recorded blocker intervals use the latest cumulative value per job,
  rather than adding repeated snapshots. They do not establish causal blame.
- Managed jobs also report their admission lane (`small` or `heavy`). Historical
  jobs without this field remain `unknown`. Admission lane stays fixed for delay
  comparisons even if a growing job later moves to heavy accounting. Native and
  guarded inspection remain visible in route counts, outside these managed lanes.
- Comparisons keep paused/active/unknown states and worker counts separate;
  jobs that cross enforcement states are excluded from comparison cohorts.
  `today --build ... --enforcement active` selects the current treatment without
  blending earlier installations and the owner's overnight pause.
- Native tool duration includes network/tool time. `guard_ms` measures the Python
  classification/guard component; benchmark results separately measure complete
  queue and feedback hook processes. Neither should be called whole-session time.
- Yellow is context. Red and paging exposure are duration-weighted over fresh,
  compatible observations; gaps are unknown. Accumulated swap is not paging.
  Host totals include idle periods: inspect session timelines when evaluating
  active development rather than treating overnight time as improved productivity.
- Wired and physical memory are sampled with host counters. Once per minute the
  collector also reads two fixed kernel allocation buckets, `data.kalloc.1024`
  and `data_shared.kalloc.1024`. Only element-size × in-use-count is retained;
  unprivileged `zprint` can redact current sizes and underflow fragmentation.
  These counters can reveal accumulation, but do not identify its triggering
  process, attribute all wired memory, or prove a leak. Missing history stays
  unknown. No privileged probes, kernel changes or termination are performed.
- Collector CPU uses observed within-window counter deltas, grouped by producer
  and boot, so restarts and counters accumulated before the window cannot inflate
  or erase the result. It excludes probe children and hook producers. Guard
  component timing is reported separately from full hook and all-agent timing.
- Reservation slack uses only complete, closely spaced observations. Safety floors,
  explicit reservations, and orphan protection are not reclaimed capacity.
- Signal events record verified enforcement-scope requests and syscall results.
  A successful signal request is not proof of exit or exact memory reclaimed.
  Unlinked external signals remain unattributed. Exit 137 does not establish OOM.
- Claude API-equivalent cost is not a subscription bill. Unavailable tokens are
  unknown. Feedback-byte counts cover emitted diagnostic/Stop components, not
  measured tokens or every agent context byte.

Use explicit owner outcomes when comparing useful work:

```sh
memcap analytics work start local-task-id
memcap analytics work accept local-task-id
# Or: reject / abandon
```

Only a keyed identifier is retained. Acceptance should reflect a checked artifact
or owner judgment; do not automatically accept because the agent stopped.

Comparisons group comparable family/workload/model/cache observations, show counts,
failed/unfinished populations, median deltas, and exploratory bootstrap intervals.
Version numbers alone are insufficient: reports record source digests and loaded
policy fingerprints, including local patches. Same-version digests differ after
patching. Worker settings remain visible as treatment context. Missing workload,
cache, concurrency, or model details mean exploratory evidence. The initial flag
is at least five completed observations per cohort and a median increase exceeding
both 20% and one second. It is an investigation signal, not automatic rollback.

## Reproducible checks

```sh
memcap run -- memcap analytics benchmark --repetitions 10 --output /tmp/before.json
# After an explicitly chosen change:
memcap run -- memcap analytics benchmark --repetitions 10 --output /tmp/after.json
memcap analytics compare /tmp/before.json /tmp/after.json
```

These bounded fixtures randomize paired enabled/paused hook order in isolated
test state. They never change live policy, call AWS, launch real builds, or kill
processes. They cover native reads, searches, fake SSM syntax, compound reads,
managed negative controls, full hook timing, and producer failure overhead.
Scheduler tests cover parallel jobs, persistent resources, and ownership separately.
This is a hook/classification benchmark, not a simulation of alternate future
memory or an end-to-end agent benchmark. Repeated real work with explicit outcomes
supplies that separate evidence. Save manifests outside analytics state if they
must outlive bounded retention.

`memcap analytics import-legacy` copies retained numeric scheduler logs through
the recorder, without changing the source files. Imports deduplicate by event
content. Legacy history lacks session, effective policy, native/paused coverage,
and monotonic boot identity; these remain explicitly unknown. Delivery is best
effort and the command reports submitted and dropped records, not a durable
receipt. Bulk import makes one bounded retry on a full local socket, including
macOS `ENOBUFS`; live hook producers remain nonblocking with no retries.

## Storage and failure behavior

Private data lives in `~/.local/state/memcap/analytics/`: an owner-only key, local
OTLP token, datagram socket, SQLite database/WAL, and collector heartbeat. The
single writer accepts only a fixed vocabulary, bounded numeric values, and keyed
identifiers. Commands, paths, prompts, outputs, SSM values, account IDs, credentials,
and raw hook/OTLP payloads are discarded before persistence. HTML uses no external
assets or scripts. OTLP listens only on loopback with a private bearer token,
1 MiB request cap, finite timeout, and bounded queue. Only documented numeric
Claude log fields are adapted; beta spans are not required.

Raw events retain at most 14 days and 100,000 records. Mergeable histogram bins
retain at most 365 days and 100,000 bins. The database/WAL/metadata budget is
256 MiB; time retention is a ceiling, not a promise. Evictions are counted.
Retention also checks used SQLite pages against the database's smaller page cap,
evicting oldest raw events before that cap prevents new writes. Startup and write
errors schedule maintenance before new samples; heartbeat updates remain reachable
after database write failures. Recovery preserves the cap and remaining history,
but cannot reconstruct observations dropped while storage was unavailable.
Long external SQLite readers can pin a WAL; collection drops rather than deleting
the WAL or waiting in a hook. Reports use short read transactions. Histogram
counts/totals merge; percentile values must not be averaged.

Producer datagrams are nonblocking and have no retries/acknowledgements. Sequence
gaps and cumulative drops are reported when a subsequent event arrives; loss at
the tail of a dead producer is unknowable. Collector absence, database failure,
or malformed telemetry cannot authorize a launch, suppress enforcement, or change
the tool's result. The recorder never acquires the queue registry lock. It reuses
fresh shared samples or performs cheap host-level probes about every 10 seconds
while activity is observed, otherwise every 60 seconds. It does not scan the full
process table on each tool call. Its own peak resident size is explicitly distinct
from footprint; neither is used for admission.

Source references: [Claude hooks](https://code.claude.com/docs/en/hooks),
[Claude monitoring](https://code.claude.com/docs/en/monitoring-usage),
[SQLite WAL](https://www.sqlite.org/wal.html).
# Classifier redesign comparisons

Before changing admission policy, preserve a consistent SQLite backup, analytics
summary, installed source, queue snapshots and recorder heartbeat. Record capture
time, build, policy, enforcement pause and missing coverage. A stale recorder is
missing data, not a successful zero-delay interval. Keep command evidence private.

`memcap classify --replay tests/fixtures/demand-replay.json --cwd EMPTY_DIRECTORY`
replays reviewed labels without executing commands. It reports false-heavy and
false-light decisions and classifier latency, with a corpus digest for comparison.
The result is not an end-to-end agent speedup or measured-memory benchmark.

New local events include fixed classification reasons, confidence, classifier
version and duration. Native observations are partial physical-footprint samples.
The report separates unsampled endings from sampled lower-bound peaks; process
disappearance is not a successful command exit. Only high usage can teach future
admission. A command that exits before sampling remains unknown. Observation
does not reserve memory, authorize signals, or change worker counts.

Compare queue delay, successful runtime, prompt/task completion, polling cost,
red-pressure exposure and paging within matching workload, worker, build, policy
and pause cohorts. Measure hook and collector overhead separately. Do not claim
that a paused candidate's zero queue delay proves active admission improvement.
# Throughput reporting and retention

Finite project jobs and jobs explicitly tagged `memcap run --purpose monitoring`
are reported separately. Untagged historical monitoring remains unknown. Pending
jobs show elapsed age even if never admitted; those ages are not mixed into
completed-job wait percentiles, and historical pending observations do not prove
current process liveness. The report distinguishes observed jobs from recorded
admissions. Matched native tool spans and job queue amplification are separate
from accepted whole-session productivity.

Compact queued/admitted/completed/cancelled/stalled checkpoints survive raw-sample
eviction. They are bounded to 14 days and 4,096 recent job identities; high-volume
samples retain the existing byte cap. Coverage reports show the oldest retained
raw event and checkpoint count. Neither raw retention nor delivery is promised
complete, and old evicted endpoints cannot be reconstructed automatically.

Use native completion notifications when supported. `memcap wait ID
--until-complete` provides one blocking observer, up to 24 hours, suitable for a
host background task. It never reserves RAM or certifies workload success. Keep
the original task for its final output/status. Legacy hooks and hosts without
resumable notifications continue using 60-second blocking polls; do not end a
Stop-blocked turn in the expectation that memcap can resume the host.
# Release baselines

Save each observation window outside rolling retention before upgrading:

```sh
memcap analytics snapshot ~/memcap-before-v0.23.0 --days 1
# After a comparable day on the new release:
memcap analytics snapshot ~/memcap-after-v0.23.0 --days 1
memcap analytics release-compare ~/memcap-before-v0.23.0 ~/memcap-after-v0.23.0
```

Each new private directory contains `manifest.json` and checksummed,
sanitized `events.jsonl.gz`. Existing snapshots are never overwritten. Keep
several releases locally; snapshot archives have no automatic deletion policy.
Raw commands and tracing keys are excluded. The manifest groups exact build and
policy hashes and labels a version only when recorded runner metadata identifies
it; upgrading does not relabel old supervisors. Earlier job endpoints are retained
for jobs observed in the selected window. Events beyond the cutoff are excluded.

Compare completed-only median/p95 waits, wait/runtime amplification, oldest
pending age, red-pressure fraction and guard overhead. Counts and learning
coverage accompany each cohort. Cancelled work and pending ages never enter
completed percentiles. Missing diagnostics are unknown; p95 requires at least
20 observations. These are descriptive differences across potentially different
workloads and host conditions, not proof that a release caused an improvement.

For ordinary builds and tests, omit `--memory` to use automatic sizing. Explicit
memory is an intentional fixed floor, not an estimate that shrinks after launch.
Fixed jobs now contribute observations to future automatic estimates. Only direct
Go builds and TypeScript compiler calls can teach lower estimates from complete
measurements; tests, scripts, resources and container clients provide upward-only
evidence because they can allocate outside the measured process tree. Incomplete
measurements never justify lowering estimates. Disposable Compose
environments still require a measured stack-plus-workload reservation.

## Reusable compiler evidence and admission benefit

Direct Go builds and TypeScript compiler invocations can share a conservative
prediction across bounded source edits. The context retains the exact command,
project, tool identity, worker count, dependency/configuration contents and relevant
compiler environment. Three complete runs within seven days are required before a
prediction can fall below its startup prior. The maximum observed peak gets a 50%
margin, with a 512 MiB minimum; source growth above 20% rejects downward reuse.
Incomplete enumeration, symlinks and unsupported execution scopes retain the prior.
Go reuse requires the module root and TypeScript requires a local `tsconfig.json`
so inherited parent manifests cannot silently escape the recorded context;
unsupported subdirectory builds retain ordinary sizing.
Tests, arbitrary scripts and container clients do not gain this compiler profile.
Observed partial growth raises future predictions immediately. These are estimates,
not hard memory limits; red pressure and actual physical headroom still gate launch.
Fixed requests remain fixed floors and classification/tool permissions do not change.

New macOS supervisors measure their owned process tree independently through
identity-checked physical-footprint reads. This lets short jobs accumulate samples
without waiting for another full-machine scan. Foreign/reused PIDs, missing live
processes, gaps (including the final interval before exit) and unresolved detached
work prevent complete learning. Probe failures retain supervision and the existing
reservation rather than ending a running task. These
observations never establish host pressure/headroom or authorize signals. Older
supervisors retain their loaded observation behavior until their existing work ends.

`analytics today --json` includes `learning_effectiveness`:

- `known_admissions` and `admissions_below_prior` show how often recorded admission
  requests actually fell below their original startup priors.
- `compiler_profile_admissions` separates reusable compiler predictions from default
  and exact-workload estimates. `reuse_reasons` codes are 0 unsupported, 1 reused,
  2 cold context, 3 insufficient complete evidence, 4 expired evidence, 5 source growth,
  6 changed workers, 7 changed compiler, 8 changed dependencies/configuration,
  9 changed compiler environment. `compiler_complete_runs` accompanies admissions.
- `owned_observation_completions`, `owned_observation_complete` and `owned_probe_ms`
  distinguish observation coverage from admission benefit and measure its direct cost.
- `sampling_busy_count`, `sampling_expired_count` and `sample_cache_mismatch_count`
  distinguish unavailable samples from data that aged out before admission. These
  are completed-job decision counts, not elapsed delay. `shared_probe_ms` describes observed probe
  timings; multiple observations can refer to the same probe.

Missing historical fields remain unknown. Lower requests do not prove time saved.
Compare equal workload/worker contexts at comparable available memory, pressure and
paging; preserve pending/cancelled work and source-build identities. Wired-memory
growth and kernel-zone counters remain context, not attributed process ownership.

Comparisons retain exact-workload cohorts for older releases and add exploratory
compiler-context cohorts when both sides have that metadata. These cohorts can
overlap; do not sum their counts. Worker and enforcement scopes remain separate.

## Learning recovery in v0.25

Compare saved snapshots by runner build and policy, then compare matching commands
and worker contexts. The startup default is a fallback, not learned evidence:
valid exact-command learning remains usable when compiler reuse is unavailable.

`learning_effectiveness.exact_profile_admissions` counts admissions with complete
exact-profile evidence. It can overlap compiler-profile admissions; do not add the
two counts. `admissions_below_prior` measures actual requests below the startup
default, not counterfactual time saved. Fixed requests retain their explicit floor.

`compiler_scope_reasons` separates supported contexts (0), unsupported commands
(1), unsupported shell syntax/startup (2), environment overrides (3), package or
configuration scope (4), inspection budget (5), and compiler input scope (6).
These describe why a context could not be inspected, whereas `reuse_reasons`
describe whether existing observations were usable. No commands or paths are
included in these numeric counters.

`observation_failures` distinguishes absent anchors, membership changes, missing
usage, identity changes, registry refresh mismatches, gaps and probe faults.
`terminal_empty_observations` counts exits discovered between polling and probing;
those empty probes never count as zero memory or as complete measurements.
Previously complete observations still require a fresh terminal timestamp.
Missing live reads remain incomplete and cannot train a smaller reservation.
Old releases without these fields report unknown values rather than zero failures.

New commands through stable installed paths adopt the new runner. Existing queue
supervisors retain their loaded code until completion; never resubmit their jobs.
If analytics is enabled, refresh its collector after upgrade to accept the new
fields. This does not require changing agent hooks or restarting enforcement.
Raw command tracing is separate, owner-enabled and expires after 24 hours; an
empty recent trace is not proof that no commands ran. Keep raw evidence local.
