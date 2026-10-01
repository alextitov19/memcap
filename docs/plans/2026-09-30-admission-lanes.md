# Admission lanes

Implement two managed lanes under the existing atomic registry, physical headroom,
pressure checks, reservation accounting, and global concurrency limit. Verified
inspection and remote control remain direct. No new cancellation/signal authority.

- Small: automatic exact workload profile with at least three complete runs,
  reservation at most 1 GiB, no known heavy command or persistent resource.
  Explicit memory requests do not constitute measured small-workload evidence.
- Heavy: unproven work, known builds/tests/mobile/browser work, resources, legacy
  runners, and jobs whose demand grows beyond the small threshold.
- At most two small finite jobs within the owner's existing global slot limit.
  Prefer three small admissions then a fitting heavy request. Preserve session
  rotation inside lanes and aged-heavy drain windows while finite work can drain.
- Preserve all reservation floors, sample freshness requirements, and pause rules.
  Hash script contents into exact profiles before granting learned priority.
- Expose lane and per-lane delays in private queue diagnostics and local analytics;
  historical lane coverage remains unknown. Existing supervisors retain loaded code.

Validation: failing behavioral controls first, targeted scheduler/analytics suites,
one final whole-diff review, required shell checks plus normal and isolated-HOME /
no-Docker full Bats suites. Install a backed-up local patch only after checks pass.

User follow-up: preserve needed queued jobs for up to 24 hours. Extend default
admission patience to 86,400 seconds; managed Claude tools use background mode and
86,400,000 ms outer timeouts. Explicit runner deadlines remain authoritative.
Set the selected Claude profiles' allowed maximum using transactional integration;
retain native-command defaults and short polling/hook deadlines. Update guidance
for current and future sessions, including foreground-subagent lifecycle limits.

Live-wait investigation: a recovered registered group retained an 8.154 GiB
reservation while measuring about 118 MiB; a subsequent fresh window reduced it
to 512 MiB. `Scheduler.reservation` seeds a restarted window with the lifetime
peak, which can resurrect a retired peak after a sampling gap. Three behavioral
regressions were added to `tests/test_adaptive_scheduler.py`; the existing queued
validation workflow must execute them as negative controls before implementing
the accounting repair. Preserve lifetime learning, real/partial growth, strict
and explicit floors, uncertain-orphan protection, and the full fresh window
before any further reduction. Do not modify live leases or cancellation authority.
The regressions reproduced four failures (three methods) in the full suite.
The accounting repair now preserves a separate admission peak after retirement,
retaining the lifetime peak exclusively for learning. Final checks are pending.

The live validation supervisor also exited on registry-lock contention while its
already-started child continued. Retain running supervision across typed lock
timeouts, without retrying a damaged registry or losing cancellation ownership.
A bounded real-lock/real-child regression and a corruption negative case cover
this distinction; run the same regression against the saved pre-fix module too.
