# Orphan recovery

The watchdog must maintain abandoned queue groups independently of the original
supervisor. An unchanged, user-owned group can receive a short renewable watchdog
observation lease. This does not change signal ownership or erase reservations.
Admission can use its normal complete, fresh measurement window while that lease
is valid; unknown or changed groups retain their allowance.

Cleanup requires repeated owner-loss observations spanning two minutes, a known
development-server group, no unknown descendants, no live session claim, no pin,
and a successful network check with no clients. Missing lifecycle evidence retains
the helper. Plans expire after 30 seconds and revalidate before TERM and KILL via
`mc_kill_pids`. Pause stops maintenance and authorization. Registry removal follows
confirmed group exit through the existing scheduler refresh.

`memcap claim JOB_ID` protects a reused resource for the calling agent's verified
process lifetime. `--pin` protects it until explicit `--unpin`. Queue output shows
requested and effective memory, last measured memory, ownership and cleanup vetoes.

Implementation order: synthetic regression tests; recovery planner and signal
bridge; scheduler/CLI/watchdog integration; remaining report reproductions and
fixes; one final diff review; full normal and isolated gates; CI, release, install.

Do not infer that a low-memory orphan caused every reported wait: headroom,
measurement freshness, classification, process termination and first-use peaks
require separate evidence.
