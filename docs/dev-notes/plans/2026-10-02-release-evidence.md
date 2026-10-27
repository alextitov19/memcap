# Release evidence and learning

The v0.22.0 overnight review found shorter median waits but a much worse tail:
one 17-second Go build waited 3h50m with an explicit 2 GiB reservation. Of 63
completed project jobs, 29 requested fixed memory and 49 lacked complete learning
evidence. Those unmatched windows are motivation, not a causal comparison.

Implement fixed-request observation learning at launch using actual worker
identity, preserving explicit admission floors and incomplete-sample safeguards.
Record numeric reasons learning was incomplete; do not reinterpret missing
process samples as complete. Explain automatic sizing to future sessions.

Preserve baseline events outside rolling retention and compare by recorded
version, exact build and policy. Separate completed waits, pending/cancelled work,
memory pressure, observer overhead and diagnostic coverage. Never use install
time to relabel old runners, publish raw commands or certify an improvement from
unmatched workloads. Fix the misleading zero-headroom message during sampling.

Validate with synthetic snapshots and process identities, deliberate negative
controls, ShellCheck, per-file Bash parsing and both complete Bats configurations.
Perform one final whole-diff review, publish v0.23.0 and install normally through
the live queue. Save install provenance alongside the before/after archives.
