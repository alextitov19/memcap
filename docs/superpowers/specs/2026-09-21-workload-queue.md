# Shared workload queue

Approved intent: Claude/Codex may request five expensive jobs, but only work
that fits a shared machine budget should start. Keep at most two finite jobs
running, constrain common worker pools, and let small read/edit operations
continue. Existing cleanup protections remain the backstop.

## Design

`memcap run [--resource NAME] [--memory GB] [--wait SECONDS] -- COMMAND...`
uses a private, per-user registry and an OS file lock. Python 3.9+ standard
library code owns admission and supervision; the existing Bash CLI/config and
footprint sampler remain authoritative. Python is required only for scheduling.

Each request reserves 2 GiB by default. FIFO admission across sessions, a
two-job ceiling, normal macOS pressure, and 3 GiB host headroom all gate launch.
Admission is serialized through launch and registration. Account for each active
workload as max(reservation, observed footprint), subtracting its already-counted
tracked memory to avoid double charging. Unknown measurements admit nothing.
No automatic retry of failed commands. Waiting expires after 30 minutes by
default with a distinct nonzero status and no launch.

Each child gets its own process group. A foreground supervisor preserves streams
and exit status and retains the lease while ordinary background descendants live.
Resource leases do not consume finite-job slots but still reserve memory.
Canonical project plus resource name prevents duplicate registered dev servers;
report the existing job rather than launching another. Double-fork/setsid and
external simulator/VM ownership remain outside process-group containment; host
pressure and the existing classifier still account for their demand.

Cancellation is limited to the scheduler's recorded process identities and goes
through `mc_kill_pids`; agent CLIs and memcap ancestry remain protected. Waiting
cancellation never starts the job. A dead supervisor with surviving children
keeps consuming capacity. Unknown/stale process identity fails closed.
`memcap off` bypasses admission for newly submitted commands and stops scheduler
cancellation enforcement, consistently with the existing kill switch.

`memcap agent-hooks AGENT --queue` emits opt-in PreToolUse integration that
rewrites shell calls through the runner. The hook never waits for capacity.
Common safe file operations bypass; unknown/compound commands queue. Hook
rewrites preserve shell text, cwd, supported shell selection and tool options.
Live configuration is not edited. Hooks are a guardrail, not a universal spawn
interceptor. `memcap queue` exposes queued/running/resource/abandoned workloads.

Inherited environment caps common Go/Rust/CMake/BLAS/Vitest pools; recognized
direct Jest/Vitest/Playwright invocations and simple npm/pnpm/yarn scripts get
explicit worker arguments. Complex scripts retain their semantics and inherit
environment limits; arbitrary programs cannot be guaranteed a worker bound.
Nested managed work shares its validated ancestor lease, avoiding slot deadlock.

## Safety and verification

All runtime experiments and tests use sandbox config/state and MC_DRY_RUN=1.
No real Docker operations, simulator boots, services or global configuration
changes. No auto-commit. Existing ShellCheck/Bats coverage is present; Python
tests run through Bats using unittest, with no added package dependencies.
Verify concurrency with real short-lived fixture jobs and independently recorded
start/end events. Prove the admission guard fails a negative control. Run all
four required repository checks and perform one final whole-diff review.
