# v0.29 backlog execution

User approved the complete October 7 overview remediation, issue dispositions,
release and installation. Baseline is v0.28.2, branch fix/v029-backlog. Execute
inline; one final whole-diff review. No unrelated live workload bypass, automatic
Codex trust approval, runtime restart, policy weakening or other-session cleanup.

## Scope and release gates

- [x] Preserve baseline and inventory all currently open issues.
- [x] Integration: actionable per-hook runtime/trust diagnostics and compatibility.
- [x] Evidence: bounded retained incident details, rollups/coverage and trace expiry.
- [x] Routing: explicit-wrapper and hook consistency, ordinary/heavy paired tests.
- [x] Fairness: eight-session fitting-turn replay, aged requests and legacy compatibility.
- [x] Learning: diagnose lost complete evidence without lowering unsafe estimates.
- [x] Sampling/polling: reproduce contention and unnecessary wait overhead.
- [x] Cleanup: simulator ownership/lifecycle and termination provenance.
- [x] Every issue mapped to tested fix, supported existing behavior or explicit
      unresolved evidence; never claim a historical cause that cannot be proved.
- [x] Full ShellCheck, every Bash file parsed, normal Bats and isolated HOME /
      no-Docker Bats. Record actual counts and negative controls.
- [ ] One whole-diff review, CI, release, Homebrew installation, live health checks,
      saved post-release evidence and issue dispositions.

## Baseline / known evidence

Private full review and immutable snapshots:
~/.local/share/memcap/release-history/v0282-overview-20261007/OVERVIEW.md.
New pre-change snapshot: release-history/before-v029-backlog.
104 reports were open in the overview; refresh inventory before disposition.
461 completed current-release jobs: median wait .729s, p95 306.622s, max476.163s;
92 waited over60s. No sampled red, but raw evidence missing during the busiest
window. Complete learning59/344 successes. Ten simulator shutdown attempts.
Codex hooks enabled with trustStatus modified; owner review required, not
automatically granted. Raw trace expired October4; historical commands unavailable.

## Investigation findings

- Baseline saved (38,708 events / three cohorts); 104 open reports refreshed.
- Actual raw-event storage dominates SQLite pages; rollups were only about1.5MiB.
  Reproduced old eviction losing all ten older decision events while keeping
  newer routine hooks. Priority eviction now retains them within the same cap.
- Reproduced a half-second sampler publication missed by the quarter-second
  handoff. A bounded one-second handoff consumes it without a duplicate probe or
  registry lock. Existing key/freshness checks remain authoritative.
- Reproduced a direct live-agent simulator driver losing protection because it
  lacked an intermediate MCP process. Exact device/driver/UID/ancestry evidence
  now protects both paths. Foreign/departed/malformed cases retain their guards.
- Reproduced a child born between probe snapshots permanently poisoning learning
  even after complete subsequent measurement. Birth identity now remains pending;
  disappearance before registry refresh still prevents complete learning.
- CLI native reclassification already existed. Added actual decision provenance
  to runner analytics/registry and owner-enabled trace, rather than claim every
  lightweight report proves an incorrect classifier.
- Added bounded private incident bundles, per-event coverage, visible trace expiry
  and expiry maintenance; capture never renews owner consent or uploads commands.
- Added separate runtime configuration, disabled-hook and trust-state diagnostics.
  The owner's Codex trust review remains required; no automatic trust writes.
- First targeted Bats red run: 8/10 passed, two regression groups failed. Corrected
  targeted Bats run: 10/10 passed. Child-birth regression failed before correction,
  then passed; 12 existing observation tests passed. Further tests remain pending.
- Existing eight-session fairness, subagent turns, aged-request backfill and
  mixed-runner compatibility tests are part of full validation; no speculative
  fairness-policy replacement is warranted by one occupied-slot snapshot.

## Current validation

The adaptive single-request regression failed with the old hard-budget rejection;
the corrected adaptive/strict/headroom cases passed in the subsequent run.
The expanded run completed 13 tests: 11 passed, two failed as intended for Python
filename-only heavy classification and hook/runner decision double-counting.
Both fixes are implemented; the next run includes fourteen tests and is pending
in the live queue. No duplicate workloads were submitted for that run.

Grouped ShellCheck, individual test-file ShellCheck, individual Bash parsing and
diff whitespace checks passed before the final Python/report additions. Full Bats
and isolated HOME / no-Docker validation remain required.

Two reports arrived during development (#388 queue-stall, #389 ssm-control);
the inventory is now 106. Their snapshots do not contain command text or prove
that all current waits are misclassification. They are included in the audit.

The fourteen-test run subsequently passed. The first full normal Bats run passed
653/656; three legacy assertions expected the former 250ms handoff or retained
expired trace records. Updated those assertions to the one-second bound and
automatic expiry. Isolated HOME did not start after that failed run.

Single final whole-diff review: checked learning completeness and its reservation
callers, direct-device ownership gates, private reporting boundaries and retention.
Found one release blocker: a new birth could overwrite an older pending identity
at the same PID. The added regression failed, then the implementation was changed
to retain the lost-observation failure. Missing, reused or exited children still
cannot certify a lower estimate. No second review cycle.

The new full normal + isolated-HOME/no-Docker run is pending. Its low-headroom
negative control now asserts the actual headroom decision, avoiding a false pass
from the separate pressure-recovery gate. Incident bundles filter old lifecycle
checkpoints to their stated 30-minute window.

That rerun exposed a fixture error: assigning controller state under a registry
lock does not save it. The headroom assertion failed on the recovery gate. Saved
the synthetic state explicitly, cancelled the already-failed run through native
task cancellation (exit130), and started fresh validation. The corrected preflight
passes all14 tests, including PID reuse and actual headroom refusal.

Owner-authorized raw tracing was renewed for one 24-hour window during rollout;
no automatic renewal was added. The snapshot contained known heavy workloads and
explicit resources, not the reported SSM command. Historical cause stays unknown.

## Final branch validation — October 7, 22:04 PDT

- Corrected preflight: 14/14 Python regression tests passed.
- Grouped ShellCheck passed; 32 test/helper files also checked individually.
- 27 Bash files parsed individually; validation driver ShellCheck passed too.
- Normal Bats: 656/656 passed.
- Isolated HOME with MC_DOCKER_RUNTIME=none and MC_DRY_RUN=1: 656/656 passed.
- Staged-diff Gitleaks: no findings. Diff whitespace checks passed.
- Single final review complete; its PID-reuse blocker is fixed and covered by the
  passing preflight and both full configurations. No additional review cycle.
- Live policy SHA-256 remains
  ff85eb87c67832e7c8255c06e7ea7136803fddfaadd5bf1b3b859e63d70c5cd1.

At PR submission, CI, publication, Homebrew installation and issue mutations are
still subsequent gates, not inferred from these local results. Installation must
retain the old keg for existing supervisors and refresh only an enabled analytics
collector. The release page and private rollout receipt record the final outcome.
