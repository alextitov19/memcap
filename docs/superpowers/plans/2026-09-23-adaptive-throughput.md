# Adaptive Throughput Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` for sequential native
> execution. Repository instructions override per-task review/commit workflows:
> complete implementation, then perform one final whole-diff review. Do not spawn
> agents or commit partial work without the applicable authorization.

**Goal:** Recover useful parallel throughput across Claude, claude-p and Codex
without prolonged red pressure, blanket startup reservations or agent wait loops.

**Architecture:** Keep the Bash CLI, Python supervisors and per-user registry.
Add separate demand-estimation and admission-control modules, a shared pressure
sampler, and bounded local event history. Preserve strict policy compatibility;
introduce an owner-selected adaptive policy that distinguishes charged footprint
from immediate physical pressure.

**Tech Stack:** macOS; Bash 3.2; Python 3.9+ standard library; existing Bats,
unittest and ShellCheck; existing Homebrew and GitHub release workflow.

**Spec:** [Owner-approved design](../specs/2026-09-23-adaptive-throughput.md).

## Global constraints

- Follow AGENTS.md. Tests set both sandbox config/state roots and `MC_DRY_RUN=1`.
- Heavy validation runs through the live agent queue; isolation applies inside
  the admitted test workload. Do not sandbox outer admission to evade protection.
- Never run real watch/clean/uninstall or Docker apply/restart as a test.
- Preserve the owner's pause state. Installing a release never turns enforcement on.
- No new third-party runtime dependency or always-on network service.
- Keep explicit reservations, PID identity checks, protected ancestry and existing
  kill choke point. No SIGSTOP-based rotation or deletion of live leases.
- Do not edit Claude's ongoing hook patch or unrelated working-tree changes.
- No release, issue closure or performance claim based only on a plan or replay.

## Review focus

1. A job grows late or changes worker count after previous small runs: Task 3
   must retain peak uncertainty and avoid incorrectly reusing its old estimate.
2. Multiple supervisors sample identical free capacity: Tasks 2/4 must reserve
   shared startup credit atomically and prevent double counting.
3. Docker guest cache, host compression and shared pages disagree: Tasks 2/4
   must retain distinct metrics rather than fabricate physical availability.
4. A session disappears, PID is reused or sampling fails: Tasks 4/5 must retain
   uncertain claims and never authorize another session's termination.
5. Old and new supervisors coexist across a release: Tasks 1/7 must preserve
   legacy lease semantics and expose unsupported combinations explicitly.

## Task 1 — Establish the maintenance baseline and incident matrix

**Files:** Read `AGENTS.md`, `CONTRIBUTING.md`, `libexec/agent_diagnostics.py`,
`libexec/integrate.py`, `libexec/report.py`, `tests/test_agent_diagnostics.py`,
`tests/test_integrate.py`; create `docs/adaptive-throughput-validation.md` and
`tests/fixtures/adaptive-admission.json` during execution.

- [ ] Inspect Claude's completed hook patch and installed guidance. The corrected
  owner-maintenance exception reached Codex during planning on September 23.
  Verify source, installed runtime and received instructions agree; do not
  assume editing source alone updates an existing process.
- [ ] Finish coordination with Claude before creating the implementation branch
  or isolated worktree. Preserve the six modified hook-related files observed
  during planning. Record exact source and installed versions.
- [ ] Read bodies/comments of open issues, not titles alone. Initial matrix:
  #27/#35 polling; #28/#29/#30/#33/#38/#40 lightweight work;
  #31/#34/#39 locking; #32 remote control; #36 measurement;
  #37/#42 admission; #41 wait integration; #43 maintenance wording.
  Refresh the list at execution/release time. Some shared report identifiers
  aggregate distinct symptoms; do not assume one fix resolves every comment.
- [ ] Preserve the numeric September 23 before/after observations in the validation
  document. Include the missing throughput/pressure history limitations.
- [ ] Add deterministic replay fixtures. Start with this record, retaining KiB:

```json
{"name":"yellow_budget_stall","cap_kb":20971520,
 "tracked_kb":19330836,"available_kb":3774873,"pressure":2,
 "fault":false,"running":0,"waiting":15,"default_request_kb":2097152}
```

- [ ] Demonstrate the old behavior in a sandbox using `Scheduler.admissible`:
  2 GiB rejected; 1 GiB accepted on this unchanged sample. Record this as a
  characterization, not a failing product requirement or throughput benchmark.

**Deliverable:** Reproducible incident matrix and immutable baseline evidence.

## Task 2 — Add trustworthy shared observations and event history

**Files:** Modify `libexec/measure.sh`, `libexec/scheduler.sh`,
`libexec/scheduler.py`, `libexec/report_metrics.py`, `libexec/status.sh`;
create `libexec/scheduler_metrics.py`, `tests/test_scheduler_metrics.py`;
extend `tests/measure.bats`, `tests/scheduler.bats`, `tests/test_report_metrics.py`.

**Interfaces:** `scheduler_metrics.rate(previous: dict, current: dict,
counter: str) -> float | None`; `scheduler_metrics.append_event(directory: Path,
event: dict) -> None`. Samples have `schema`, `monotonic`, `boot_id`, `pressure`,
`footprint_kb`, `estimated_available_kb`, `page_bytes`, `swapins`, `swapouts`,
`measurement_fault` and policy revision. Rates use KiB/s.

- [ ] Write tests for 4/16 KiB pages, counter reset, reboot, zero/negative time,
  missing samples, a stale healthy sample followed by red, and missing Docker.
- [ ] Pin conversion with a concrete test:

```python
from scheduler_metrics import rate
previous = dict(monotonic=10, boot_id="a", page_bytes=16384, swapouts=100)
current = dict(monotonic=12, boot_id="a", page_bytes=16384, swapouts=228)
assert rate(previous, current, "swapouts") == 1024  # KiB/s
```

- [ ] Run the new suite before implementation and confirm it fails. Implement
  numeric-only parsing and rate calculation; reset history on counter/boot changes.
  Do not derive a rate from accumulated swap usage.
- [ ] Validate Darwin metric semantics against Apple documentation and independent
  tools. Keep VM footprint, guest/container usage and configured ceiling separate.
  Document overlapping page counters. Never adopt a fabricated `footprint-swap`
  formula or globally replace footprint with RSS.
- [ ] Move expensive sampling outside the registry lock. Use a separately elected
  sampler and validate sample age (maximum two seconds) plus policy revision at
  commit. Immediate pressure check remains required before a heavy launch.
- [ ] Record bounded numeric admission/start/completion/pressure events locally:
  keep at most 24 hours or 32 MiB, whichever is smaller. No full commands, source,
  environment values or public paths. Keep public metric expansion allowlisted.
- [ ] Run `python3 tests/test_scheduler_metrics.py`, the affected report tests,
  and `bats tests/measure.bats tests/scheduler.bats` inside the test sandbox.

**Deliverable:** A low-overhead, auditable view of useful demand and host behavior.

## Task 3 — Replace blanket estimates with classified and learned demand

**Files:** Modify `libexec/scheduler_policy.py`, `libexec/scheduler.py`;
create `libexec/workload_estimates.py`, `tests/test_workload_estimates.py`;
extend `tests/test_productivity.py`, `tests/test_scheduler.py` and Bats coverage.

**Interfaces:** `workload_estimates.estimate(prior_kb: int, peaks_kb: list,
complete_runs: int) -> int`; `workload_estimates.fingerprint(description: dict,
key: bytes) -> str`. The description includes project identity, executable/tool
version, workload, build flags, worker count and cold/warm classification.

- [ ] Reproduce reported safe wrapper forms without executing them. Test every
  pipeline stage; include negative controls where a file read is followed by a
  build, an expansion invokes a command, or a git invocation runs an external tool.
- [ ] Distinguish recognized lightweight/control work, small managed work, known
  heavyweight work and unknown managed work. Keep explicit `--memory` authoritative.
- [ ] Test the estimator with complete comparable peaks of 256 MiB: twenty samples
  plus a 25% margin and a 512 MiB floor should estimate 512 MiB, not 2 GiB.
  With one observed 4 GiB peak, retain at least 5 GiB rather than clipping at the
  old startup allowance. Cancellations/incomplete samples cannot lower estimates.
- [ ] Implement recent peak history (last 50 comparable complete runs), maximum
  until twenty samples, then a 95th percentile plus 25% margin. Constrain downward
  movement to 10% per new complete comparable run. Treat these as initial values
  to evaluate in Task 8, with no unsupported safety guarantee.
- [ ] Retain anticipated future growth separately from current measured demand.
  Include worker count and build context in estimate identity; invalidate history
  on meaningful tool/configuration changes. Never learn remote memory into the
  local CLI estimate or count VM growth once for every Docker client.
- [ ] Run focused new tests and existing productivity/worker tests. Break one
  classifier guard deliberately in a disposable copy and verify its test fails.

**Deliverable:** Known-small work can start without a blanket 2 GiB claim, and
later memory peaks improve subsequent decisions.

## Task 4 — Implement adaptive admission with explicit strict compatibility

**Files:** Create `libexec/admission.py`, `tests/test_admission.py`;
modify `libexec/scheduler.py`, `libexec/scheduler.sh`, `libexec/config.sh`,
`libexec/init.sh`, `libexec/enforce.sh`, `libexec/status.sh`;
extend `tests/config.bats`, `tests/shared_budget.bats`, `tests/test_scheduler.py`.

**Interfaces:** `admission.decide(policy: dict, sample: dict, controller: dict,
jobs: list, candidate: dict) -> dict`. Return `allow`, fixed `reason`,
`reservation_kb`, `startup_credit`, `sample_revision`, `policy_revision`.
This function is pure; only the supervisor commits state and launches work.

- [ ] Write strict-policy regression tests first: existing explicit cap/headroom
  behavior remains. Adaptive fixtures cover the 18.5/20 GiB stall and 22.54 GiB
  yellow observation without declaring every job admissible.
- [ ] Add `QUEUE_POLICY=strict|adaptive` with validated schema and visible status.
  Existing configs retain strict behavior until migration. Separate physical
  metrics from soft footprint planning and explain this in generated defaults.
- [ ] Implement one globally shared uncertain-start token and staged concurrency
  growth. Initial trial interval is two seconds; local maximum finite jobs is
  twelve. Require fresh observations between uncertain starts and reserve
  anticipated growth before launch. Known fits need not all wait for a probe.
- [ ] Test red/unknown/stale pressure blocks heavy starts, a pressure change
  between decision and launch blocks it, and simultaneous waiters cannot acquire
  the same startup credit. A late-growing job updates demand immediately.
- [ ] Use sustained paging changes together with pressure/growth and comparable
  responsiveness signals for backoff. Do not use unrelated remote request latency
  or total swap size as proof of host overload. Task 8 calibrates released values.
- [ ] Share the policy interpretation with watchdog budget-trigger decisions;
  preserve oversized-job, ownership and ancestry protections. Adaptive admission
  must not immediately trigger cleanup solely for crossing the soft target.
- [ ] Run admission and scheduler suites plus affected budget/enforcement tests;
  use real short-lived owned fixtures with synthetic pressure and dry-run kills.

**Deliverable:** Useful work can progress beyond a conservative footprint target
without switching off protection or launching every unknown request together.

## Task 5 — Remove fairness stalls, wait loops and stale ownership problems

**Files:** Modify `libexec/scheduler.py`, `libexec/agent_diagnostics.py`,
`libexec/feedback.sh`, `libexec/idle_gc.py`, `libexec/poll_cleanup.py` only where
reproductions require it; extend scheduler, queue-storm, idle-GC, polling and
agent-diagnostic tests.

- [ ] Add six-session fixtures with different job sizes and an aggressive
  submitter. Eligible sessions receive turns; a large aged request cannot
  indefinitely freeze fitting jobs when no finite job can drain.
- [ ] Maintain session fairness plus age and admitted memory-time accounting.
  Test finite work alongside long-lived resource leases. No running job is
  suspended or killed to create a turn.
- [ ] Test sixty-second agent wait returns, missing TaskOutput, explicit
  cancellation and stale task handles. Status/wait/report remain lock-free reads.
  Internal capacity wakeups do not require another model turn.
- [ ] Use a short-lived local notification channel or generation-change wakeup
  with a bounded fallback timer; do not place full sampling or reporting under
  the job lock. Keep native task output and exit status intact.
- [ ] Pin dead-owner/live-child retention, fully exited groups, PID reuse,
  permission failures and transient measurement failure. Reap only proven stale
  metadata; never fabricate capacity by deleting uncertain leases.
- [ ] Deduplicate only an identical tool-call identity, not arbitrary repeated
  commands. Report both original job ID and attachment status to the caller.
- [ ] Audit GC against the reproduced leak cases. Extend only existing lifecycle
  evidence handling if deficient; retain observe default and all active-work
  vetoes. A running Docker builder is never reclaimed just for low CPU.
- [ ] Run `tests/test_queue_storm.py`, `tests/test_poll_cleanup.py`,
  `tests/test_idle_gc.py`, `tests/test_agent_diagnostics.py` and scheduler tests.

**Deliverable:** Independent sessions keep progressing and waiting costs little
agent usage, without inventing ownership or cancelling needed work.

## Task 6 — Allocate worker pools for throughput

**Files:** Modify `libexec/scheduler_policy.py`, `libexec/admission.py`,
`libexec/scheduler.py`; extend `tests/test_scheduler.py` and admission tests.

- [ ] Test a single supported build, several competing builds, a smaller explicit
  user limit, `--` argument boundaries, nested managed work and unknown tools.
- [ ] Allocate a shared launch-time worker budget from logical CPUs and admitted
  workload memory estimates, preserving two logical CPUs for interactive work
  on this host as an initial trial. Workers are reservations, not a guarantee
  of CPU isolation. Do not overwrite explicit smaller settings.
- [ ] Condition memory estimates on worker count. Grant extra workers only when
  both CPU share and predicted demand permit it; preserve unsupported commands.
- [ ] Test that a lone build can exceed two workers while competing launches
  cannot each receive the entire shared pool. Running pools keep their launch
  settings; further tuning applies to later jobs.
- [ ] Run focused worker tests and compare single-build versus mixed-session
  completion times in Task 8.

**Deliverable:** Parallelism improves both across sessions and inside eligible
builds, with explicit accounting for the interaction.

## Task 7 — Make policy, compatibility and reports understandable

**Files:** Modify `libexec/report_metrics.py`, `libexec/report.py`,
`libexec/agent_diagnostics.py`, `libexec/integrate.py`, `libexec/status.sh`,
`README.md`, `CHANGELOG.md`; extend their existing tests.

- [ ] Surface policy/version, estimate source/confidence, measured demand,
  projected growth, pressure trend, paging rates and admission reason. Distinguish
  queued-but-not-started from running, completed, cancelled and unknown.
- [ ] Add numeric public counters for queue-delay distributions, startup-estimate
  misses, controller backoff, stale supervisors and lightweight delays. Preserve
  one-time consent, privacy allowlists, deduplication and unlimited report volume.
- [ ] Test sanitization rejects command strings, project paths, environment data,
  process identities and unexpected nested values. Reporting never takes a slot.
- [ ] Version queue records and controller policy. Exercise old/new supervisor
  combinations using fixtures. Do not migrate live reservations destructively;
  show which old supervisors must drain for full benefit.
- [ ] Verify new Claude, claude-p and Codex sessions receive correct guidance;
  verify stable installed paths and existing Codex hook trust without granting
  trust automatically. State limits of refreshing already-running supervisors.
- [ ] Document performance-mode semantics, strict mode, pause preservation,
  owned-task cancellation, measurement interpretation and maintenance exception.

**Deliverable:** Users and agents can interpret behavior without manual coaching.

## Task 8 — Prove performance and complete release validation

**Files:** Create `tests/benchmark_admission.py`, extend new behavioral tests and
`tests/scheduler.bats` to run them; update `docs/adaptive-throughput-validation.md`.

- [ ] Replay deterministic histories for five/eight sessions: safe inspection,
  remote-control waiting, small tests, cold/warm builds, browser bursts, Docker
  baseline changes and delayed peaks. Ensure the simulator models shared demand
  independently rather than echoing the implementation's own accounting.
- [ ] Run controlled owned mixed workloads under the live queue with isolated
  test state. Compare strict and adaptive policies across at least three
  comparable runs, alternating order and recording cache/background conditions.
  Do not stop other sessions to manufacture a favorable result.
- [ ] Record completion throughput, total completion time, p50/p95 queue delay,
  worst per-session wait, peak concurrency, pressure duration, paging rates,
  sampler CPU and agent wait-turn counts. Report the 50% target honestly.
- [ ] Release gates: recognized light work acquires no heavy reservation; no
  duplicate launch; red/fault never admits heavy work; all eligible finite test
  sessions finish; queue observation creates zero drain jobs; strict compatibility
  passes. A stable, fitting workload must not remain at zero running indefinitely.
- [ ] Establish performance thresholds from Task 1's reproducible workload.
  Reject a change that increases simultaneous processes but worsens completion
  time or responsiveness. Distinguish CPU-bound saturation from memory stalls.
- [ ] Run all required checks through one admitted validation workload. Within it,
  use fresh `MEMCAP_CONFIG_HOME`/`MEMCAP_STATE_HOME` and `MC_DRY_RUN=1`:

```bash
shellcheck bin/memcap libexec/*.sh tests/*.bats tests/*.bash
for f in bin/memcap libexec/*.sh tests/*.bash; do
  /bin/bash -n "$f" || exit 1
done
bats tests/
env HOME="$(mktemp -d)" bats tests/
```

- [ ] Report actual counts and failures; add new tests to the Bats entrypoint.
  Perform one final whole-diff review after the full implementation. Fix release
  blockers within that pass; do not start per-task or repeated review cycles.
- [ ] Require exact-head CI. Local green is not evidence of CI green.

**Deliverable:** Recorded correctness and performance evidence, including any
remaining limitations. No assertion of never reaching red under arbitrary work.

## Task 9 — Release, install and close verified issues

**Files:** Source release metadata/docs; `Formula/memcap.rb` in the Homebrew tap.

- [ ] Reconcile Claude's maintenance release and choose the next unused version;
  target 0.16.0 for the adaptive feature if still available. Do not overwrite tags.
- [ ] Create a focused PR with the final behavior, evidence and migration details.
  Respect protected main and required checks; merge the exact reviewed/tested tree.
- [ ] Publish the source release; calculate the actual archive SHA256; update and
  release the tap against that immutable artifact.
- [ ] Upgrade locally, compare installed runtime files to the release, run the
  sandboxed Homebrew test, and verify stable hook paths for all three profiles.
- [ ] Stage the requested adaptive local policy through the supported installation
  workflow as an explicit owner-authorized administrative operation, separate
  from tests. Preserve pause state; do not run Docker apply or restart workloads.
  Record any setting the repository's installation rules require the owner to
  apply rather than silently modifying live config/state from development code.
- [ ] When the owner resumes enforcement, verify real admission/pressure metrics.
  Do not report live adaptive behavior as verified while enforcement remains off.
- [ ] Link evidence and release version to each resolved issue. Close only reports
  whose distinct observations are addressed; explain and retain unresolved ones.
- [ ] Deliver source/tap release links, installed version, test counts, measured
  performance change and precise remaining restart/drain requirements.

## Execution order and scope

Tasks 1–3 establish evidence and remove inflated demand estimates. Tasks 4–6
implement admission, fairness and worker throughput. Tasks 7–9 finish integration,
verification and delivery. Keep implementation cohesive; do not publish partial
claims that the adaptive controller exists after only changing default numbers.

This is a planning deliverable. No scheduler behavior, policy setting or process
lifecycle was changed by writing it. Implementation remains the next work item.
