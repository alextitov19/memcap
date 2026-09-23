# Adaptive throughput validation — 0.16.1

## Incident and design evidence

The September 23 incident had 15–17 waiting requests, zero managed running jobs,
a 20-GiB cap and blanket 2-GiB requests. Approximately 18.5 GiB was already charged.
That arithmetic prevented the first launch, so post-launch reservation adjustment
could never help. Slow host sampling also held the shared admission lock.

After the owner paused enforcement, one measured snapshot was 22.54 GiB charged,
3.60 GiB estimated available, and yellow pressure. A separate 30.9-second sample
contained fourteen yellow and two green observations, zero red, and approximately
32.6 MiB/s swap-out. This does not establish five uninterrupted minutes without
red or a particular real-build throughput gain.

Apple describes pressure as a combination of free memory, swap rate, wired memory
and cached files, not a sum of process sizes. Docker describes the settings slider
as the VM memory allocation. Container stats, VM accounting and the host footprint
are different layers. Adaptive admission therefore uses pressure and physical
headroom in addition to reservations rather than discarding Docker accounting or
subtracting compressed memory from a footprint.

Sources: [Apple Activity Monitor memory usage](https://support.apple.com/guide/activity-monitor/view-memory-usage-actmntr1004/mac),
[Docker Desktop resource settings](https://docs.docker.com/desktop/settings-and-maintenance/settings/).

## Controlled experiment

`tests/benchmark_admission.py` runs five owned Python subprocesses, each allocating
16 MiB, hashing it, and waiting eight seconds to represent an external service.
The **host memory observations are fixtures**, held identical between policies:
17 GiB charged, 4.5 GiB available, yellow pressure. Three pairs alternate order.
Strict uses the previous local profile: eight slots, two workers, 2-GiB requests,
2-GiB headroom. Adaptive uses twelve slots, eight maximum workers, 1-GiB requests
and its physical emergency margin. Neither launches a real Docker/browser/build
stress test. Everything runs through the installed live queue with isolated state.

| Pair | Strict completion | Adaptive completion | Strict p95 wait | Adaptive p95 wait |
| --- | ---: | ---: | ---: | ---: |
| 1 | 41.626 s | 19.069 s | 33.055 s | 10.605 s |
| 2 | 41.803 s | 18.820 s | 33.291 s | 10.135 s |
| 3 | 41.682 s | 19.159 s | 33.169 s | 10.648 s |

Median completion: **41.682 → 19.069 seconds**, about **54% less elapsed time**
and **2.19× completion throughput** in this fixture. All thirty jobs finished;
peak concurrency was one versus four. No model polling turns were needed inside
the benchmark. This isolates admission overhead; it does not prove that CPU-heavy
real builds get faster or that the Mac can support a specified amount of RAM.
An additional eight-session adaptive run completed all eight jobs in 25.328 seconds,
with 16.721 seconds worst queue wait and four concurrent jobs.

Sampler CPU, real-build throughput and per-policy real pressure duration were not
measured in this fixture. Live enforcement remains paused by the owner.

## Correctness and regression coverage

- `test_admission.py`: footprint above target at yellow, strict compatibility,
  red/unknown/stale measurements, explicit reservations, outstanding growth,
  slots, startup spacing, recovery, and accumulated swap versus current traffic.
- `test_adaptive_scheduler.py`: actual concurrent supervisors finish exactly once,
  sampling occurs outside the registry lock, status never locks/writes, busy
  sampling cannot reset recovery, worker sharing, private estimate identities,
  bounded backfill, and retention of observed peaks through later memory lulls.
- `test_scheduler_metrics.py`: 4-KiB/16-KiB page conversion, elapsed-time rates,
  missing/reset/reboot counters, private numeric-only events and age retention.
- `test_workload_estimates.py`: recent peaks, gradual reductions, incomplete data,
  larger observations and private workload/worker identities.
- Existing scheduler/productivity suites cover five simultaneous jobs, session
  rotation, stale waiter cleanup, retained surviving groups, cancellation identity,
  lightweight search/read/status/SSM wrappers, cached-wrapper reclassification and
  `memcap wait --session` without another reservation.
- New dry-run watchdog regression: above-target green/yellow/unknown pressure does
  not select tier 2; red reaches the existing eligibility gates. No new processes
  become eligible for termination. All existing orphan, minimum-age, safe-root,
  TOCTOU, agent-CLI, ancestry and pause protections remain.

Two deliberate negative controls restored the hard-cap decision and moved sampling
under the admission lock in memory. The respective regression tests failed with
an assertion failure and lock error; production files were not changed by the
controls. Initial focused checks found a recovery-fixture exhaustion and a test
using a future timestamp; these failures were corrected and are not counted as
passes. The first complete Bats run also failed an old cache-location fixture and
the changelog version-prefix guard. Both were fixed and their targeted checks
passed; final full-suite and CI results are recorded with the release.

## Issue coverage and evidence

After the first adaptive release, local incident transcripts supplied sanitized
reproductions missing from the numeric reports: bare `*.go` searches, a fixed
directory-status loop, `ps` diagnostics, and `f=$(rg -l ...); sed ... $f`. Version
0.16.1 adds an expanded-argument guard for these reads. Raw transcripts, project
paths and commands are not included in public reports. Some reported scripts also
contained real builds/deployments; these remain managed, with adaptive admission
addressing the capacity stall rather than pretending those scripts are all reads.

| Reports | Disposition / evidence |
| --- | --- |
| #31, #34, #39 | Sampling outside the registry lock; status is read-only. Lock contention tests exercise both boundaries. |
| #37, #42 | Adaptive mode removes the reproduced strict-budget zero-running stall; concurrent-run and five-session throughput tests cover progress. |
| #43 | Owner-authorized maintenance exception shared across runtime and installed guidance. |
| #27, #35, #41, #45 | Read-only session wait, local Stop-hook waiting, and explicit no-managed-work guidance avoid drain jobs and loops over unmanaged native tasks. Memcap cannot install a missing native polling tool or certify its task outcome. |
| #28–30, #32–33, #38, #40, #44 | Recovered inspection forms now execute without a reservation after argument validation; SSM control and cached-wrapper regressions remain covered. Actual mixed build scripts retain admission. |
| #36 | Busy/failed host sampling keeps the waiter registered and retries internally, rather than exiting the task. A report-time fault-free snapshot cannot identify the earlier probe failure; this release fixes the interruption behavior, not every possible OS probe failure. |

Report #47 (Docker-ceiling confusion) is additionally covered by shared guidance tests: no
reservation arithmetic from the ceiling, no promised reclaim by subtracting
container usage, and no manual hard target applied while the user paused admission.

`test_inspection_expansion.py` exercises real shell expansion and execution with
sandboxed state, including a filename that expands to an execution option. The
unsafe command must reach a recording admission fallback without running the
helper. Safe inspection must preserve output/status and create no queue registry.
A negative control removed this argv check in a temporary copy: the helper ran
and the regression failed as intended. Production source was not changed.

## Migration and limits

New `init` uses adaptive; omitted policy on existing installs remains strict.
Upgrades preserve pause state and Docker settings. New invocations load the new
runner. Existing supervisors keep their loaded version; queue summary counts them.
Do not resubmit duplicate work to upgrade an already-running supervisor.

Workload sampling is periodic. Short unobserved jobs do not teach estimates;
completed, fully sampled successful runs can lower them gradually. Cache-directory
presence and executable metadata are practical identity hints, not perfect build
cache invalidation. Arbitrary wrappers keep the configured prior. Explicit memory
allowances, orphan survival, user-selected cleanup and oversized-process limits
remain independent controls. Red prevents new managed heavy starts, not memory
growth inside running or unmanaged processes.
