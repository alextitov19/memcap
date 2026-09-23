# Paused execution and inspection regressions — v0.16.2

All seven reports (#49–55) were collected while enforcement was paused, with no
managed running/waiting jobs. Those snapshots alone do not prove the original
cause or duration. Local incident transcripts recovered the preceding command
forms without publishing private paths, command bodies or logs.

The shared reproduction showed that a paused PreToolUse hook still changed task
mode to background and injected queue-wait instructions. The runner then executed
without a lease, so memcap wait correctly found nothing to wait for. Paused runs
also acquired the registry lock and rewrote worker flags/environment. These are
real defects even though the owner had already removed the memory-admission cap.

| Reports | Fixed behavior |
| --- | --- |
| #49, #53 | Paused hooks leave native task mode alone; state-change guidance distinguishes native tasks from managed waiting. Empty Stop hooks do not block completion. |
| #50 | Finite AWS log inspection avoids heavy admission when active; paused mode never wraps it. |
| #51 | Literal repository aliases and GitHub workflow API control remain native. |
| #52 | Checked pathname brace expansion reads the original files without reserving memory. |
| #54 | Paused commands retain original worker arguments/environment, including a registered waiter released by pause. This fixes hidden throttling; it does not prove all 900 reported seconds were caused by memcap or that the application tests succeed. |
| #55 | A single-file cleanup followed by inspection avoids heavy admission; true builds remain managed when active. |

`test_pause_contract.py` runs real sandboxed CLIs, shell expansion, process
supervision, a held registry lock, and a registered waiter released by a fixture
pause. No real user jobs or Docker services are stopped. The original version
failed five pause assertions and timed out against the held lock; two additional
inspection cases and the pause-transition feedback test also failed before their
fixes. A first focused invocation named a nonexistent Bats file; it ran no intended
checks and was corrected rather than counted as a passing run.

Two temporary-copy mutation controls reintroduced worker capping and forced
background hooks while paused. Both regression tests failed as intended; production
files were unchanged. The active negative controls retain admission for builds, AWS follow streams,
recursive deletion and unknown command substitution. Explicit pause is the only
reason the native execution path applies; upgrading does not create or remove a
pause marker or modify the owner’s live limits.
