# Memory-demand routing

The classifier answers a memory question. It does not approve actions or treat
shell execution capability as a reason to reserve gigabytes of RAM.

| Outcome | Behavior |
| --- | --- |
| Lightweight | Original native task mode, deadline and worker settings; no reservation or admission wait. |
| Heavyweight | Shared memory admission, session rotation, supervised ownership and existing worker controls. |

Positive evidence includes known compiler/test/browser/container/simulator
workloads, inspected helper contents, relevant executable Git hooks, package
scripts and previously observed high usage of the exact fingerprint. Command
positions matter: searching for `go build` is not running a build. Remote payloads
are not local workloads. A configured heap ceiling alone is not allocated memory.
Unused recognized function bodies do not count as executed workloads.

Inspection is bounded to regular local text, 256 KiB per input, 32 dependencies,
eight nesting levels and a 200 ms dependency-probe budget. No script or Git hook
is executed during classification. Unresolved code is uncertain lightweight work.
This policy accepts first-run allocation risk; static inspection cannot prove a
general program's future memory use. The existing watchdog and signal safeguards
remain separate from admission.

When the local recorder is healthy, unfamiliar native commands can register an
observation before an exec replaces the shim with the original shell. The shared
collector samples physical footprint with process-start identities. It never
signals native observations or creates scheduler leases. Sampling gaps, short
commands, unavailable identities and unsupported hosts remain unknown. An observed
peak of at least 512 MiB promotes the exact command/dependency/worker-environment
fingerprint for 30 days. Profiles are bounded to 2,048 entries. These are observed
lower bounds; low partial samples never demote a known heavy workload.

Native observation is best effort and unavailable when analytics is disabled or
stale. Codex modes that require an action-permission rewrite preserve the original
native request instead. Login shells, zsh and shells with startup environment
scripts also remain untouched, so observation cannot run their startup files a
second time. Their native memory coverage remains unknown. The collector shares
two process-table probes per tick across native jobs and bounds footprint sampling.
No telemetry failure should create an admission wait.

`memcap classify --command 'COMMAND' --cwd DIRECTORY` explains one decision.
`memcap classify --replay CORPUS.json --cwd DIRECTORY --repetitions 20` compares
labeled decisions without running the corpus. Raw local command tracing remains
an explicit, temporary owner-enabled feature; public reports contain no commands.

Existing stable hooks load the new classifier on their next invocation. Previously
generated automatic `run` wrappers reclassify before admission when they have no
explicit resource or memory request. Already-running supervisors retain their
loaded code and ownership. Do not submit duplicates or erase reservations. Legacy
inspection wrappers retain their former guards until the host generates a fresh
call. Claude's updated timeout ceiling requires settings reload; expired tasks
cannot be revived. Installation preserves owner pause, policy and Codex hook trust.
