# Open issue investigation — v0.18.4

This investigation started with 20 open issues. Unlike the preceding snapshot
consolidation, it recovered concrete command shapes from local evidence and
reproduced their behavior. Private commands, paths and transcripts stay local;
the tests use generic temporary fixtures and fake remote clients.

## Reproduced defects

| Reports | Reproduction and resulting behavior |
| --- | --- |
| #147, #227, #231, #235, #236, #237, #238 | Literal AWS retry environment settings caused otherwise supported SSM calls to queue. SSO login, hosted source uploads and hosted schema description were missing families. Fixed forms remain native, including guarded finite upload loops. |
| #149, #224, #225, #229, #233 | Command lookup at the end of a read/search chain, home-path aliases, file:line loops, archive reads and finite jq grouping/extrema were unrecognized. Their output and exit semantics are preserved; expanded external arguments still pass runtime checks. Unsupported `memcap cancel` now returns its existing usage error immediately rather than reserving memory to reject it. No cancellation authority was added. |
| #228, #234 | Concurrent first hooks could all emit full session guidance; each queue transition also repeated long protection text. A separate nonblocking receipt lock makes the initial refresh atomic. Subsequent queue messages are brief and keep waiting/reporting instructions. SessionStart deliberately refreshes after compaction or resume. |
| #159 | Failed runs discarded measured peaks, and updating a cached estimate did not refresh retention order. Both defects reproduced with synthetic completions. Failed runs may now raise estimates but cannot lower them or earn complete-run credit. Recently updated estimates survive cache pressure. |
| #151 | The previously reproduced cross-session ownership defect remains covered by the completion-scope suite, including shared-daemon conversations and subagents. Guidance deduplication further reduces repeated completion context. No other scope's jobs are released, awaited or canceled. |

The original eight new tests produced 20 failing assertions before implementation.
The two learning regressions produced three additional failures: failed exits 7
and -9 discarded their peaks, and cache churn evicted a recently trained entry.
The first implementation run failed one upload-loop test; literal finite values
must retain their extensions during proof. That failure was corrected rather
than weakening the test. The expanded eleven-test suite passes with the patch
and fails against immutable v0.18.3 with 25 assertion failures and one missing-API
error. The deliberately broken SessionStart refresh fails its single regression.

A fixed fourteen-command comparison routes 0/14 recovered forms natively on
v0.18.3 and 14/14 with the patch. Across ten trials, median classification time
for the complete set was 9.157 ms before and 3.702 ms after. This proves routing
and classifier cost, not an end-to-end host latency guarantee.

## Heavy-work reports and scope limits

#200, #226, #230 and #232 describe long waits for local tests/builds. Source
inspection of a helper described as remote deployment found a local Go build
before the remote API operation. That helper must retain admission. A regression
ensures that a remote-looking filename cannot exempt its local build.

The post-v0.18.3 local sample contained 125 admissions, 124 matched completions,
33 executions under five seconds that waited over a minute, and one red-pressure
sample. Three requests began at 1 GiB and later recorded peaks above 4 GiB with
no prior complete estimate. Publication time does not prove each supervisor's
loaded version. Short runtime alone does not establish lightweight eligibility.

This release removes the reproduced false reservations and guidance overhead,
and retains more valid peak history. It does not remove physical headroom,
pressure checks, explicit reservations or the 512 MiB automatic floor. Unknown
first-run growth and already-running/unmanaged applications can still cause
pressure; admission is not a kernel memory limit or a promise of immediate starts.
Historical aggregate snapshots cannot establish the cause of every wait.

## Activation

Stable Homebrew opt paths load the new hook code on the next invocation. Existing
supervisors retain their loaded scheduler code until their tasks finish. No hook
definition, profile, trust, consent or live policy change is required. Separate
Claude profiles retain their existing installations; Codex trust is checked by
doctor, never approved by the installer. SessionStart refreshes full guidance
after reload, resume or compaction. The generated Stop timeout/wait contract is
unchanged (75-second hook timeout, up to 60 seconds of local waiting).

## Validation

All five direct suites pass (11, 13, 16, 12 and 9 tests), as do all 37 selected
Bats checks. Grouped ShellCheck and independent checks of 28 test files pass;
all 24 Bash files parse individually. Final normal and isolated-HOME/no-Docker
full suites both pass 626/626; the combined validation exits 0. No live
enforcement tests or real remote operations are used by the new regressions.

Both full suites then passed 626/626. Release preparation exposed two more
queued inspection forms: built-in GitHub issue/PR help. A narrow help-only rule
and positive/alias-extension negative cases address that gap; final gates are
repeated successfully after this last correction. The incident was reported once and deduplicated
to #233, retaining its new snapshot locally rather than posting another comment.

The first combined validation passed all five direct suites (11, 13, 16, 12 and
9 tests) and 36/37 selected Bats tests. An older negative fixture classified a
hosted YAML upload as local work; the documented hosted-upload contract requires
native handling. The fixture now checks native YAML upload and keeps watch mode
as the negative control. That initial run exited 1, before lint or full suites.

The single whole-diff review traced expanded command arguments through proof and
runtime checks, guidance receipts through session/subagent identity and atomic
replacement, and failed-run peaks through the upward-only estimator. It found
one additional release blocker: a busy receipt lock could suppress SessionStart
after compaction. SessionStart now emits full context without waiting when that
lock is busy; a deliberately broken temporary copy must fail its regression.
No second review cycle is planned.

The current investigation reported its prolonged verification wait once as #239.
That report is evidence of remaining live latency, not a failed test assertion or
permission to reduce reservations. The original queued task eventually ran and
passed all eleven tests (0.627 seconds of test runtime). A later combined run
reached a real fixture mismatch described above; the corrected run passed its
targeted checks and continued to the full release gates.
