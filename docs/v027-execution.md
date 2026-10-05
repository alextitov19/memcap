# v0.27 execution ledger

Branch: fix/v027-productivity, baseline 4934a3c (v0.26.0).
Plan: v027-productivity-plan.md. User authorized planning through release/install.

## Verified candidate result

The entries below are chronological; pending statements describe their time of
writing. At corrected implementation 8b18f6b, local job f3e5fe73 completed with
exit 0: 652/652 normal Bats and 652/652 isolated-HOME/no-Docker Bats. Candidate
ShellCheck and individual Bash parsing passed. CI run 37253879066 independently
passed both 652-test suites and lint/parse checks. Four deliberately broken
controls produce assertion failures inside the passing regression suite.

The original focused local red run never started before implementation; it was
cancelled as obsolete after CI and replaced by the complete local suites. Do not
claim a chronological local red/green cycle. Deterministic tests compare the
old startup reservation against the learned request with identical host inputs;
this is an admission replay, not a production throughput benchmark or a full
historical-binary replay. Pending burden and insufficient-evidence comparison
verdicts already existed and are retained; the new fields normalize contention
and distinguish pending observations. Historical missing fields remain unknown.

Open reports through #349 were triaged by context. The confirmed fixes have
synthetic paired reproductions. Reports alone do not establish that native reads,
SSM calls or waits were queued. No blanket issue closure is supported. Recorder
gaps and historical wired allocation ownership remain unresolved. Those facts
must stay visible in release comparisons rather than be claimed as fixes.

One whole-diff review is complete. Publishing, Homebrew installation, live health
and the post-install snapshot are the remaining operational checklist, recorded
in the private release-history validation directory after execution. Existing
trusted hook commands remain compatible with the new package. Leave their text
and trust intact during this upgrade; future explicit integration uses the new
interpreter form and requires the owner's independent Codex trust review.

## Evidence and progress

- Read repository guidance, current source and open GitHub issue list.
- Saved private immutable pre-release snapshot (42,181 events, three cohorts)
  under release-history/before-v0.27.0. Earlier October 4 comparison remains intact.
- Live policy SHA-256 remains ff85eb87c67832e7c8255c06e7ea7136803fddfaadd5bf1b3b859e63d70c5cd1.
- Latest file-read incident's adjacent reads were native; a separate build was
  managed. Do not claim a proven file-read classifier defect from that report.
- Found native help incorrectly forced into managed host mode by the environment
  wrapper branch; prepared a paired real-environment-work regression.
- Found post-measurement membership changes retroactively poison owned learning;
  prepared pending-identity, disappeared-child and stale/incomplete-probe tests.
- Prepared sampler publication handoff, literal compiler wrapper, heredoc/data,
  normalized analytics and actionable wait-summary regression cases.
- Found an actual capacity-retention case: no booted devices, one Shutting Down
  device, no launchd_sim, many same-UID reparented simulator runtime leaves.
  GC_MODE is owner-selected on. Existing all-Shutdown gate prevents collection.
  Added a scoped design/task and synthetic grace/authorization tests. No real
  cleanup performed. User approved a normal shutdown of that specific device;
  its existing tool session 82141 remains pending without reset/escalation.
- Wired allocation remains large; nested-script kernel-leak research provides a
  plausible mechanism, not a confirmed owner. Added a bounded internal-dispatch
  mitigation task. Docker VM footprint is not promised reclaimable capacity.

## Validation in progress

The first regression run was submitted once through live admission as job
44c99ff8, tool session 83905. It remains waiting on actual headroom. Keep polling
that existing session; never resubmit or bypass it. The test file was expanded
while the job was still waiting. Explicit-interpreter mitigation has now been
implemented while validation waits. The sampler regression was written first,
but has not executed red: do not claim a demonstrated red/green result yet.

Pre-change Bash parse, git diff whitespace and full ShellCheck passed (56107 exit
0). These are pre-change results, not validation of the candidate.

Internal sampler/status/report/environment/cancellation bridges now invoke the
known Bash dispatcher explicitly; generated hooks do likewise. Hook ownership
recognizes both old and new exact forms, leaving arbitrary shell wrappers alone.
This avoids a candidate OS trigger without changing user commands or reclaiming
existing kernel allocations. Hook changes require profile refresh, session reload
and independently verified Codex trust; the installer never grants trust.

Found existing private v0.25 validation artifacts with a bounded macOS 26.6.2
execution comparison: 256 launches per arm; direct nested arms grew the bucket
by 227/272 elements and explicit-interpreter controls by 107/17. Background
activity was not isolated; supports the mitigation, not historical attribution.
No repeat leak stress test is necessary on this already constrained host.

Added signed per-hour kernel allocation analytics with same boot/build/policy
and 180-second gap checks. Implemented the planned help/heredoc fixes, literal
compiler wrapper scope, pending-child observation reconciliation, bounded sampler
handoff, normalized sampling metrics, wait-summary ordering and transitional
simulator eligibility. All candidate behavioral validation remains pending.

Auditing the installed artifact found a second shebang hop in Homebrew's wrapper
(`memcap` execs `memcap-real`). Prepared the tap formula to exec `/bin/bash`
explicitly too. This packaging change is required for the runtime mitigation;
review and validate the tap diff alongside the repository diff before release.
No installed executable was edited. The normal simulator shutdown was cancelled
through its native tool handle after hanging; final exit 130, shutdown unconfirmed.

Preserved bounded raw traces privately under v0.27.0-validation before rolling
retention expires. They contain credentials and must never be copied into the
repository, issue bodies or release notes. Open issue list still spans 259–347;
the reports are not all reproducible and no blanket issue closure is justified.
Candidate version/changelog are prepared; v0.27 is not released or installed yet.

Single whole-diff review used the house pr-review workflow, with local behavioral
gate explicitly still pending. Scope included untracked tests/docs and the tap
wrapper. Followed the internal bridge/cancellation parent chain, pending-child
measurement/terminal path, and simulator eligibility through fresh choke-point
authorization. Caught and addressed a classifier regression: heredoc text inside
quotes/comments must not swallow a following real build. Added paired regressions
and simulator reactivation/PID/UID/children uncertainty cases. No second review
cycle is planned; test failures and clear release blockers still require fixes.

Candidate full grouped ShellCheck passed (93741 exit 0). Candidate Bash parsing
passed for every dispatcher/library/helper file; git diff --check passed. These
checks do not substitute for the still-pending behavioral test job and full suites.

Draft PR #348 created at 4bb899b. First CI run 37253302436 completed 652 Bats
tests: 648 passed, four failed; isolated-HOME step did not run after that failure.
The new productivity regression group passed. Failures exposed a real mixed-hook
migration mismatch (role parsing still assumed an unprefixed command), plus three
stale expectations for cancellation argv, wait phase text and immediate sampler
return. Fixed role normalization, retained legacy phase wording, and updated
argv/bounded-handoff contracts. CI and local suites must pass on the corrected head.

The focused 21-test regression group passed in CI on the same test-file bytes.
Its separately queued local invocation was therefore obsolete (the required full
local Bats suites will cover it again). Cancelled only that owned pending job
through native tool cancellation; session 83905 ended with exit 130 without
starting the tests. Do not claim a local pass. Required normal/isolated local
suites remain outstanding; no other session's job or lease was touched.

Corrected head 8b18f6b passed CI run 37253879066: 652/652 normal Bats and
652/652 isolated-HOME/no-Docker Bats, plus ShellCheck and per-file Bash parsing.
Both PR check runs are green. Logs are saved privately. Submitted the required
full local normal and isolated suites once as job f3e5fe73, tool session 82871;
it is pending, not a local success. A manual memory_pressure -Q reading of 8%
does not match the scheduler's vm_stat page-based headroom definition. Admission
still reported about 0.96–1.20 GiB against 1.50 GiB required. No policy relaxation.

The current recorder heartbeat remains live with zero reported errors. Earlier
multi-minute recorder gaps include a later-day gap and are not explained solely
by startup. Their cause remains unproven; this candidate does not claim to fix them.

## Rulings

- Execute inline with one final whole-diff review, honoring the user's requested
  end-to-end delivery and repository cadence; no per-task approval/commit loop.
- Do not call the hover check lightweight: its queued command includes a dev
  server and browser, and its automatic request retains observed upward evidence.
- Scope includes the simulator transitional-state cleanup defect because it is
  directly observed in the reported capacity stall. Maintain every existing
  identity, activity, network, grace, pause and signal-authority guard.
- No speculative process killing, Docker restart, OS upgrade or live policy
  relaxation is part of this release. Unresolved external attribution stays explicit.
