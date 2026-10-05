# v0.27 productivity release: design and execution plan

## Goal and evidence

Reduce avoidable waiting without admitting an unmeasured heavy workload into
insufficient memory. Finish the implementation, validation, GitHub release,
Homebrew update and local installation; preserve versioned comparison evidence.

The October 4 18:05 PDT private baseline contains 33 v0.25 completions and 26
v0.26 completions. Median waits increased from 13.1 to 20.3 minutes; p95 from
40.6 to 95.2. Neither cohort used a smaller learned admission. Different workloads
and host windows prevent release-causal conclusions. The existing public reports
are observations, not sufficient reproductions by themselves.

At follow-up, the reported hover check is still pending: it starts a dev server
and browser verification, requests about 2.065 GiB from upward exact evidence,
and requires another 0.5 GiB of headroom. Recent available memory is 0.96–1.2 GiB.
Two other jobs wait with no managed finite work running. Container inspection
confirms the shared builder has already stopped; only a small database remains.
Neither queue rotation nor deleting reservations can reclaim the missing memory.
Private commands and credentials must never enter this document or GitHub.

## Chosen design

Keep the two-outcome memory policy and existing supervisor/ownership model.
Correct specific inspection and observation boundaries, make sampler handoff
bounded and useful, and expose whether improvements affect actual admissions.
Replacing the whole daemon would discard tested process-safety invariants and
would not resolve physical headroom. Simply reducing all estimates would hide
uncertainty and risk recreating red pressure. Neither is this release's approach.

Learning needs a precise distinction between an incomplete measurement and a
new child observed after that measurement. The latter creates a pending evidence
obligation. It must prevent reservation reduction and terminal certification until
that same identity is measured. An identity disappearing unmeasured permanently
invalidates the run. Missing usage, probe faults, PID reuse, real sampling gaps
and incomplete samples continue to prohibit downward learning.

Sampler clients may briefly wait for an already-running probe outside the
registry lock, reusing only a matching sample younger than two seconds. Bounded
waiting must not launch duplicate probes, stall cancellation indefinitely, or
turn stale measurements into admission evidence. Record actual handoff outcomes
and durations so contention can be separated from capacity refusal.

Compiler profile resolution should recognize literal invocation forms used by
agents without executing shell startup files or target scripts. Bound expansion
to known system shells and literal single-compiler commands; preserve injected
environment, external input, process-tree and worker guards. Unsupported scopes
remain unsupported rather than being labeled safe.

## Global constraints

- Heavy tests/builds use the live memcap queue with 86400-second admission patience.
- All memcap behavioral fixtures use temporary config/state and dry-run enforcement.
- Never change live memory budgets, pause state, reservations or process ownership.
- Preserve explicit memory floors, fresh pressure checks, 24-hour wait semantics,
  exit status, single execution and mixed-version supervisor compatibility.
- Do not restart Docker, adopt/stop another project's resources, or reset devices.
- Do not certify low peaks from incomplete observations or claim wired ownership
  from aggregate process/VM totals.
- One whole-diff review after implementation; no per-task review loop.
- Existing snapshots remain immutable. Raw command evidence stays private/local.

## Execution checklist

### 1. Preserve and establish reproductions

- [x] Save a fresh pre-release snapshot, config/build identity and private trace
  evidence before retention removes it, using a new private release directory.
- [ ] Triage open issue groups, especially current lightweight, sampling, collector
  and polling reports. Correlate route evidence with actual queued operations;
  do not treat a separate pending build as proof that a native read was queued.
- [ ] Record synthetic sanitized reproductions for confirmed classifier/hook bugs.
- [x] Record the hover check's request provenance and actual blocker without
  publishing its command, changing it, or duplicating another session's task.
- [x] Investigate wired allocation with bounded read-only native evidence. State
  explicitly if privileged instrumentation or an external OS fix is required.

### 2. Lightweight execution and honest route evidence

Files: `libexec/demand_policy.py`, `libexec/scheduler_policy.py`,
`libexec/command_stages.py`, focused demand/hook/staging tests.

- [ ] Write failing tests for confirmed help/inspection and wrapper cases, pairing
  each native example with real heavy work using the same form.
- [ ] Correct classification or hook rewriting at the responsible boundary;
  native help must retain the host's execution mode and create no reservation.
- [ ] Keep literal data/heredoc contents distinct from executed code; preserve
  heavy interpreter bodies and mixed commands with actual heavy stages.
- [ ] Verify stdout, stderr, exit status and one execution with temporary fixtures.

### 3. Useful conservative learning

Files: `libexec/job_observation.py`, `libexec/scheduler.py`,
`libexec/compiler_commands.py`, `libexec/compiler_profiles.py`,
`tests/test_job_observation.py`, `tests/test_learning_integration.py`,
`tests/test_compiler_commands.py`.

- [ ] Reproduce post-measurement child birth falsely poisoning complete history.
- [ ] Implement identity-bound pending observations; clear only after complete
  paired measurement. Persist terminal uncertainty and missing/reused-child failures.
- [ ] Preserve current running allowance while pending; no new-child gap may
  shrink reservations. Do not modify host pressure controller from owned samples.
- [ ] Resolve common literal compiler wrappers with an unchanged bounded scope.
- [ ] Demonstrate three complete runs feeding a reused profile and fitting a
  constrained-host replay where the old startup prior would remain queued.
- [ ] Negative controls: missing/foreign/reused/escaped child, late terminal gap,
  partial high peak, startup injection, changed workers/config and fixed floor.

### 4. Sampler progress without stale admission

Files: `libexec/scheduler_metrics.py`, `libexec/scheduler.py`,
`tests/test_sampling_context.py`, `tests/test_scheduler_metrics.py`.

- [ ] Reproduce a contender returning busy just before the elected probe publishes.
- [ ] Add a bounded publication handoff outside registry locks; use the existing
  freshness/configuration identity checks on every return.
- [ ] Ensure long/faulty/incompatible probes terminate the handoff as unavailable.
- [ ] Verify concurrent callers generate one probe; cancellation and registry
  reads remain available; old cache cannot roll the paging controller backward.
- [ ] Measure useful handoffs, wait duration, expired/busy outcomes and subsequent
  capacity refusal separately, rather than claiming blocked intervals as time saved.

### 5. Analytics and stalled-session guidance

Files: analytics event/report/release modules, collector if a defect is reproduced,
`libexec/throughput.py`, managed guidance source and corresponding tests.

- [ ] Add normalized sampler/learning outcomes and an explicit insufficient-data
  comparison verdict. Include pending-work burden alongside completed percentiles.
- [ ] Keep old release rows comparable: absent instrumentation remains unknown.
- [ ] Distinguish pending evidence from permanent observation failure in analytics.
- [ ] Reproduce collector heartbeat delays where possible; fix established causes,
  otherwise retain the unresolved incident and diagnostics rather than claim success.
- [ ] Make sustained zero-running/headroom stalls actionable: show required versus
  available memory and that waiting alone may not change capacity. Do not recommend
  repeated status loops, Docker restarts or stopping unowned resources.
- [ ] Clarify that a successful wait observes status and is not workload success;
  native inspection can continue while another heavy job is pending.

### 5a. Evidence-driven scope addition: abandoned simulator cleanup

Follow-up inspection found no booted devices, no launchd_sim, and a device stuck
in Shutting Down with reparented runtime leaves. GC_MODE is already owner-enabled.
The global all-devices-are-Shutdown gate currently prevents these leaves from
becoming candidates. This is a concrete capacity-retention defect, not grounds
to terminate a booted device or bypass admission.

Files: `libexec/idle_gc.py`, synthetic GC/release regression tests, safety guidance.

- [ ] Treat Shutting Down as eligible for observation only when no live mobile
  tooling/device owner exists, retaining unknown/Booting/Booted vetoes.
- [ ] Retain the complete 600-second configured idle grace, fresh PID identity,
  same UID, reparented runtime leaf, no-child and network checks; every signal
  still passes the existing freshly authorized mc_kill_pids scope.
- [ ] Test reactivation during grace and before authorization, PID reuse,
  foreign UID, connected sockets, process children, pause and dry-run protection.
- [ ] Document the precise widening in the final review and PR. Validate with
  synthetic fixtures before installing; never use a production cleanup as a test.

### 6. Verify complete implementation and review once

- [ ] Run new regression tests red before fixes and green afterward; retain results.
- [ ] Run meaningful negative controls against the safety boundaries above.
- [ ] Run ShellCheck over the full required set, parse each Bash file individually,
  `bats tests/`, and isolated-HOME/no-Docker `bats tests/`. Record actual counts.
- [ ] Replay the same workload fixtures against v0.26 and the candidate, recording
  admission decisions and observation completeness with identical host inputs.
- [ ] Perform one whole-diff review using house standards; address clear blockers.
- [ ] Update version/changelog/docs around demonstrated outcomes and limitations.

### 5b. Reduce avoidable nested script launches

Read-only host investigation found a large growing data.kalloc.1024 allocation.
Published first-party research describes a reproducible nested-shebang kernel
leak on macOS 26.3/26.5.x, with persistent allocations until reboot:
https://photon.codes/blog/we-found-a-kernel-memory-leak-in-macos-that-any-shell-script-can-trigger
This is a candidate mechanism, not attribution of this host's allocation to
memcap, Docker or an agent. Do not run an unbounded leak reproducer on this Mac.

- [x] Audit frequent memcap-owned script dispatches, especially admission sampling.
- [x] Launch the known Bash dispatcher with explicit `/bin/bash` internally,
  preserving arguments, config loading, parent identity and exit status.
- [ ] Prove the internal sampler still reads its configured bridge and faults
  on failed/malformed results; do not generalize interpreter rewriting to user commands.
- [ ] Record kernel-zone growth and coverage in local analytics comparisons.
  Treat allocation counters as non-resident attribution evidence, never a reclaim promise.
- [x] State that this mitigation cannot release existing kernel allocations or
  establish which external workload caused them; no automatic reboot or OS change.

### 7. Release, install and audit

- [ ] Commit the complete release, open PR and obtain green required CI; merge.
- [ ] Publish the next version/tag and verify release CI/artifact checksums.
- [ ] Update Homebrew formula, install locally, preserve old runner paths for
  already-running supervisors, and verify installed source/build identity.
- [ ] Refresh integrations only where required by changed guidance/contracts;
  preserve unrelated settings and existing trust; report reload requirements honestly.
- [ ] Verify recorder heartbeat, status/queue, config hash and current enforcement.
- [ ] Save post-install evidence under the actual release build; compare replay
  results and available production cohorts without declaring unmatched gains causal.
- [ ] Update resolved issues only with demonstrated fixes; document unresolved OS
  attribution or unreproduced reports explicitly, with no raw local evidence upload.
- [ ] Audit every checkbox against artifacts and authoritative state before marking
  the release goal complete. Pending validation or installation means incomplete.

## Review focus

1. Child born after a sample but exiting before the next sample: never certify low usage.
2. Foreign/PID-reused identities or nested jobs: no attribution or signal-scope widening.
3. Concurrent old/new samplers, changed config and expired cache: no stale admission.
4. Help/data containing heavy command names: no accidental queue; actual work still queues.
5. Empty or unmatched analytics windows: unknown, never zero improvement or no regression.

## Release success criteria

Confirmed lightweight reproductions run natively without reservations. Post-sample
births that are subsequently fully measured no longer destroy learning, while
unmeasured exits cannot lower requests. Supported repeated compiler workloads
demonstrate actual smaller admission in a fixed-host replay. Sampler handoff
reduces avoidable retry latency without changing freshness or pressure policy.
The full suite, isolated suite and CI pass; the published release is installed and
recording versioned evidence. Real heavy work that cannot fit remains protected,
with an honest explanation rather than a promise that another minute will fix it.
