# Throughput learning v0.24 implementation plan

**Goal:** Reduce unnecessary managed-job waiting through reusable measured compiler
estimates and timely complete process observations, preserve safety and release
v0.24.0 with evidence that can distinguish mechanism improvements from host changes.

**Architecture:** Keep native/lightweight classification and heavy admission separate.
Add a compiler-only statistical profile alongside exact fingerprints; do not use it
to authorize native execution. Separate frequent owned-job physical-footprint
observation from expensive shared host admission samples. Retain atomic admission,
fresh pressure, cancellation ownership and existing headroom policy.

**Tech stack:** Existing Python standard library and Bash 3.2; macOS libproc;
unittest through Bats. No new service or dependencies.

**Spec/evidence:** Local private release-history/2026-10-03-v022-v023-causes.md
and its numeric archives; public motivation is long waits, poor estimate reuse,
incomplete observations and sample contention. Private commands stay local.

**Execution:** Inline implementation using executing-plans and test-driven-development.
The user explicitly requested plan execution and release; no additional design or
publication approval is needed. One whole-diff review after all implementation,
not per-task reviews. Progress and deviations are recorded below.

## Global constraints

- Live expensive work runs through memcap; no policy bypass, duplicate jobs or lease
  deletion. All behavioral fixtures use temporary state/config and dry-run enforcement.
- No changes to signal eligibility, protected agents, orphan floors, fixed request
  floors, strict policy, pressure limits, physical headroom or two-second freshness.
- Observations authorize neither signals nor cancellation. RSS is not footprint.
- Incomplete observations can raise predictions; they never lower them. Native
  unknown-demand calls remain native. Estimated memory is not a hard memory cap.
- Old supervisors keep their loaded code. New optional registry fields must be
  compatible with old supervisors; new profile history uses a separate namespace.
- Preserve installed config, pause state, other projects' containers and profile
  permissions. No Docker restart or kernel intervention.

## Review focus

1. PID reuse/foreign ownership/process churn: unknown or incomplete observation must
   not become a low-memory certificate (Task 2 fixtures).
2. A small compiler becomes large after edits: only scoped stable compiler contexts
   may reuse predictions, with source envelope bounds, margins and immediate upward
   evidence (Task 1 fixtures).
3. Old and new supervisors share registry state: neither may overwrite history or
   lower a fixed/orphaned allowance (Tasks 1–2 compatibility fixtures).
4. Cached samples arrive out of order or expire under contention: no extension of
   freshness or controller rollback (Task 3 negative controls).
5. Missing analytics/failed recorder: command execution and results remain unaffected,
   unknown measurements stay unknown (Task 4 fixtures).

## Task 1: Reusable compiler predictions

Files: new `libexec/compiler_profiles.py`, `tests/test_compiler_profiles.py`;
modify `libexec/scheduler.py`, `libexec/workload_estimates.py`, Bats entrypoint.

Interfaces: `compiler_profile(argv, cwd, workers, env) -> dict|None` returns a
private context description and bounded source envelope; `predict(history, profile,
prior, now) -> (estimate, reason)`; `record_profile(history, profile, peak, complete,
now)` updates a separate bounded compiler-history namespace.

- [ ] RED: fixtures for repeated small direct Go builds/tsc across source edits;
  incomplete runs, large new peaks, changed dependencies/config/tool/workers/env,
  oversized source growth, symlinks and wrappers must not teach unsafe reductions.
- [ ] Implement compiler-only reuse: exact command/target, cwd, executable identity,
  dependencies/configuration and memory-affecting environment remain in the key.
  Source edits use a bounded envelope with complete enumeration, max 4096 files,
  8 MiB and bounded scan time. Require three complete runs, age <=7 days, maximum
  observed peak with 50% margin and minimum 512 MiB. Reject >20% source growth or
  file-count growth against evidence. Uncertain/unsupported scopes retain prior.
- [ ] Keep exact profiles as upward safeguards; partial growth raises both relevant
  profiles immediately. No fixed-floor changes, no native routing from these estimates.
- [ ] Verify a fitting learned compiler can be admitted at 1.2 GiB available while
  the unchanged 1 GiB default cannot; red and insufficient true headroom still deny.
- [ ] GREEN and negative controls, with test output saved locally.

## Task 2: Timely owned-job observations

Files: new `libexec/job_observation.py`, `tests/test_job_observation.py`;
modify `libexec/scheduler.py` and tests.

Interfaces: identity-checked `sample_job(job, table_reader, usage_reader) -> dict`;
owned-job learning uses this independent sample without advancing host admission
freshness or feeding a synthetic host pressure sample into the controller.

- [ ] RED: short job obtains two complete samples; fresh PID reuse, foreign UID,
  missing live member, escaped child and unavailable probe prevent complete learning.
- [ ] Use macOS libproc physical footprint (existing native observer API), paired
  identity checks and fresh process membership. Sample promptly during startup,
  separate from the shared host probe. Bound polling and keep cancellation responsive.
- [ ] Avoid permanently poisoning new-protocol learning solely because a full-host
  snapshot predates a newly observed child; the dedicated observation must itself
  establish completeness. Legacy jobs retain legacy conservative behavior.
- [ ] Preserve peak growth immediately and all effective reservation floors; no
  reservation release from missing process data. Endpoints and exit status unchanged.
- [ ] GREEN and negative controls, including mixed-version registry observations.

## Task 3: Shared sample contention and freshness

Files: `libexec/scheduler_metrics.py`, `libexec/scheduler.py`, `libexec/admission.py`,
new tests in the throughput release suite.

- [ ] RED: equivalent measurement contexts share a sample despite differing session/
  lease metadata; actual measurement configuration differences still invalidate it.
- [ ] Give shared host samples an explicit compatible measurement signature, retaining
  config bytes, probe-affecting environment and source identity. Do not include
  irrelevant per-command lifecycle metadata. Preserve legacy compatibility safely.
- [ ] Record fixed numeric busy/expired/cache-mismatch/probe-duration diagnostics;
  preserve the two-second admission limit and final live pressure check.
- [ ] Measure owned process probing and full host probing under normal live policy
  with bounded read-only probes; save timings and scope limitations.
- [ ] GREEN and freshness/controller negative controls.

## Task 4: Explain whether the mechanisms helped

Files: analytics event allowlist, scheduler events, analytics reports/releases,
`docs/analytics.md`, release tests and docs.

- [ ] Record default/exact/compiler-profile source, history count, prior versus
  requested allowance, fingerprint miss reason, dedicated observation completeness
  and sampling reason. Only numeric fields and keyed identifiers, no raw commands.
- [ ] Add report sections for actual admissions below prior, profile reuse coverage,
  missing-sample reasons and busy/expired sample counts/timing. Missing historical
  fields remain unknown. Compare matching workload/worker cohorts when available.
- [ ] Add deterministic paired replay: identical workloads and host headroom before/
  after, cold/edited/warm/large-growth/missing-evidence cases, with safety negatives.
  No claim that replay proves whole-day causal improvement.
- [ ] Document wired-memory/headroom context and preserve snapshots by version.
  Do not blame a process or promise a kernel fix without ownership evidence.

## Task 5: Final validation, review, release and install

- [x] Reproduce current v0.23 baseline in immutable local snapshot before installation.
- [ ] Run ShellCheck for bin/libexec/tests; individually parse every Bash file;
  `bats tests/`; isolated temporary HOME + no-Docker `bats tests/`. All through live
  admission where expensive. Record actual counts and failures.
- [ ] One final whole-diff review; fix clear blockers, run relevant checks and full
  suites after code changes that warrant them. No repeated reviewer loop.
- [ ] Bump version and changelog for v0.24.0, commit release work, open PR, await CI,
  merge and publish GitHub release. Triage related issues honestly; generic reports
  without a reproduced fix must not be mass-closed as resolved.
- [ ] Update Homebrew formula with verified archive checksum; install through live
  memcap, verify installed source digest/version, collector health and integrations.
  Preserve policy and pause state. Old queued work is never duplicated or killed.
- [ ] Save install provenance, post-install snapshot and comparison manifest. Verify
  new observations expose added fields. Report limitations and the next-day metrics
  needed to evaluate real-world progress.

## Progress and decisions

- Baseline branch: `feat/v024-throughput-learning` from `f1fe5e1`; clean start.
- Prior status investigation preserved immutable v0.22/v0.23 evidence locally.
- Read-only feasibility probe: macOS libproc queried 1073 PIDs in 3ms, 845 available
  and 228 unavailable. Thus use it for identity-checked owned jobs, not as a blind
  replacement for whole-machine measurement: inaccessible processes remain unknown.
- Ruling: existing repository Python/Bats conventions take precedence over adding
  a new framework/toolchain from generic backend recommendations.
- First RED check: tool session 46547 / job b59add70 admitted after a prolonged
  physical-headroom wait; exited 1 with seven expected missing-module errors.
  Compiler/job observer drafts were held as `.pending` modules until this check,
  then activated. Other integration work was drafted during the wait; falsification
  controls are still required before declaring those tests protective.
- Pending GREEN checks: session 13549 / job ac689380 (compiler profiles), session
  99153 / job 1766d668 (learning analytics/integration). Do not duplicate these jobs.
- Queue incident reported once (deduplicated #323); additional required delayed-work
  and polling reports deduplicated to #324 and #320. All new snapshots stayed local.
- First GREEN checks completed: seven compiler-profile tests and six learning/
  integration tests passed. Broader throughput+analytics run: 11/13 Bats passed;
  failures were a retained legacy diagnostic and a timed native fixture ending
  during observation. Missing host-sample diagnostics were restored without
  letting stale host membership poison the dedicated stream.
- The native smoke test passed on an unchanged rerun (five integration tests),
  establishing timing sensitivity. Ruling: its child now exits on an observation
  gate after three complete samples, with a five-second self-expiry; separate
  churn tests still require incomplete evidence for missed live processes. No
  production completeness condition was relaxed to make the fixture pass.
- ShellCheck passed for bin/libexec/tests; all 27 Bash entrypoints parsed separately.
- Immutable baseline saved: local release-history/before-v0.24.0 (43,462 sanitized
  events, one recorded release cohort). Installed policy and runner remain v0.23.
- Final fresh whole-diff review completed once, read-only. Three release blockers:
  process-probe exceptions could escape active supervision; compiler subdirectory
  contexts omitted parent-module inputs; terminal observation gaps could certify
  a low peak. Regression fixtures added before fixes. Full release remains gated.
- Ruling: compiler reuse will require a supported project-root input scope, rather
  than infer external module inputs. Unsupported scopes retain ordinary estimates.
- Targeted rerun remains the original queued job 064d908e / tool session 1798;
  no duplicate or replacement was submitted. Full validation script prepared at
  /tmp/memcap-v024-validation.sh, with private normal/isolated logs and probe timings.
- Ruling: apply the final review fix pass while the unchanged validation job waits;
  deliberate negative controls restore all three faulty behaviors in isolated
  fixtures, so the same run verifies falsification plus corrected behavior. No
  claim of a separate pre-fix execution is made for these review regressions.
- Review fixes: optional observation catches probe-adapter exceptions, terminal
  learning requires a sample within five seconds, and compiler reuse declines
  non-module-root Go scopes. Source files are size-checked before reading so the
  bounded scanner cannot allocate an oversized file before rejecting its envelope.
- Full release validation submitted once: job 0739e1b5 / tool session 52972.
  Includes grouped/individual ShellCheck, every Bash file, normal Bats, isolated
  HOME with no Docker Bats, then the bounded owned-versus-host probe benchmark.
  This job remains pending under live admission. The targeted job was superseded
  by the full suite and cancelled through its native tool session; final exit 130
  confirmed no test execution. No supervisor was killed or registry state edited.
- The same final fix pass also rejects inherited TypeScript configs and unreadable
  source subtrees, bounds reads before allocation, and excludes legacy zero probe
  placeholders from the new owned-probe cost distribution. Added focused fixtures;
  full validation has not yet started, so it will cover the resulting final tree.
- Ruling: publish the candidate branch for ordinary repository CI while local
  validation remains queued. This is a provisional implementation commit, not a
  completion claim; no PR merge or release until required local and CI checks pass.
  The local workload remains subject to unchanged live admission. Remote CI uses
  the repository's existing macOS sandboxed test workflow and adds no Mac workload.
