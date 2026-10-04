# Learning recovery release implementation plan

**Goal:** Fix v0.24's learning and admission regressions, extend useful estimate reuse to real agent invocations, verify the complete product, publish the next release, and install it locally with preserved comparison evidence.

**Architecture:** Keep native versus managed routing and live pressure policy intact. Repair estimate composition and owned-process observation at their existing boundaries. Normalize only bounded, statically understood compiler launch forms; preserve the executed command verbatim. Add numeric diagnostics for rejection and observation failures so the next comparison explains mechanisms, not just outcomes.

**Tech stack:** Existing Python 3.9+, macOS process probes, Bash 3.2, Bats/unittest, Homebrew and GitHub Actions. No new runtime dependencies.

**Evidence:** Local `release-history/2026-10-03-evening/` reports: 50 completed v0.24 jobs, zero below-prior admissions, zero compiler-profile uses, 49 incomplete observations. Median completed-job wait 14.27 minutes versus 8.33 for different v0.23 jobs. These are observational cohorts, not a controlled speed comparison.

**Execution:** Follow this plan in this session. User explicitly authorized planning and full execution through release. Perform one whole-diff review after implementation; no per-task review gates or unsolicited permission stops.

## Global constraints

- Heavy tests, builds and installation run through live `memcap run --wait 86400`; retain and poll their original tool sessions until terminal.
- Tests use temporary config/state/profiles and dry-run enforcement; no real process termination, Docker mutation, or live queue manipulation.
- Preserve fresh pressure/headroom admission, fixed reservations, strict/orphan floors, observed upward growth, identity and foreign-UID exclusions, nested leases, cancellation and signal scopes.
- Unknown measurements remain unknown. Do not solve poor learning by certifying missing peaks or weakening memory policy.
- Raw commands remain private locally. Analytics and GitHub contain only allowed numeric/fixed-vocabulary diagnostics and synthetic examples.
- Preserve baseline archives, live configuration bytes and old installed runner files needed by active supervisors. No reset or cancellation of other work.

## 1. Freeze evidence and reproduce causes

Files: local release-history/before-v0.25.0; tests/test_learning_integration.py; tests/test_job_observation.py; optional bounded diagnostic script under tests/.

- [x] Save an immutable current analytics snapshot, current version/build, config digest and recorder health outside live state. Preserve the evening comparison.
- [x] Inspect recent GitHub reports #331–333 and the currently available local command structures. Check trace expiration before relying on absence of commands.
- [x] Add a regression test: a valid exact learned 512 MiB estimate with a 1 GiB prior remains 512 MiB when compiler reuse is unsupported, cold or insufficient. Pair it with larger exact and partial compiler-growth floors.
- [x] Reproduce missing observations with synthetic process lifecycle transitions and a bounded queued real-process probe. Distinguish stale registration anchors, after-sample refresh races, missing usage, PID reuse, births/exits and sampling gaps. Record the failing cases before changing behavior.

## 2. Restore estimate composition

Files: libexec/scheduler.py, libexec/compiler_profiles.py, tests/test_learning_integration.py, tests/test_learning_negative.py.

- [x] Distinguish an absent compiler prediction from an observed compiler floor. Unsupported/cold profiles must not replace valid exact learning with a default prior.
- [x] Keep an independently observed upward floor, including incomplete high peaks. Preserve the separate mixed-version prediction namespace when actual compiler reuse lowers an estimate.
- [x] Validate unsupported, cold, insufficient, expired, source-growth, large exact and partial-growth cases. Add a deliberate negative control that restores the faulty default floor and must fail.

## 3. Repair owned observation based on reproduction

Files: libexec/job_observation.py, libexec/scheduler.py, tests/test_job_observation.py, tests/test_learning_integration.py, tests/benchmark_owned_observation.py.

- [x] Fix only demonstrated identity/lifecycle accounting defects. A command completing between poll and registry refresh must not manufacture evidence of a missing live process; an actually unobserved process must remain incomplete.
- [x] Carry the measurement's own before/after identities and timestamp through registry refresh. Confirm observation identities never authorize signals or cancellation.
- [x] Retain terminal freshness <=5 seconds, at least two complete samples, foreign-UID/PID-reuse exclusions, escaped-child attribution and nested-job deduplication.
- [x] Add reason counters for absent anchors, member changes, unavailable usage, identity changes, refresh mismatch and excessive gaps. A failed probe must retain supervision and reservation.
- [x] Run repeated bounded real-process observations with both stable and churning children. Report complete-learning yield and observer cost; incomplete churn must not masquerade as complete evidence. Keep negative controls for every newly fixed case.

## 4. Make reusable contexts reach agent commands

Files: libexec/compiler_profiles.py or a focused compiler-command resolver; tests/test_compiler_profiles.py; tests/test_learning_integration.py.

- [x] Inspect private command shapes and existing static parser capabilities; record only sanitized shape counts in public artifacts.
- [x] Resolve bounded literal shell wrappers, directory/environment prefixes and local package-script compiler invocations without executing scripts. Include wrapper/script bytes, relevant environment, tool/worker identity and compiler inputs in the context.
- [x] Keep tests/arbitrary scripts, shell expansion, injected startup code, external or uncertain inputs unsupported unless their memory scope is actually bounded. Never treat a compiler nested inside an arbitrary script as the whole workload.
- [x] Add ordinary/heavy wrapper pairs, changed package scripts/config/dependencies, worker changes, symlinks, external paths and startup-injection tests. Preserve original shell text, exit status and single execution.
- [x] Produce separate fixed reason codes for unsupported command forms versus unsafe/unbounded input scope and exhausted inspection budget. Confirm wrappers reach a context in an end-to-end scheduler test and three complete runs permit reuse after a bounded source edit.

## 5. Diagnose and reduce avoidable sampling contention

Files: libexec/scheduler_metrics.py, libexec/scheduler.py, tests/test_sampling_context.py, tests/test_throughput_replay.py.

- [x] Inspect sample ownership/cache freshness and registry timing with multiple synthetic sessions. Reproduce any redundant probe or expired-sample retry defect before fixing it.
- [x] Preserve the two-second admission freshness boundary and atomic reservation accounting. Do not extend sample lifetime or raise admission limits.
- [x] Compare old/new behavior under identical headroom and command mix: cold, learned-small, oversized, growth, fixed, strict, red and concurrently fitting sessions. Report admission outcomes, retries and waits separately from probe latency.

## 6. Make release analytics explain effects

Files: libexec/analytics_events.py, libexec/analytics_reports.py, libexec/analytics_releases.py, tests/test_learning_analytics.py, docs/analytics.md.

- [x] Expose observation/reuse failure reason counts with numeric allowlisting. Preserve unknown values for older releases.
- [x] Report exact-estimate reuse separately from compiler-profile reuse and actual below-prior admissions. Ensure no comparison treats the startup default as learned evidence.
- [ ] Save reproducible same-workload replay results alongside the release baseline. Distinguish controlled tests from exploratory live release cohorts.
- [x] Document rollout expectations, current/future sessions, raw-trace expiry, incomplete telemetry and how to compare subsequent releases without interpreting missing data as zero.

## 7. Whole-product validation and single review

- [x] Finish implementation, regression tests, negative controls and release notes before whole-diff review.
- [x] Run grouped ShellCheck plus individual test-file ShellCheck, and parse every Bash entrypoint individually.
- [x] Run `bats tests/` through the live queue; record actual passed/failed counts.
- [x] Run isolated-HOME, no-Docker dry-run Bats through the live queue; record actual counts.
- [x] Run targeted replay and real observer validation; inspect output and terminal status. No claimed gain from microbenchmarks alone.
- [x] Perform one final whole-diff review against identity, uncertainty, command scope, mixed-version and reporting contracts. Correct release blockers; rerun checks affected by edits without starting another review cycle.

## 8. Release, install and verify

- [x] Bump the next available version (expected v0.25.0), update CHANGELOG and relevant guidance. Prepare a PR describing measured defects, changes, validation and limitations.
- [ ] Commit/push the authorized release changes, open PR, await green required CI, merge and verify main. Publish the release at the tested commit.
- [ ] Update the Homebrew tap archive URL/SHA, publish tap change, then upgrade through live memcap with automatic cleanup disabled to preserve active old runners.
- [ ] Verify installed version and source identity, unchanged live config digest, active enforcement, integration diagnostics, recorder health and an installed-package bounded smoke/replay test.
- [ ] Save initial post-install snapshot and release report with links, exact tested commit, test counts, controlled before/after results and remaining real-world uncertainties. Do not claim all-day improvement until sufficient new workload evidence exists.

## Completion audit

Every checkbox requires file/tool/CI/runtime evidence. A published tag alone is insufficient: implementation, local installation, analytics continuity and bounded verification must all be complete. Unresolved capacity pressure is not proof of a software failure or of success; report it honestly without changing policy.

## Execution ledger

- Baseline saved to local `release-history/before-v0.25.0`: 37,290 events, two cohorts. Base commit b45990d; live configuration SHA256 ff85eb87c67832e7c8255c06e7ea7136803fddfaadd5bf1b3b859e63d70c5cd1.
- Read #331–333: repeated headroom/sampling waits and polling overhead; no command reproductions. Private trace expired before v0.24 rollout. Older 473 queued-command records include 108 shell -c wrappers, 23 env prefixes, 3 go -C invocations and package wrappers; do not attribute those historical records to v0.24.
- Natural-exit diagnostic through live admission: 3/4 stable jobs complete; 0/4 churning jobs complete. Saved `v0.25.0-validation/lifecycle-before.json`. Paired samples can precede different registry membership; real missing reads also occur as children exit. Preserve uncertainty for actual missing reads.
- Ruling: while the live queue holds regression tests below required headroom, continue source implementation from demonstrated code/diagnostic failures and retain deliberate baseline-restoration controls for the final queued verification. Do not bypass admission or publish until both the controls and final suite execute. This reorders the test-first sequence to avoid stopping all independent work; it does not remove the required red/green evidence.
- Single whole-diff review completed. Fixed the two reported scope blockers: inherited CDPATH can redirect a relative shell directory change, and an explicit package-manager executable must not be fingerprinted as a different PATH executable. Added regression fixtures for both.
- Added deterministic real-supervisor terminal-empty tests and a deliberate regression control after review identified a coverage gap. The fixtures distinguish completed empty groups from probe faults, stale prior evidence and live identities; runtime results remain pending.
- Repeated natural lifecycle diagnostic exited 0: 3/4 stable and 0/4 churning jobs complete, unchanged from baseline. Saved `v0.25.0-validation/lifecycle-after.json`. This small experiment establishes no broader learning-yield improvement; do not present the targeted bookkeeping corrections as proof of one.
- Preserve unknown observation reason counters for non-owned/legacy measurement protocol rather than emitting false zeroes. Local full validation and three previously submitted targeted checks remain in their original live queue tasks; no duplicate submissions or policy changes.
- Exact-estimate regression test completed successfully (1 test, exit 0). Wrapper/sampler checks and full validation remain queued. A separate bounded local kernel-execution experiment completed successfully and supports a reported nested-script allocation mechanism on macOS 26.6.2; it does not attribute the existing wired allocation or replace release tests. Private findings and numeric evidence are saved in `v0.25.0-validation/kernel-allocation-follow-up.md` and `kernel-exec-probe.json`; no additional production scope was introduced.
- Wrapper regression suite subsequently admitted and passed all 7 tests (tool session 37916, exit 0), including CDPATH redirection and explicit package executable identity. Sampler and full validation tasks remain pending in their original sessions.
- Sampler-context suite subsequently admitted and passed all 4 tests (tool session 81847, exit 0), including fresh-cache publication and stale/incompatible negative cases. Targeted results total 12 passing tests across the three original tasks. Full validation remains pending in original tool session 83471.
- First full validation completed with grouped/individual ShellCheck and all 27 Bash parse checks passing. Both normal and isolated-HOME/no-Docker suites passed 649/650 tests; the same new wrapped-compiler fixture failed in each because Bats exports shell functions, which correctly veto bounded shell prediction. Corrected the fixture to supply an isolated plain compiler environment and added explicit exported-function rejection coverage. No production guard was weakened. Failed-run evidence is retained under local `v0.25.0-validation/attempt-1/`.
- The full rerun is submitted once through live admission (job 574d1d9e, tool session 77399). Both suites must pass before publication. The first run already passed deterministic terminal-empty supervisor coverage, all deliberate learning/sampling negative controls and the controlled admission replay, but final-source full validation remains required.
- The corrected full rerun was admitted without changing live policy. Grouped and individual ShellCheck and all 27 Bash parse checks passed again; normal and isolated Bats results remain pending.
- Corrected normal Bats suite passed 650/650 (exit 0). Isolated-HOME/no-Docker suite is running in the same admitted task. Reports #334–336 appeared during validation; they remain unresolved follow-up evidence. Source inspection confirms monotonic scheduler durations and wall-clock event timestamps, so #334 cautions against treating reported durations as exact wall elapsed developer delay.
- Corrected isolated-HOME/no-Docker suite also passed 650/650 (exit 0); complete validation job exited 0. Source checksums before and after matched. Both suites exercised the deliberately broken controls and deterministic admission replay. Remaining evidence is publication/CI, installed-package smoke and saved replay output, integration and recorder continuity; record those in the private release report after the immutable release commit is created.
