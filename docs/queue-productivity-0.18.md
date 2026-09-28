# Queue productivity investigation

Baseline: v0.17.1, GitHub issues #146–#164, inspected 2026-09-28.

Observed command forms include jq interpolation and del(), git branch status,
remote calls with environment-variable arguments, and document-to-text readers.
Some reports also combine inspection with arbitrary scripts; those scripts must
still receive admission. Report snapshots cannot establish incident-time cause.

Implementation scope:

- Recognize finite jq formatting, git branch inspection and document text reads;
  guard expanded environment arguments before execution.
- Scope subagent completion and native waits to the same hook identity as jobs.
- Refresh the shared sample early without extending its two-second validity.
- Retain upward learning from incomplete runs; never learn a lower estimate from
  incomplete measurements.
- Record numeric classification, estimate provenance, reservation changes and
  blocker durations, without public commands, paths or process identities.

Unchanged: user policy, pressure checks, fresh launch validation, ownership and
cancellation rules. No unrelated workload or Docker process is terminated.

Validation on the implementation in commit `80ad792`: 613/613 normal Bats tests,
613/613 with isolated HOME and no Docker, shellcheck and 24 individual Bash parse
checks passed. Both GitHub CI runs passed (36458567198 and 36458589740).
The new eight-test regression suite against immutable v0.17.1 produced eight
failure assertions and two missing-feature import errors, as expected. No test
signals a real process or changes the live memory policy.

## Diagnosis and evidence

The first regression run against v0.17.1 produced seven failures across native
classification, guarded environment arguments and upward learning. The first
patched targeted run passed 118 tests; later scope and telemetry regressions add
coverage beyond that initial run. These are functional admission-path results,
not a measured claim about end-to-end speed on arbitrary workloads.

Completion scope must not become scheduling priority: subagents receive distinct
completion keys with a shared parent prefix. Fairness and worker allocation use
the parent; Stop and hook-generated waits use the full key. Old runners retain
their original identity and hosts without agent IDs cannot separate siblings.

An incomplete measurement previously suppressed all learning, including real
large peaks. It now only prohibits downward learning and complete-run counting.
A lower final worker allocation previously erased the estimate key entirely;
it now uses the actual worker fingerprint. Neither change proves that every
request-to-peak mismatch in #159 had that cause.

## Diagnostic fields

- `classification_code`: 0 unknown/direct/legacy, 1 recognized workload family,
  2 unsupported command/options, 3 compound shell/expansion, 4 script/interpreter,
  5 jq outside the supported language. `waiting_classification_N` aggregates
  current queue entries with that code.
- `estimate_source`: 1 explicit, 2 initial prior, 3 learned history.
  `estimate_complete_runs` reports the history used at request time.
- `reservation` events record allowance, measured footprint, completeness and
  source (1 lifetime peak, 2 adaptive observation window, 3 retained uncertainty).
- `blocked_REASON_ms` records elapsed intervals under the previous admission
  decision. Public reports sum currently waiting entries; these values are not
  per-job percentiles or proof that a blocker caused every millisecond of delay.
- `sample_duration_ms` measures collection time; validity remains two seconds
  from the existing sample timestamp. No stale data is authorized by this change.

## Issue disposition boundaries

#146, #148 and #150 have matching document/jq, Git branch inspection and remote
environment-argument forms covered by the classifier changes. #147 and #149
also contain deduplicated reports whose exact original command is not established.
#151–#156 and #160–#164 describe waiting/integration symptoms with little
or no exact reproduction. Scope isolation and additional diagnostics address
known contributing mechanisms, but these reports must not be closed solely from
an empty queue at report time or a passing classifier test. #157–#159 likewise
need post-update duration/estimate evidence to establish whether their aggregate
latency and burst-growth symptoms are resolved. Preserve the issues for that
verification rather than claim a blanket fix.
