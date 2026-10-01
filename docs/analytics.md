# Local performance analytics

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
