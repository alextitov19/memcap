# v0.31.0 implementation plan

Goal: make cancellations, workload decisions, learning and release comparisons
actionable without changing the owner's memory or concurrency policy.

The October 10 baseline is saved privately in release-history/v030-assessment-20261010.
All production tests use synthetic processes and isolated config/state. Heavy
validation runs through the installed live queue. One final whole-diff review.

- [x] Investigate #403–407 against local evidence; reproduce supported cases.
  The historical #403 signal sender remains unknown. Preserve private commands locally;
  publish only sanitized reproductions and conclusions. Separate deliberate waits
  and compound browser work from accidental lightweight admission.
- [x] `agent_diagnostics.py`, `scheduler_policy.py`: explain native explicit waits
  and the positive heavy decision, with paired native/heavy regression tests.
- [x] `scheduler.py`, `scheduler_metrics.py`, `analytics_events.py`: record the
  supervisor's received cancellation signal, guarded cancellation attempts and
  result; retain these independently from completion. Never infer the sender.
- [x] `feedback.sh`: give single-process compilers actionable recovery guidance;
  preserve kill eligibility, identity checks and the owner's 4 GiB limit.
- [x] `compiler_commands.py`, `compiler_profiles.py`: support bounded direct local
  Node/TypeScript compiler wrappers for learning. Preserve source/config/runtime
  identities and decline arbitrary Node preloads or execution wrappers.
- [x] `analytics_reports.py`: explain long waits by stored blocker durations;
  separate unobserved short jobs, probe faults, churn and complete learning.
- [x] `analytics_store.py`, `analytics_releases.py`, `incident_evidence.py`: retain
  compact diagnostic/job checkpoints longer than raw telemetry, preserve relevant
  incident evidence, and include matched-workload comparisons in release reports.
  Keep bounded disk use and unknown coverage explicit.
- [x] Run failing negative controls, focused tests, shellcheck, per-file bash
  parsing, all Bats tests and isolated-HOME/no-Docker Bats. Review the full diff
  once. Normal and isolated suites: 658/658 each. Bash parse: 27 files;
  individual test shellcheck: 32 files; grouped shellcheck passed.
- [ ] Release through green CI, install locally and save the rollout receipt.
  The immutable before-v031-20261010 baseline is saved outside rolling retention.

Review focus: signal provenance is observation rather than kill authorization;
incomplete peaks never lower estimates; wrapper inspection never executes code;
raw commands never enter analytics/public reports; retention cannot stall admission.

No policy allowance above the configured oversized limit is added without a
separately specified owner policy. This release diagnoses legitimate compilers
and discourages identical retries instead of silently increasing that limit.
