# Pressure accounting follow-up, v0.18.1

On September 28, v0.18.0 recorded 15 critical-pressure samples between 11:13:37
and 11:27:28 PDT. The last managed admission before these observations was at
11:09:49; none followed until 11:30:30. The watchdog was running v0.18.0 and
declined mobile cleanup for active tooling, visible Simulator use and idle-grace
protections. These observations are not proof of continuous red pressure or of
which process caused it. Issue #159 remains the incident tracker.

The release comparison and failing synthetic tests identified two defects:

1. Job accounting used only the supervised process group. Descendants that create
   another group/session were excluded from its peak and growth allowance even
   while the command continued running. Global host measurement could still see
   them, but job-specific learning and allowance growth understated demand. This
   defect predates v0.18.0; the incident does not establish a newly introduced leak.
2. Shared observations could be consumed out of order. An older yellow observation
   without paging could erase paging history established by a newer observation.
   v0.18.0 added concurrent cache reuse during early refresh, making correct ordering
   particularly important. The test proves the defect, not that this race caused
   the photographed pressure spikes.

v0.18.1 separates memory attribution from process control. A same-user descendant
can be measured outside the original group, with remembered start identity so it
remains attributed after reparenting while the job remains registered. New managed
groups take precedence over inherited attribution. PID reuse and foreign owners
do not inherit attribution. Missing descendant measurements cannot lower the
existing allowance. Foreground completion with surviving attributed work makes
the learning observation incomplete, allowing only upward learning.

Cancellation and completion still use the original supervised group. The patch
does not signal detached children, extend cleanup authority, reset simulators,
restart Docker, change configuration or impose a new concurrency cap. Delegation
through a daemon (including Docker and CoreSimulator), or a child that detaches
before any ancestry observation, remains outside proven per-job attribution.

The shared controller now ignores older/same observations from the same boot;
different boot identities still reset it. Fresh pressure checks, sample deadlines,
yellow admission and the lightweight path are unchanged. Old supervisors retain
loaded code; new runs use the installed update. Concurrent old runners can still
write old accounting until they finish. Installation does not cancel them.

Regressions use synthetic ownership/identity tables and memory amounts, not a host
memory stress test. Incident closure requires subsequent live evidence, not just
passing tests or a version bump. No zero-red guarantee is made.
