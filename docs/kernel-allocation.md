# Kernel allocation and script launch mitigation

macOS can retain wired kernel allocations independently of the lifetime of a
developer's processes. A large allocation is not evidence that Docker, a
particular agent or memcap owns it. Queue fairness cannot make this RAM available.

## Evidence and mitigation

[Photon's investigation](https://photon.codes/blog/we-found-a-kernel-memory-leak-in-macos-that-any-shell-script-can-trigger)
reproduced retained kernel allocation during nested script execution. A bounded
local comparison on macOS 26.6.2 also observed more bucket growth with direct
script execution than explicit interpreter controls. Whole-machine background
activity was not isolated; neither result attributes this host's accumulated RAM.

memcap now invokes its known Bash dispatcher through `/bin/bash` for internal
sampling, diagnostics, status, environment lifecycle and cancellation bridges.
Generated lifecycle/admission hooks also use this form. Arguments, configuration
loading, supervisor parent identities and the signal choke point remain intact.
This removes avoidable shebang dispatch on these paths. Arbitrary user workloads
retain their original commands and interpreter choices.

Installed hook updates require `memcap integrate` for the selected profiles and
session reload. The installer recognizes old and new managed hooks, preserves
unrelated hooks and never grants Codex trust. Verify trust independently. Running
queue supervisors retain their original code until they finish; installing the
release does not retrofit their dispatches or justify resubmitting their jobs.

## Recovery and measurement

This mitigation cannot free allocations already retained by the kernel. Recovery
from the reported defect requires rebooting after active work is saved; memcap
does not reboot the machine, restart Docker or terminate sessions to achieve it.
An OS fix is required to remove the underlying defect across all software.

The local recorder samples wired memory and two fixed kernel bucket counters.
Compare the signed growth rate, observed coverage, OS version, boot identity,
release build and workload mix. Keep pre-reboot snapshots: a smaller post-reboot
bucket is not evidence of a memcap improvement. Sustained growth after mitigation
requires further attribution, not another round of unrelated process cleanup.
