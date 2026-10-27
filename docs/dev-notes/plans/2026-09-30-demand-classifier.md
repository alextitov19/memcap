# Memory demand classification redesign

Owner-approved contract: two outcomes, lightweight and heavyweight. Lightweight
calls execute without workload admission or reservations. Heavyweight calls retain
the existing shared admission, ownership and signal safeguards. Unknown syntax is
not evidence of substantial memory demand. This policy accepts first-run unknown
workload risk; observation cannot prevent every fast allocation.

Baseline is private and outside Git at
`~/.local/share/memcap/benchmarks/before-classifier-redesign-2026-09-30T213900-0700`.
It preserves a consistent SQLite backup, all retained events, queue logs, issue
snapshots, installed source and policy. Recorder coverage stopped before capture;
separate incomplete coverage and paused cohorts from active observations.

Implementation sequence:

1. Replace the hook's proof-of-nonexecution classifier with positive memory-demand
   evidence: actual command positions, shell/interpreter wrappers, local package
   scripts, relevant Git hooks and measured workload history. Preserve original
   commands and host foreground/background/deadline semantics for native work.
2. Add read-only classification explanations and a local replay benchmark. Test
   ordinary command variants and heavy commands embedded in the same wrappers;
   never execute replayed commands. Preserve private commands locally.
3. Observe unfamiliar native work without queue registration or signal authority.
   Use identity-checked physical footprint; incomplete observations can establish
   high usage, never prove a low peak. Observation failures cannot delay commands.
4. Connect classification evidence and queue/runtime ratios to local analytics;
   repair the recorder's cap recovery. Keep public reports sanitized.
5. Retain heavy-job fairness, reservation and supervisor-lock repairs. Reconcile
   legacy runners explicitly; current source must not imply old processes reloaded.
6. Run negative controls, targeted behavioral tests, the required full normal and
   isolated suites, then one final whole-diff review. Publish only with green CI.
7. Install with original-byte backups, preserve pause/config/trust and unrelated
   profile settings, enable owner-authorized local command capture, and save the
   after artifacts beside the baseline. Report replay results separately from
   production outcomes; a paused live run does not prove active admission latency.

Acceptance: every labeled lightweight replay stays out of admission even under
red pressure/full queues; every labeled heavyweight replay remains managed;
classification does not grant action permission; original output, exit status and
single execution are preserved; no queue or cancellation ownership is fabricated.
