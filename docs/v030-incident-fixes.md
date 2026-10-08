# v0.30 incident investigation — October 8

Baseline saved locally before changes in
`release-history/before-v030-20261008` (38,301 events, two cohorts).
Private command traces and tool results were inspected locally; no raw commands,
project paths, credentials or transcript contents belong in public issues.

## Supported causes and scope

- #392: a compound inspection included an Xcode version query. The classifier
  treated that executable as build evidence even in version-only mode. Keep the
  exact version query native, including through xcrun; preserve build admission
  and inspection of executable substitutions.
- A later deduplicated #393 reproduction and new #400 reporting sequence exposed
  shell executable lookups being treated as launches. Lookup-only command flags
  must not queue the named program; actual launches and substitutions still do.
- #393, #394, #395, #397: inspected reproductions followed actual test runs,
  not delayed repository searches. Queue feedback explicitly instructed agents
  to file inspection/polling reports, including after a final test result. Make
  reporting conditional on an observed incident, preserve the mandatory report
  for actual inspection delays, and prioritize final tool status over historical
  admission messages. New lane-labelled queue messages also missed the prompt
  branch and unnecessarily triggered a fresh diagnostic memory probe.
- #396 and #398: a Codex tool requested a subdirectory, but the Bash-compatible hook
  supplied only the session directory. An inserted --cwd overrode the actual
  tool cwd. Inherit the host's execution directory unless the tool input supplies
  one explicitly; keep Claude's established cwd contract. Classification still
  uses the best available hook directory and the runner reclassifies at launch.
  In #398, formatting was the first stage of a combined formatting/test command.
  Its managed trace records the session directory; the tool request supplied the
  subdirectory. The relative-path formatter failed there, then passed as a
  standalone native call in the requested directory. This is the same wrapper
  cwd defect, not evidence that all native control calls were delayed.
- #399: local watchdog evidence identified a 4.24 GiB Expo process above the
  configured 4 GiB per-process ceiling. This was configured enforcement, not
  proof of a spurious kill. Expo startup was nevertheless incorrectly unknown
  demand: it escaped admission and worker limits even through an explicit run.
  Classify known Expo startup/build operations, bound Metro startup workers,
  and recognize persistent Expo servers in hooks and explicit runners. Keep
  version/help/config queries native. Limits and signal eligibility do not change.
- #391: Claude's worktree guard rejected the optional passive observer's opaque
  shell wrapper for otherwise native inspections. Leave unknown-demand native
  calls unchanged in Claude subagents and known Claude worktree paths, foregoing
  optional passive learning there. Positive heavy evidence and learned promotions
  still require admission. Do not change or bypass the host's worktree guard.
- #401 arrived during validation. The report observed four finite slots occupied;
  local evidence identified one as an Android emulator, alongside actual test
  suites. Treat supported direct emulator launches as persistent resources, while
  retaining their memory accounting and admission checks. Unknown options and
  mixed commands stay finite. This removes one demonstrated slot-retention cause;
  it does not establish that the entire reported delay was avoidable, nor change
  already-running supervisors' classifications.

## Validation

Initial targeted run: seven tests, nine failing assertions/subtests covering
classification, incorrect cwd, missing worker/resource handling, misleading
feedback and an unnecessary probe. After the first changes, seven passed and
the additional explicit-run persistent-resource test failed as intended.
Focused validation passed 99 tests before the later emulator reproduction.
The emulator regression failed on the old finite-lifetime behavior as expected.
All 100 focused tests passed with the emulator fix.
Final full-suite and CI results are recorded
in the release PR and the private local rollout receipt.

Single final review caught an overly broad automatic persistence change: an
arbitrary package script named start can run a finite test. Its regression failed,
as did two end-of-options controls for Expo. Narrowed automatic persistence to
known Expo startup and excluded post-delimiter arguments from help detection.
The first full attempt was cancelled (exit 130) because these changes superseded
it. A second attempt was stopped when the new executable-lookup reproduction
arrived. Neither interrupted attempt counts as passing full validation.
A third attempt was cancelled while investigating #391; its isolated-agent
regression failed on the original observer behavior before the compatibility fix.
The first completed normal run passed 656/657 checks and failed the existing
queue-guidance size limit (1,723 characters versus a limit below 1,500). Shortened
the message without removing conditional reporting or final-result handling;
the 21-test feedback suite is included in the final focused validation too.
A standalone feedback run passed the size check but failed an unrelated native
inspection expectation because it saw the live optional observer. Its supported
Bats/validation entrypoint supplies isolated HOME/config/state; final focused
validation uses that same isolation, rather than changing production behavior
to satisfy an incorrectly invoked fixture.
The next normal run caught the reporting contract's required capacity-wait
sentence missing from the shortened text. Restored that sentence and added the
26-test report suite to focused validation; that superseded full run was cancelled.

No live policy, container runtime, other session's tasks, or existing servers
are changed. Existing supervisors retain their loaded code. New launches adopt
the installed runner. Hook command definitions remain unchanged, so no automatic
trust acceptance or profile rewriting is needed.
