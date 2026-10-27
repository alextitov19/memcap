# v0.31 incident investigation — October 10

Baseline: the private v030-assessment-20261010 archive has 745 completed jobs,
744 measured waits, median 700 ms, p95 3,581 ms, max 392,754 ms, nine over one
minute and none over ten minutes. Production comparisons must retain workload,
policy, installed build and coverage; these numbers are not a controlled experiment.

## Incident dispositions

- #404 is a confirmed classifier defect: an Android AVD listing was classified
  like an emulator boot. Exact `emulator -list-avds` stays native, including
  absolute/tilde executable paths and inspection chains. Real boots, mixed
  listing/boot commands and executable substitutions remain heavyweight.
- #406's local tool history contains an explicit session wait followed by a
  file read; it returned successfully with the original typecheck's exit 0.
  This is not evidence of an independently queued read. Feedback explains the
  sequential wait and requests separate calls for independent inspection.
- #407's reported lightweight edit also executed a browser script in a loop.
  The retained admissions near the report do not reproduce a 90-second admission
  wait for a standalone edit. Preserve heavy admission and explain its positive
  demand reason and compound scope. Do not classify script contents being read
  as execution, or dismiss a separately observed delayed edit.
- #405 corresponds to six confirmed TypeScript footprints of 4.08–4.32 GiB,
  above the owner's 4 GiB limit. This is configured oversized enforcement. The
  limit, kill checks and signal path are unchanged. Feedback now explains that
  a single compiler is not reduced by parallel-worker limits or waiting for green
  pressure, recommends inspecting effective project inputs/generated sources,
  and requires reporting the actual capacity need instead of an identical retry.
  Direct local Node/TypeScript wrappers can now use bounded compiler prediction;
  this does not grant an exception to the oversized limit or guarantee less RAM.
- #403 matches a guarded supervisor cancellation of a mobile workload, rather
  than an oversized enforcement record. Existing evidence does not identify
  the external signal sender or establish whether it was a host deadline or user
  cancellation. New telemetry records the supervisor-received signal, guarded
  cancellation attempts/results, and correlated scheduled enforcement actions.
  A cancellation request/result is not fabricated into a final workload exit.
  The historical initiating cause remains unknown.

## Throughput and evidence

The longest v0.30 wait recorded almost its entire duration against finite slots.
Other outliers recorded slot occupancy and sample unavailability. No concurrency,
pressure, headroom or policy limits are raised. Sampling-only backoff falls from
the normal two seconds to at most 500 ms, outside the registry lock; the elected
sampler and two-second admission freshness remain intact. Incident bundles record
bounded opaque finite-slot occupants, with stored state explicitly not liveness.

The existing bounded incident collector selected the newest 2,000 records, which
could discard the decisive termination under heavy sample traffic. It now favors
selected tool-call decisions and lifecycle evidence. `report --operation` is
private and never changes public bodies or deduplication. Raw command tracing
remains separately owner-enabled and expires normally.

Rare action/cancellation checkpoints and job lifecycle checkpoints survive raw
event rotation for up to 90 days, capped at 4,096 diagnostic events and 4,096 jobs.
The database size budget is unchanged. Matched-workload comparisons are included
in immutable release comparisons; missing historical fields remain unknown.
Learning summaries distinguish short unobserved runs, failed workloads, probe
faults, unverified scope, observation gaps and missing process observations.

## Validation and rollout

New regression tests were observed failing before the corresponding fixes:
native listing, decision/wait guidance, cancellation retention, sample-flood
incident truncation, learning categories, long-wait blockers, operation selection,
matched release comparisons, direct Node compiler resolution and learning reuse.
Normal and isolated-HOME/no-Docker Bats both passed 658/658. Grouped shellcheck,
32 individually linted shell test files and 27 individual Bash parses passed.
The final review caught direct tsc.js entrypoint cache invalidation; its regression
failed before the correction and the final compiler suite passed 10/10. Initial
validation failures were corrected, not counted as successful runs. CI and install
results are recorded in the release PR and local rollout receipt.

Existing jobs keep their loaded supervisor. Stable hooks pick up installed code;
only the analytics collector requires a refresh for its new storage behavior.
No engine restart, profile edit, hook trust change or live queue manipulation is
required. No production latency improvement is claimed until the next comparable
run. The historical #403 initiating cause is not retroactively reconstructed.
