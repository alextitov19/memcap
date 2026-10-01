# Admission lanes and long queue waits — September 30, 2026

Historical development record. The subsequent owner-approved demand redesign
supersedes small-lane routing for new jobs: lightweight commands run natively;
new managed work uses the heavy queue. Legacy records retain compatibility.
See [the redesign plan](plans/2026-09-30-demand-classifier.md). The tests and local
installation described below preceded that redesign and do not validate it.

Small priority requires at least three complete exact-profile runs, an automatic
reservation at most 1 GiB, and a supported executable/script identity. It grants
no admission exemption. Both lanes share pressure, physical headroom, reservations,
session rotation and the owner's global concurrency limit. Small jobs have no
additional two-job ceiling. Fitting sessions without finite running work receive priority,
then the least recently admitted session goes next. Small/heavy preference breaks
ties; it cannot grant a project repeated turns ahead of fitting peers. After
three small admissions the tie preference shifts to heavy work. Subagents share
their parent's turn. Running jobs are never suspended to create apparent capacity.
Known heavyweight tools and interpreters with unfingerprinted external code stay
heavy. Changed script contents or worker allocations invalidate small priority
before launch. Direct verified SSM reads/searches remain outside admission.

Queue patience defaults to 86,400 seconds. Managed Claude hooks request background
execution with an 86,400,000 ms outer deadline; integration raises only the selected
profiles' maximum timeout ceiling. Native command defaults, explicit runner waits,
Codex response yields, short hook deadlines and cancellation remain intact.
The outer deadline includes both queueing and execution. Current Claude sessions
need a settings reload; old host tasks and supervisors retain their loaded behavior.
An expired task cannot be revived by upgrading memcap.

Claude's background timeout controls require 2.1.285 or newer; the local installed
binary is 2.1.286. See the [official background command contract](https://code.claude.com/docs/en/tools-reference#background-commands).

## Negative controls and review

- Initial lane suite: five failures and four errors before implementation.
- Initial deadline suite: four failures and two errors among seven tests. It
  reproduced the retained 600,000 ms host deadline and a default admission expiry
  after a simulated 2,000-second wait.
- One final review concentrated on admission/accounting, fairness, profile identity,
  timeout semantics, and preserving unrelated profile settings. It found an
  unverified-interpreter priority gap; its regression test failed for nine runtimes
  before the conservative classification fix.
- First full Bats run: 632 passed, three failed. In addition to that regression,
  the run caught a redundant wrapper rewrite and two outdated mock signatures.
  These were corrected before the final required checks.
- A subsequent normal suite passed 634 of 635 Bats cases; three new reservation
  tests produced four assertion failures. They proved that a sampling gap could
  resurrect a retired lifetime peak. The repair keeps a separate admission peak,
  preserving current allowances, partial growth and uncertain-owner floors while
  retaining the lifetime peak for learning.
- That run's live supervisor exited 75 on registry contention after admission;
  its child continued to finish the normal suite. The isolated suite and local
  installation did not run. A typed lock-timeout retry now retains supervision;
  a damaged registry still fails. A real lock/child regression checks single
  execution and final status, including a saved pre-fix negative control.

## Pre-install local baseline

The private snapshot `/tmp/memcap-pre-lanes-analytics.json` contains today's retained
data across builds and projects, including development/test activity. It is a
descriptive baseline, not a matched experiment or proof of causality.

- 367 started jobs; 356 observed queue waits.
- Queue wait: median 6.83 seconds, p95 366.14 seconds, maximum 944.49 seconds.
- 116 of 201 jobs with observed runtimes between 10 ms and 5 seconds waited longer
  than they ran. A short runtime does not establish low memory demand.
- Guard component p95: 12.35 ms.
- Recorder CPU: 0.431% of one core over observed intervals, excluding probe children
  and hooks.

New events carry a numeric admission lane; reports group waits by small/heavy.
Historical events retain unknown lane coverage. Compare like workloads and workers
within known build/policy/enforcement cohorts after collecting new data; do not
interpret a longer timeout as evidence of faster completion.

The lane/deadline/reservation/lock patch passed both full suites: 635/635 normal
and 635/635 with isolated HOME, no Docker runtime and dry-run enforcement.
Grouped and individual ShellCheck checks passed; all 26 Bash files parsed.
The two lock tests and all 13 adaptive tests also passed. The same real-lock
regression failed against the saved pre-fix scheduler as expected.

That patch was installed locally at 16:09, with private original-byte backups.
Both Claude profiles received the timeout ceiling, all three selected profiles
passed integration checks, and Codex reported all nine hooks enabled and trusted.
Queue policy, pause state, unrelated profile settings and entrypoints were verified
unchanged. Current session reload state is not certified.

Post-install health verification found an existing analytics storage failure:
the database had reached its 65% SQLite page cap, below the 80% disk cleanup
threshold. Failed sample writes also prevented both retention and heartbeat
updates. A bounded page-pressure repair and independent heartbeat path are under
validation; earlier dropped observations cannot be reconstructed from the database.
The completed suites above precede this final storage repair.

CI has not run for this uncommitted local patch.

## Additional queue feedback

The expanded candidate is v0.21.0. Temporary owner-enabled command tracing is
also written, with private bounded files, identity-checked snapshots of existing
supervisors and explicit clear/expiry controls. Its tests exercise exact command
capture, real hook/CLI dispatch, retention, isolation and exclusion from analytics.
They have not executed yet. The user authorized enabling it on this Mac after
validation; a private snapshot of currently queued supervisors was saved meanwhile.

The session-first rotation and finite `launchctl print` classification are written
but await validation. The existing queued validation job will run the pre-fix
fairness negative controls, targeted checks and both full suites. Local installation
is deferred until this expanded scope passes.

`memcap wait ID` previously printed matched-job counts without saying they were
scoped. Thus “0 running, 1 queued” did not establish an idle host queue. Its output
now identifies those counts as belonging to the wait scope.

The live orphan inspection found an identity-matched surviving group member and
a remembered descendant outside that group. This explains why observation recovery
retains that job's allowance. It does not authorize deleting the reservation,
terminating either process or weakening the escaped-descendant safeguard.
