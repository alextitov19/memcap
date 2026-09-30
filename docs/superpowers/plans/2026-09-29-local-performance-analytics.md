# Local Performance Analytics Implementation Plan

> Execution: implement in this session. The user's repository instructions require one final whole-diff review and no automatic commits.

**Goal:** Measure developer delay, machine health, and measurement quality locally across current and future sessions, including owner-paused operation.

**Architecture:** Existing Python hook/runner processes send sanitized nonblocking datagrams to one local SQLite recorder. Reports read bounded snapshots; optional Claude OTLP logs use a bounded authenticated loopback receiver. Analytics has no admission, cleanup, or policy authority.

**Tech Stack:** Existing Bash/Python standard library, SQLite WAL, launchd, unittest/Bats.

**Spec:** `docs/superpowers/specs/2026-09-29-local-performance-analytics-design.md`

## Global constraints

- Preserve the owner's enforcement pause and native execution semantics.
- No commands, prompts, output, paths, credentials, or raw agent payloads in analytics.
- Retain unknown outcomes; Stop is an attempt, background acknowledgement is not completion.
- Producers never wait for the collector. Collector failure must not affect work.
- Tests use isolated state/config/HOME and dry-run enforcement; heavy verification uses live `memcap run`.
- Raw retention 14 days, aggregate retention 365 days, total storage budget 256 MiB.

## Review focus

- Missing and reordered endpoints must not create fabricated durations or successes.
- Existing native exporters must survive installation unchanged.
- Same-version local patches and old running supervisors need distinct provenance.
- Parallel waits, sleep, and clock changes must not inflate developer delay.
- Full storage, missing recorder, and malformed secret-bearing events must leave execution intact.

## Tasks

- [x] **Producer and schema:** `libexec/analytics_events.py`; tests in `tests/test_analytics.py`. Fixed vocabulary and numeric fields, keyed IDs, immutable loaded build/config identity, nonblocking transport, bounded packets, sequence/drop accounting. Verify secret omission and absent receiver before wiring producers.
- [x] **Recorder:** `libexec/analytics_store.py`, `libexec/analytics_collector.py`. Single writer, idempotent events, indexed timelines and histogram rollups, bounded retention/checkpoints, cheap host samples, heartbeat/coverage, legacy import with explicit missing provenance. Test duplicates/reordering, byte caps, clock ambiguity, and unknowns.
- [x] **Integration:** scheduler lifecycle/routes, existing lifecycle feedback, verified signal outcomes, optional OTLP adapter in `libexec/analytics_otlp.py`. Stable hook command strings preserve current trust. Tests cover paused/native routes, incomplete jobs, API field allowlisting, and background acknowledgements.
- [x] **Reports and experiments:** `libexec/analytics.py`, `libexec/analytics_reports.py`. Today/explain/compare, work acceptance markers, immutable benchmark manifests and randomized paired fixture runner, local self-contained HTML. Test parallel interval union and a deliberately slow regression control.
- [x] **Installation:** `libexec/analytics_install.py`. Private state and independent LaunchAgent, transactional Claude telemetry environment installation without overwriting exporters, capability/observed-coverage doctor. Tests use fake profiles/service and verify idempotence and pause preservation.
- [x] **Delivery:** document commands and limitations, run targeted analytics and compatibility checks, measure producer/whole-hook overhead, then one final diff review and all four repository gates. Install backed-up local files and recorder; verify live recording and future profile configuration. Report current-session native telemetry limitations truthfully.

Verification and installation evidence: `docs/analytics-validation.md`.
