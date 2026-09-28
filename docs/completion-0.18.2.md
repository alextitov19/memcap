# Completion ownership and open-issue audit

Investigated 2026-09-28 against v0.18.1, covering the 45 open issues #147–#195
(excluding numbers already closed or used by pull requests).

## Confirmed ownership failure

One Codex app-server hosts multiple independent conversations. Its live process
tree and private queue registry contained explicit runners from multiple projects
with empty session keys. The hook recognized `memcap run` as already wrapped and
left it untouched. Completion accepted any unkeyed job under the same agent PID,
so finishing one conversation waited for other projects' work (#193/#194).

The fix binds explicit/cached runners to the current hook identity, without
touching child arguments. Scoped Stop and wait observers require that identity
and a fresh same-user supervisor identity. An old unkeyed job cannot establish
conversation ownership: it remains supervised and reserved but does not block
every conversation. A CLI wait with no hook identity retains process-wide
observation; a specific job ID remains available for legacy jobs. No job is
cancelled, reassigned or removed from the live registry by this change.

Absolute executable paths and lightweight redirected/composed session waits also
receive the same identity. Codex input rewrites preserve its permission contract;
restricted sessions never receive an automatic approval from classification.

## Confirmed inspection failures

This investigation reproduced ordinary status/read chains containing Git remote
inspection and jq object projections entering the live queue. Regression coverage
includes explicit jq string arguments, data-only fallback values in curl headers,
and relative remote helper paths resolved against the tool's actual workdir.
Expanded arguments are still validated at execution. Unknown scripts, command
substitutions in fallbacks and execution-capable options remain managed.

The initial six regression tests produced 16 failing assertions on v0.18.1.
Additional tests cover guarded execution, permission modes and composed waits.
All fixture CLI calls isolate config/state and keep enforcement dry-run.

## Issue disposition

| Issues | Evidence and disposition |
| --- | --- |
| #193, #194 | Confirmed shared-daemon/unkeyed-runner ownership defect fixed here. |
| #151, #164, #168, #184, #188 | Earlier Stop symptoms; the new regression covers the recurring mechanism, but their snapshots alone do not prove identical causes. |
| #149, #167, #169, #170, #172, #179, #185, #186, #187, #191, #195 | Inspection delays; reproduced Git/jq gaps fixed. Other command shapes require a local reproduction before claiming resolution. |
| #147, #152, #162, #163, #166, #176, #177, #180, #183, #189, #190, #192 | Remote-control delays; data-only fallback and actual-workdir helper checks address confirmed remaining gaps. Arbitrary mixed local scripts still require admission. |
| #153, #154, #155, #156, #157, #158, #160, #161, #171, #173, #175, #178, #181, #182 | Broad waiting/integration symptoms. Preserve unresolved throughput evidence rather than claim all capacity waits are defects or resolved by this patch. |
| #159 | Prior peak-learning, detached-descendant accounting and paging-order fixes remain. Aggregate pressure evidence does not establish a new signal authority or justify relaxing headroom. Live verification remains necessary. |
| #196 | Found during validation: shared test setup allowed dry-run watchdog tests to read the real Docker settings. Give every test an isolated settings path before sourcing libraries. |

The first local full-suite attempt was cancelled with exit 130 after 186 passing
tests because a watchdog fixture stalled reading the real Docker settings file.
That incomplete attempt is not a passing validation run. No real Docker settings
were changed. A regression now checks that shared setup selects the isolated
settings path before libraries are loaded.

The next normal run completed with 614 passing tests and two failing Python-suite
wrappers. The older wait fixture lacked the UID present in real process-table
rows; the paused Codex fixture omitted permission mode and expected the former
rewrite contract. Both fixtures were corrected to exercise the intended runtime
contract. Those failures are not counted as a successful full run.
The targeted rerun also caught an unnecessary rewrite of an already resolved,
unscoped wait. The hook now leaves identical argv untouched; the existing
compatibility assertion is preserved.
Release verification also reproduced named-remote ref lookups entering admission.
Two added regression assertions failed before the narrow classifier fix; custom
upload-pack programs, helper protocols and Git configuration overrides remain
managed. The final validation is rerun on the complete release contents.

Final local validation passed: ShellCheck (including 27 test files individually),
24 individual Bash parses, 616/616 normal Bats tests and 616/616 tests with a
temporary HOME and no Docker runtime. The 12-test new regression suite and the
5-test productivity/11-test pause suites passed directly too. Against immutable
v0.18.1, the expanded regression suite failed with 25 assertions and two missing
rewrite-output errors, as expected. CI is recorded separately on the release PR.

## Activation

Upgrade through Homebrew. Existing stable-path hooks load the corrected code on
their next invocation; no hook definition change or Mac restart is required.
Already-running supervisors retain their loaded scheduler version until they
finish. Do not resubmit or cancel them to make an upgrade appear complete.

If hook definitions are stale, `memcap integrate` refreshes only the selected
profiles, with the usual backups and consent. Claude and Claude-personal are
separate profiles. Reload the affected session after replacing definitions;
Codex hook trust still requires the owner's review and is never auto-approved.
This release does not change live memory policy, pause state or Docker settings.
