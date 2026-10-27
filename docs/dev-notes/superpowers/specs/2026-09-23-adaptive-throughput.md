# Adaptive throughput: owner-approved design

## Outcome

Keep Claude, claude-p and Codex sessions productive on a shared Mac. Optimize
completed work and interactive responsiveness, accept yellow memory pressure,
and stop additional heavy admissions at red. Fifty percent more useful parallel
work is the owner's hypothesis to evaluate, not a promised improvement. Avoid
requiring agents or users to repeatedly tune reservations or babysit queues.

The owner authorized development, testing, releases in the source and Homebrew
tap repositories, and local installation. This document records the design
discussed on September 23. The implementation plan is
[adaptive throughput plan](../plans/2026-09-23-adaptive-throughput.md).

## Evidence and limits

- Installed release at investigation: 0.15.1. Configuration: total 20 GiB,
  eight finite slots, two workers, 2 GiB default requests, 2 GiB host headroom,
  yellow pressure allowed.
- Verified snapshots had 15–17 waiting jobs and zero managed running jobs.
  Approximately 18.5 GiB was tracked. All waiting requests were 2 GiB.
- Replaying admission against one unchanged snapshot rejected 2 GiB and admitted
  1 GiB. This establishes an admission bottleneck, not actual job requirements.
- Existing automatic reservation adjustment begins after launch. It cannot
  correct an overestimate preventing the first launch.
- Docker's VM process had a 10 GiB charged footprint plus Desktop helpers.
  Docker reported much lower container and VM usage. `vmmap` included substantial
  compressed/swapped accounting. These metrics must not be equated or naively
  subtracted: footprint, RSS, guest usage and configured capacity differ.
- The owner paused enforcement at 10:59:30 local time. A later sample measured
  22.54 GiB combined footprint, 3.60 GiB estimated available memory and yellow
  pressure. The registry was empty; paused launches need not register.
- Sixteen readings across 30.9 seconds contained fourteen yellow, two normal,
  and no red readings. Average swap-in/out rates were 14.740/32.596 MiB/s.
  Completion throughput and uninterrupted five-minute pressure history were
  not recorded. Absence of red in that sample is not proof of sustained safety.

## Policy

Introduce `QUEUE_POLICY=strict|adaptive`. Existing installations retain their
explicit policy until an owner-authorized migration; new setup explains the
tradeoff. The requested local target is adaptive. Preserve the owner's current
pause state through installation and migration.

Strict mode retains the existing cap, reservation and headroom contract.
Adaptive mode treats charged footprint and the configured total as planning
signals rather than an unconditional physical-RAM veto. It combines workload
estimates, system pressure, recent growth, paging activity and a shared launch
window. This policy distinction must also reach watchdog decisions and status;
the watchdog must not undo adaptive admission solely because a soft footprint
target was exceeded.

Adaptive mode is not unrestricted execution. Required pressure and measurement
signals must be fresh and valid; red blocks new managed heavy work. Explicit
memory requests retain their meaning as scheduling allowances. Uncertain job
identities do not release reservations. Unknown resource demand does not become
zero demand. No mode promises that already-running or unmanaged work cannot
push the host into red.

### Workload demand

Recognize bounded lightweight operations without requiring a heavy reservation.
Keep complete shell semantics and check every pipeline/compound stage. Do not
approve arbitrary Python, shell scripts, substitutions or hooks merely because
an outer command looks lightweight.

Record local workload identities using project, executable/tool version,
subcommand, relevant build/test flags, worker count and cold/warm conditions.
Use private keyed fingerprints rather than public command strings. Unknown
wrappers remain unknown; agents cannot declare arbitrary commands cheap.

Initial managed-work priors distinguish small known tasks, heavy known tasks
and unknown tasks. Candidate unknown allowance is 1 GiB with one uncertain
startup admitted at a time; this is an experiment, not a discovered safe bound.
Known heavyweight commands retain appropriate larger priors. Explicit requests
are never silently reduced. Learn complete-run peaks, not just startup usage.
An initial estimator uses the largest recent peak until twenty comparable runs
exist, then a recent upper percentile with a margin. Never lower an estimate
because a run was cancelled, measurement was incomplete, or ownership was lost.
Raise estimates promptly after an underestimate; lower them gradually.

### Shared admission control

One per-user control state coordinates all supervisors. Reserve every admission
atomically, including anticipated growth not yet visible in host measurements.
Measure each process group once and count Docker once; do not sum container
usage on top of the VM. Keep long-lived resources separate from finite slots.

Use a shared sampler, not one full host scan per waiting job. Acquire host
measurements outside the registry's critical section, then validate their age
and policy revision inside it. Pressure is checked immediately before launch.
Use a separate sampler election/lease so simultaneous waiters cannot cause
duplicate scans or share unreserved capacity. Publish heartbeat and completion
events without holding locks during waits, network calls or child execution.

Candidate adaptive startup parameters: a two-second launch observation interval,
one unknown startup token, and a maximum of twelve finite jobs on this host.
The latter is a safety ceiling, not an instruction to fill twelve slots.
Repeated comparable stable samples permit incremental growth. Worsening pressure
or sustained paging plus declining responsiveness reduces future admissions.
Avoid using accumulated swap size alone as a stop condition. Recovery requires
a stable interval, not a single good sample. Validate numerical thresholds
through controlled mixed-workload trials before choosing released defaults.

Keep charged-footprint planning and physical-state checks distinct. Do not add
overlapping VM page categories as if they were independent free memory. Any
available-memory estimate must document its inputs, uncertainty and page units.
Pressure-only, RSS-only and container-only admission are all insufficient.

### Fairness and waiting

Round-robin eligible admissions by session, with age and memory-time accounting
to prevent a prolific submitter dominating. Backfill fitting work while reserving
progress opportunities for aged larger jobs. Do not promise bounded wait when
background/unmanaged workloads leave insufficient capacity.

An aged job may accumulate capacity while finite work drains, but must not
indefinitely block smaller work when nothing can drain. Zero managed running
jobs plus prolonged waiting triggers explicit stall diagnosis and estimate
re-evaluation. An exploratory launch still needs valid pressure, shared startup
credit and a bounded uncertainty allowance; age alone never grants admission.

The queue remains responsible for waiting. Agents use existing task handles,
receive launch/completion/cancellation results and poll at most once per minute
when needed. Memcap internally responds promptly to capacity changes. Status,
wait and reporting do not acquire the admission lock or reserve workload slots.
No invented tool names, recursive waiting jobs, duplicate resubmissions or
Stop-hook loops. Explicit cancellation is respected.

### Workers and cleanup

Allocate supported tool worker limits at launch from a shared CPU/memory budget.
A lone build may receive more workers than one of several concurrent builds.
Preserve smaller explicit limits, command argument boundaries and unsupported
program semantics. Do not claim to resize already-running worker pools. Learned
memory estimates must be conditioned on the worker allocation.

Retire stale leases only after fresh process identities demonstrate the owner
and managed group exited. Retain uncertain/orphaned surviving groups. Duplicate
suppression distinguishes retransmission of one tool request from intentionally
running the same command twice. Existing dev-server/resource reuse remains.

Use existing lifecycle GC for proven abandoned helpers, retaining its ownership,
active-work, network, age and simulator gates. Keep `GC_MODE=observe` by default.
Low CPU or an idle-looking Docker builder is not permission to terminate work.
All termination continues through `mc_kill_pids`.

## Diagnostics and delivery

Explain each admission using observed footprint, estimated demand, unobserved
growth allowance, policy, pressure/paging trend, slot/worker availability and
fairness. Store bounded private event history with numeric counters. Public
reports remain allowlisted, consent-based and deduplicated. No publication quota
or retry cooldown is reintroduced. Never publish raw commands, paths or PID rows.

Old supervisors may retain old code. Version the protocol and policy records;
support safe mixed-version operation or clearly require draining old supervisors.
Do not silently reinterpret an old lease under weaker assumptions. New sessions
must learn the policy through installed hooks without manual instructions.

Validate unit contracts, concurrent atomic admission and controlled mixed-workload
performance. Report before/after completion time, queue delay, fairness, paging
and pressure duration. Synthetic replay cannot certify host throughput. Perform
the four repository checks, one final whole-diff review, exact-head CI, release
both repositories, test Homebrew installation and verify all three agent profiles.
Close issues only when their observations have been reproduced and addressed.

## Research basis

- [Apple: footprint includes compressed/swapped memory](https://developer.apple.com/videos/play/wwdc2022/10106/).
- [Apple: memory pressure combines multiple system signals](https://support.apple.com/guide/activity-monitor/view-memory-usage-actmntr1004/mac).
- [Kubernetes VPA: historical peaks and recommendation margins](https://github.com/kubernetes/autoscaler/blob/master/vertical-pod-autoscaler/docs/flags.md).
- [Netflix: adaptive concurrency feedback](https://github.com/Netflix/concurrency-limits).
- [Slurm: backfill scheduling and estimation limits](https://slurm.schedmd.com/sched_config.html).

These are design precedents, not evidence that their thresholds transfer to macOS.
