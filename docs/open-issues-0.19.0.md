# Open-issue repairs in v0.19.0

This release addresses the 23 open issues present on September 29, 2026
(#159, #200, #226, #230, #232, #239 and #241–#257).

## Abandoned work and held capacity

A registered group had lost its supervisor while retaining a 6,504,920 KiB
effective allowance. Its remaining server processes measured approximately
119,888 KiB. The displayed original request was only 1 GiB. This is a concrete
way one remaining session can wait behind another session's abandoned work.
It does not establish the cause of every historical headroom/sampling interval.

The watchdog now renews observation leases for identity-checked orphan groups.
The ordinary complete, fresh adaptive window can then retire old peaks; it cannot
shrink an explicit request or an uncertain group. A synthetic reproduction retains
the old allowance through 60 seconds, reaches the existing 512 MiB floor only
after the full window, and admits a waiting 1 GiB request with the same headroom,
pressure policy and host sample. No reservation is deleted to achieve admission.

Cleanup independently requires verified originating-agent loss, two minutes of
continuous eligibility, known development-server commands, no escaped/unknown
children, no overlapping registered job, usable lifecycle/network evidence, no
active clients and no live session or pin. Live origins remain protected even if
their project changes. Command changes restart the grace. Plans expire and
revalidate before both TERM and KILL through `mc_kill_pids`. Legacy missing origin
metadata retains cleanup protection. Claim/pin protection also applies to ordinary
cleanup; configured oversized-child limits and supervisor cancellation still apply.

Relevant reports: #200, #226, #230, #232, #239, #252, #256, #257.

## Inspection and remote-control delays

Eighteen recovered command examples classified as managed on immutable v0.18.4
now classify as native inspection or receive runtime argument checks without a
workload reservation. The comparison is routing evidence, not a latency benchmark.
Private project paths, command arguments and transcripts are not published.

| Reports | Reproduced form and repair |
| --- | --- |
| #241 | Bounded PostgreSQL catalog SELECTs, with psql startup scripts disabled |
| #242 | `memcap run --help` remains usable without admission |
| #243, #245, #254 | Finite remote log helpers, including validated `if`/`case` branches and positional arguments |
| #244, #246 | Hosted Benmore docs, checks, finite logs and pulls |
| #248, #249 | Bounded JSON-to-text Python projections, isolated from project imports |
| #250 | Docker's formatted, non-streaming stats |
| #251 | Git searches and grouped literal inspection pipelines |
| #255 | Finite filename loops, nested read substitutions and brace/glob paths |

Fake transports prove identical output/status and exactly one execution per call.
Unknown execution, local builds, dynamic executable names, unknown shell grammar,
streaming processes and oversized inputs retain admission. The classifier grants
no command authorization. No real remote command executes in these tests.

## Peaks, learning and termination reports

#159: observed peaks are now saved before completion; a supervisor lost to a
session limit cannot erase that evidence. Tag arguments no longer erase applicable
history for the same script: a conservative fallback includes script content,
project, executable, workers and dependency fingerprints. Exact learned profiles
take precedence. Automatic requests already waiting also increase when new peaks
arrive; explicit requests do not change. Regression tests prove argument changes
reuse the observation and content/worker changes invalidate it. Paging tests still
use actual counter deltas, page sizes and elapsed intervals, rejecting resets and
changed boot identities. Numeric event version stamps identify the emitting
runner, while existing reservation events expose the allowance trajectory.

First-ever workloads can still exceed a prediction. These are admission estimates,
not a kernel memory cap; the release makes no zero-red-pressure guarantee and
changes no owner-selected limits.

#247: the local audit record identifies configured oversized-child enforcement
(5.17 GiB against a 4 GiB limit). #253: the audit record identifies cancellation
requested by the registered supervisor. A report's activity label does not establish
which process was terminated or who requested it. These reports are resolved with
the verified cause, without weakening either policy.

## Verification

- Grouped ShellCheck plus 29 independent test-file checks; 25 individual Bash parses.
- 630/630 Bats tests with normal HOME, and 630/630 with isolated HOME/no Docker.
- Targeted lifecycle/learning tests, inspection execution tests, scheduler behavior
  and telemetry tests, including final regression fixes.
- Deliberately broken grace, recovery and live-origin guards fail the regression
  suite. The recovered inspection comparison is 0/18 on v0.18.4 and 18/18 here.
- All enforcement tests are dry-run and all state/profile writes use fixtures.

Remote CI and installed-package verification are recorded on the release PR.
The local user-selected pause is preserved. No live process was terminated to
validate cleanup, no other project's job was cancelled, and agent profiles/trust
were not changed. New runners use the installed version; old supervisors retain
their loaded code.
