# Admission and productivity recovery

Approved scope: all five recommendations from the October 1 overnight review.
Preserve measurement, signal authorization, red-pressure protection, explicit
reservations, owner-selected policy, and live jobs. No unrelated cleanup.

Baseline (00:12–10:10 PDT, v0.21.1): 33 completed project jobs, median wait
87.644 s, p95 1132.363 s; one 4.078 s Go test waited 1132.363 s. Two pending
jobs waited 11 h and 9 h 36 min. 705 wait calls. No recorded red samples.
19 monitoring jobs excluded from project figures. These are observations,
not matched-workload proof of causation. Private database and queue-log copies
are under ~/.local/share/memcap/benchmarks/before-throughput-2026-10-01/.

Implementation sequence:
1. Retain compact job lifecycle evidence separately from high-volume samples;
   report pending age, monitoring cohorts, latency, amplification and coverage.
2. Reclassify automatic wrappers, including argv invocations. Explicit memory
   and resource requests retain admission. Split only proven literal sequential
   shell commands, preserving operators/cwd/exit status; unsupported syntax keeps
   whole-command admission. Reclassify each stage at execution time.
3. Use complete exact evidence for the selected worker configuration to reduce
   a pending estimate; incomplete evidence may only increase it. Fingerprints
   include workload source so changing tests invalidates small estimates.
4. Detect sustained headroom deficits without draining finite jobs. Report
   required/available memory and evidence-based alternatives; retain the job.
5. Add a single long-lived completion wait for notification-capable hosts and
   bounded fallback waits for hosts/Stop hooks that cannot resume automatically.
   Do not loosen session ownership or infer success from registry disappearance.

Owner expansion: clean verified disposable resources after sessions finish, and
avoid a live session's idle stack blocking its own queued test. Implement an
atomic environment workflow: reserve stack plus test first, create a unique
Compose project only after admission, run tests inside that lease, and stop the
owned containers afterward. A dead worker/agent leaves a private identity record
for fresh watchdog recovery after continuous grace. Shared/pinned stacks retain
protection. No Docker Desktop restart or data-volume removal. Do not automatically
hibernate a live session's untracked stack: dependencies and ownership are unknown.
This prevents the resource hold-and-wait condition for the supported workflow
without risking another session's services or repeatedly restarting dependencies.

Validation: targeted synthetic tests and negative controls, ShellCheck, individual
Bash syntax parses, full Bats suite, isolated-HOME/no-Docker suite through live
memcap admission. One final whole-diff review. Do not claim overnight improvements
until another comparable real run supplies measurements.
