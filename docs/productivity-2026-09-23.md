# September 23 productivity regressions

Reports: [#19](https://github.com/alextitov19/memcap/issues/19),
[#20](https://github.com/alextitov19/memcap/issues/20),
[#22](https://github.com/alextitov19/memcap/issues/22),
[#23](https://github.com/alextitov19/memcap/issues/23),
[#24](https://github.com/alextitov19/memcap/issues/24).

## What was reproduced

At c075ad8 / v0.14.2, five of six ordinary inspection/control command forms
received workload admission instead of the lightweight path. Plain `rg` already
worked; multiline `rg`, `LC_ALL=C rg`, `git -C … status`, SSM send-command and
get-command-invocation all received the default 2 GB reservation. No remote
command or user workload was executed during reproduction.

The snapshots attached to #20 and #24 show host-headroom admission decisions,
not Docker-ceiling decisions. Correlating the local incident transcripts identified
the actual commands: #20's queued key check was a cat/sed/rg inspection pipeline
using `sed -n '/MARKER/,$p'`; #24's status check was an SSM send/query workflow
with a short propagation delay and `$(cat /tmp/id)` to read its command ID. Both
now classify as lightweight. Regression tests use disposable names and remote
`true` payloads; no private command text is included in the repository.

The first larger inspection script also contained a finite file loop. Its narrow
`for file in literal-files; test -f; read` idiom is now recognized, with at most
16 literal filenames and independently checked read-only bodies. Arbitrary loops
remain conservative. Queued job-ID lookup pipelines are replaced by `wait --session`.

## Changes and boundaries

- Classify literal multiline inspection, safe environment prefixes, Git `-C`, and
  the reported finite file-inspection loop and specific SSM control calls without workload reservations, including a literal
  command-ID file read and bounded propagation sleep in the reported workflow. Every shell stage
  must qualify; arbitrary substitutions, preprocessor execution, mixed builds, transfers
  and interactive SSM sessions retain admission.
- Provide `wait --session` without a lookup pipeline or new reservation. It is
  read-only and bounded to 60 seconds, selects the current agent process's finite
  jobs, and refuses unknown ancestry. Explicit IDs remain the more precise option
  when multiple conversations share an agent process.
- Stop guidance tells agents how to cancel obsolete owned tasks through native
  task cancellation. Live needed work still prevents premature completion; dead,
  canceled, recycled-PID and foreign-session entries do not.
- Different fixed report contexts have different deduplication identities. This
  retains separate read, wait, remote and Stop-hook incidents without publishing
  free-form notes, commands, paths or tokens. Consent and rate limits still apply.
- Stable installed hooks run current code; running sessions receive updated
  guidance at their next tool, once per version. Newly launched cached automatic
  wrappers reclassify before reserving. Existing queue supervisors are not hot
  replaced or restarted, and changed hook definitions still require agent reload.

## Measurement

A five-trial local fixture ran the same fake SSM executable through each
classifier. The scheduler sample reported yellow, 20 GB cap, 16 GB tracked,
3.6 GB available and 2 GB headroom, with available capacity released to 6 GB
at 0.5 seconds. No real Docker/process state or memory policy was changed.

| Version | Classification | Median completion | Range |
| --- | --- | --- | --- |
| v0.14.2 | workload, default 2 GB request | 0.730 s | 0.722–0.833 s |
| v0.15.0 | lightweight, no request | 0.092 s | 0.089–0.209 s |

This demonstrates removal of the simulated capacity wait (~7.9× in this fixture),
not a predicted speedup for real AWS calls or builds. The six-case classification
check improves from one to six lightweight results. Under permanently insufficient
headroom the old classified workloads would continue waiting; the new lightweight
forms do not depend on build capacity.

## Regression coverage

`tests/test_productivity.py` covers both agent hook payloads, exact no-rewrite
results, mixed-heavy and shell-expansion negative controls, cached-wrapper CLI
execution, explicit-reservation preservation, and read-only ownership-scoped waits.
Before the fix its initial classification suite failed on all ten positive cases.
`tests/test_report.py` checks distinct contexts, duplicate suppression and rejected
free text. `tests/feedback.bats` verifies next-tool delivery once per version,
including a simulated existing session's old receipt. Existing scheduler and
lifecycle tests retain pressure, capacity, rotation, identity and cancellation
coverage. Full release validation is recorded in the PR and release notes.

During full validation, concurrent normal/isolated suites exposed a fixture
collision: the idle-mobile-tooling test used the same process pattern in both
runs. It now uses a per-test-process marker, so a second suite's freshly spawned
fixture cannot reset the first suite's idle observation. Production cleanup policy
is unchanged.
