# Analytics validation, September 29–30, 2026

## Release CI correction

The first release CI runs failed the collector heartbeat deadline. A diagnostic
stack trace located startup in `HTTPServer.server_bind` → `socket.getfqdn`:
reverse DNS stalled despite the receiver binding only numeric loopback. The
receiver now binds through `TCPServer` and uses its numeric address directly.
A negative control makes any DNS lookup fail; it failed before the correction
and passes afterward, alongside authenticated OTLP delivery and collector checks.
The eight-second readiness deadline remains unchanged. Publication CI results
are recorded on PR #268; the earlier evidence below describes pre-release runs.

This work adds local analytics and retains the previously installed SSM inspection
improvement. It does not change enforcement thresholds, resume the owner's pause,
or widen process eligibility. Every enforcement signal still goes through the
existing guarded `mc_kill_pids` path; telemetry observes syscall outcomes afterward.

## Verification

- 24 focused analytics tests pass: private schema, absent/full transport, real
  collector delivery, paused hooks without reservations, clock changes/reboots,
  unfinished work, persistent-resource claims, actual SQLite byte exhaustion,
  OTLP authentication/wire format/retry deduplication, transactional profiles,
  independent service installation, and loaded-code/Homebrew provenance.
- Negative control: removing admission events makes the queue-regression test
  fail. With production correlation restored it passes. Deliberately slow fixtures
  produce a regression signal; equal data and insufficient evidence do not.
- Existing focused scheduler (48), pause (11), scheduler metrics (6), and
  completion-scope (12) Python tests passed during implementation.
- HTML verified in Chromium with fixtures and actual live data at desktop/light
  and mobile/dark sizes: screenshots inspected, keyboard expansion works, no
  document overflow or external requests.
- ShellCheck and 26 individual Bash parses pass. The final full-suite results and
  installation receipt are recorded below after completion.

The first full runs failed the same enforcement-source test because an optional
analytics guard referenced an unset `LIB`. The guard now skips optional recording
when the library root is unavailable. The targeted failure passes after the fix.
An isolated full run also found an existing exact-output test using unsorted
parallel `rg` traversal. Its fixture now requests `--sort path`; it still asserts
native and guarded stdout, stderr, arguments, and exit behavior exactly.
Another isolated run exposed two adjacent memory-fallback fixtures that compared
successive RSS readings of a live sleeping process. They now use a synthetic PID
and deterministic `ps` output, retaining exact fallback/argument assertions.

## Measured observer cost

Bounded fixture manifest retained locally at
`~/.local/share/memcap/benchmarks/2026-09-29-analytics.json`.
Benchmark build digest: `cff0edef1cefaecbe7e3f53909eb3482874a9f98ed7f00848ba80928a9758aa1`.
The subsequent import-only correction is described below; hook paths did not change.

| Measurement | Observations | p95 |
| --- | ---: | ---: |
| Nonblocking producer, recorder absent | 1,000 | 0.01579 ms |
| Complete queue hook, enabled policy | 60 | 58.78 ms |
| Complete queue hook, owner-paused fixture | 60 | 10.16 ms |
| Complete feedback hook | 20 | 207.37 ms |

All 120 queue-hook fixtures and 20 feedback invocations passed. Fixtures use
isolated state and do not run real AWS/build commands. The producer target was
met in this sample; both complete hook paths exceed the proposed 50 ms target.
This last run overlapped validation; an earlier run measured queue/feedback p95
at 48/146 ms. Host contention and sample conditions matter. These are not
whole-agent task durations, nor a causal before/after
improvement claim. Native Claude hook telemetry includes all matching agent hooks
and is labeled separately from memcap-only guard timing.

After historical import, two live heartbeat observations 80.99 seconds apart
showed 0.581 seconds of collector CPU (0.72% of one core), zero recorder errors,
and about 24.3 MiB of analytics disk usage. Observed peak resident memory was
about 31.1 MiB; this is not physical footprint. This short active-work sample
does not include CPU consumed by probe subprocesses or hook producers and is
not a steady-state battery benchmark.

## Scope of review

One final review examined the full working diff and new files, concentrating on
independence from admission/signaling, private transport/storage/profile handling,
and timing/outcome attribution. Fixes prevent omission of queue wait in comparisons,
duplicate counting of feedback as tool/Stop events, native OTLP naming mismatches,
and treating resource reuse as finite-job cancellation. No second review cycle,
commit, push, release, or public analytics upload was performed. CI status is unknown.

## Final gates and installed state

The normal and isolated/no-Docker suites each passed all 632 Bats cases. Live
installation verification then exposed macOS `ENOBUFS` on bulk historical import.
A regression test reproduced the failure before correcting the bounded retry
handler; it now also asserts reported drops and prompt missing-recorder errors.
All 24 analytics tests pass. The subsequent full runs each failed three
configuration/profile fixtures: after local installation, `memcap run` exported
`TOTAL_BUDGET_GB` for analytics and the shared fixture setup did not clear it.
The setup now clears it alongside inherited queue settings. All 69 config/profile
cases pass under the installed runner, as do grouped ShellCheck (55 files) and
26 individual Bash parses. The final runs with that isolation correction passed
all 632 cases normally and all 632 with a temporary HOME and
`MC_DOCKER_RUNTIME=none`, both exit 0. Earlier failed runs are not counted as
passes. Logs: `/tmp/memcap-analytics-bats-delivery.log` and
`/tmp/memcap-analytics-bats-delivery-isolated.log`.

Installed build: `a854202d4141617667d841b7c8eb3136469e80d8c43eda2bc2f119fa8e4a2243`.
The source and installed digests match. Files were backed up under
`~/.local/share/memcap/local-patches/2026-09-30-analytics-000312`; the original
pre-analytics installation is retained in `2026-09-29-analytics-235820`.
The Homebrew wrapper remains intact. This is a local patch, not a published release.

The independent analytics LaunchAgent is running and the authenticated loopback
receiver accepted an empty request. Both default and personal Claude profiles
have native telemetry configured with content logging disabled. Existing hook
definitions, Codex hook file bytes/timestamps, enforcement configuration and pause
marker were verified unchanged. Doctor confirms all three profiles' hooks and
all nine Codex hooks enabled/trusted; project overrides and old-session reloads
remain outside that check.

Actual current-session events are arriving: one snapshot contained 137 paused
routes and 131 matched tool spans. Native Claude events were still zero, as
expected until a process starts with the new environment. Do not describe those
tokens/API durations as observed. The legacy import completed with 15,789
submissions and nine reported drops; imports are best effort and idempotent.
The source log files were not changed. A self-contained report is exported to
`~/Downloads/memcap-performance.html`.
