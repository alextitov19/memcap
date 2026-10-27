# Remaining issue investigation

Started September 28, 2026 after v0.18.2. This is an experiment log, not a claim
that every historical report shares one cause.

## Observed baseline

- The local completion/admission log contains a matched short run with 464,543 ms
  of queue wait and 2,046 ms of runtime. Its classification was compound shell.
  Recorded blocker intervals included 260,331 ms of headroom and 204,037 ms of
  sampling. Those are intervals under recorded decisions, not causal attribution.
- Another matched short run recorded 2,062 ms of wait and 2,049 ms of runtime.
- Short runs with no measured peak can report `learning_complete=1`. Actual
  estimate training already requires two samples; the diagnostic overstates
  completeness when a process exits before it can be sampled.
- Live registered server commands include Python HTTP and Django development
  servers under literal `exec`/redirection wrappers. They have empty resource
  identities, so their own session completion treats them as finite jobs.
- Fifteen additional v0.18.2 reports were open at this investigation's initial
  issue-list read. They include lightweight reads/searches, remote control and
  queue waiting. Raw commands and private process identities remain local.

## Experiments

1. Added synthetic server-classification regressions, with negative controls for
   unknown source scripts, builds, help and execution-affecting wrapper options.
2. Added a synthetic completion-event regression for zero/one/two observations
   and incomplete runs. No real child, server or enforcement operation is used.
3. Analyzed 182 admissions after the preceding release's publication timestamp,
   with 177 matched completions. Of 114 jobs that ran for at most five seconds,
   32 waited more than a minute. Publication time does not prove which runner
   version each job had loaded. Four red-pressure samples also occurred; this
   is not evidence that lightweight classification alone resolves host pressure.
4. Traced two remaining learning gaps: the completion guard discarded a single
   large observed peak before it reached the upward-only estimator, and valid
   partial membership measurements raised reservations without retaining their
   measured lower bound as an observed peak. Added synthetic single/partial-peak
   regressions and a faulty-sample negative control. The implementation preserves
   positive partial observations and records them only as incomplete learning;
   complete-run counts and downward learning still require complete observations.
5. Recovered ordinary filename loops, line-range/section awk filters, streaming
   paste, socket inspection and malformed report usage entering admission.
   New loop handling proves finite bodies and checks expanded external arguments
   before execution. Filename expansion cannot introduce an unguarded executable
   option. Native output, exit status and quoting are compared in temporary files.
6. The initial ten-test regression suite passed on the working implementation
   and produced 23 failing assertions against immutable v0.18.2. Existing
   productivity, pressure-accounting, lightweight-family, shell-helper and
   completion-scope suites also passed. Later additions and full release gates
   are pending; this is not a final release validation claim.
7. A fixed twelve-command hook benchmark (ten trials per version, same host)
   queued 12/12 commands on installed v0.18.2 and 0/12 on the working tree.
   Median classifier time for all twelve was 3.684 ms before and 1.958 ms after.
   This measures routing and classifier cost, not a live end-to-end queue latency
   promise. Arbitrary scripts, generators and unbounded raw-file jq loads retain
   admission. Subsequent finite jq/SSM cases have functional regressions.
8. The expanded thirteen-test Python suite passed; the immutable baseline had
   25 failing assertions and one missing-module error. Existing five Python
   suites also passed, as did all 33 Docker tests. Two new device tests initially
   failed because their shell fixture used the same variable name as a function
   local; renamed the fixture so negative cases exercise the intended table.
   That combined run exited 1 and is not counted as a passing release gate.
9. The next run passed all direct Python suites, all 37 device/Docker tests and
   both new shutdown-action regressions, but three existing shutdown tests failed:
   unrelated multiline argv entries in the live process table were incorrectly
   treated as a global observation failure. The device check now skips non-PID
   continuation lines while retaining a known driver's missing/unknown ancestry.
   A synthetic continuation-line negative control covers that distinction. This
   run exited 1 before the four full gates and is not a release pass.
10. After the device observation correction, all seven direct Python suites
    passed (13 new regressions plus 8 productivity, 9 pressure, 7 lightweight,
    5 helper, 12 completion and 21 integration tests). All 37 device/Docker
    tests and all 13 shutdown-action tests passed. The immutable v0.18.2
    baseline produced 27 failing assertions and one missing-module error in
    the new suite, failed the version-only doctor regression and failed both
    new device-action tests. Removing the helper-definition guard in an
    isolated mutant reopened both unsafe forms. ShellCheck then flagged
    indirectly called test stubs; narrow annotations document those calls.
    That run exited 1 at lint, before the full suites.

## Confirmed additional incidents

- #213: the audit records a device shutdown after its launchd_sim was CPU-flat
  for 912 seconds. A live Maestro native driver named that exact device ID and
  was held through an MCP server by a live agent. The device process lives under
  CoreSimulator, so the existing browser-style ancestry protection missed it.
  The new read-only cross-tree check requires the named device, same-user driver
  and complete ancestry to a classified live agent through a server. It runs
  again before shutdown. Unknown observations retain the device; another device,
  a command-line mention, foreign driver or departed agent does not qualify.
- #217: a native status command stalled in the Docker settings `cat` read. It was
  canceled through its original tool session (exit 130). The new Python reader
  exits normally after a one-second deadline using a daemon reader thread, never
  signalling another process. Existing unknown, unreadable and cached-ceiling
  distinctions remain. The no-Python compatibility fallback is unchanged.
- #216: the incident-time doctor output warned about all three profiles solely
  because their installation metadata recorded an older version, while current
  hook definitions matched and Codex reported all nine hooks enabled/trusted.
  Doctor now distinguishes version-only metadata from actual configuration
  mismatch. It still fails for changed hooks, paths, schema, disabled/untrusted
  hooks or unavailable trust, and does not edit any profile.

The single whole-diff review traced expanded inspection arguments to the runtime
guard, finite completion to supervisor/PID identity, and device protection through
both planning and the final action point. It adds no signal target or authority;
existing orphan, age, mobile and agent-ancestry gates remain. Full automated gates
are recorded below when complete. No second review cycle is planned.
The review found one release blocker in helper recognition: a conditional or
pipelined function definition may never define the helper in the calling shell,
allowing a same-named external program to resolve later. Definitions must now
execute unconditionally in that shell; both negative cases have regressions.

## Completion and activation

Python HTTP and Django servers under known literal exec/uv wrappers now register
as persistent resources. Unknown setup scripts and mixed server/build commands
stay finite. For an already-running legacy job, completion may recognize its
original group leader as a known foreground server only with matching UID,
recorded PID start identity and live ancestry to its registered supervisor.
This read-only observation never changes reservations or cancellation targets.
A server descendant, reused PID, foreign owner or reparented leader does not
qualify. The registry remains byte-for-byte unchanged by the completion check.

Stable-path hooks load new completion/inspection code on their next invocation.
Existing supervisors retain their loaded accounting code until they finish.
No hook definition, profile, trust setting or live policy change is required.

## Historical report triage

The open reports were re-read through #217. Most are sanitized snapshots rather
than command reproductions. A snapshot's release number is the reporter's
version, not proof that a waiting supervisor loaded that release. Duplicate
symptom reports must not be represented as independent confirmed bugs.

| Report group | Evidence and disposition boundary |
| --- | --- |
| #151, #164, #168, #184, #188 | Historical Stop loops; the confirmed cross-conversation defect shipped in v0.18.2. This release additionally covers verified old foreground servers. Reservations and other sessions remain intact. |
| #149, #167, #169, #170, #172, #179, #185, #186, #187, #191, #195, #198, #203, #205, #206, #207, #208, #210, #212, #214 | Inspection delays. Recovered finite loops/awk/socket and jq forms have failing-before/passing-after coverage. The twelve-case routing benchmark moves every case out of admission. An old snapshot alone cannot identify every original command. |
| #147, #152, #162, #163, #166, #176, #177, #180, #183, #189, #190, #192, #199, #202, #204 | Remote control. Existing workdir/fallback fixes plus checked SSM parameter helpers and finite jq constructors cover recovered lightweight forms. Arbitrary local Python, test/setup scripts, unknown functions and unbounded raw-file loads still require admission. No remote actions were executed in tests. |
| #153, #154, #155, #156, #157, #158, #160, #161, #171, #173, #175, #178, #181, #182, #200, #201, #209, #211, #215 | General waiting/polling/integration symptoms. During this investigation, fresh queue decisions repeatedly named physical headroom and unused running reservations. Existing owned tasks eventually ran without lease deletion or policy changes. That establishes progress, not that every historical delay was necessary or resolved. |
| #159 | Two additional reproduced upward-learning losses are fixed here. Earlier descendant-accounting and paging-order fixes remain. Host pressure and unknown burst growth need live verification; these changes do not authorize a hard-cap claim or policy relaxation. |
| #213, #216, #217 | Individually confirmed device ownership, version-only doctor warning and blocking settings-read defects, with targeted regressions. |

The investigation itself observed an extended headroom/sampling wait and reported
it once under #200; deduplication retained its new snapshot locally. Live policy,
reservations, supervisors, Docker and other sessions' servers were preserved.

## Final release gates

- Grouped ShellCheck passed, followed by independent checks of all 28 test files.
- All 24 Bash entrypoint/library/helper files passed individual `/bin/bash -n`.
- Normal full Bats suite: 625/625 passed.
- Isolated HOME, `MC_DOCKER_RUNTIME=none`, `MC_DRY_RUN=1`: 625/625 passed.
- The combined final validation command exited 0. Remote CI and installed-package
  verification are separate release steps; these counts describe local source.
