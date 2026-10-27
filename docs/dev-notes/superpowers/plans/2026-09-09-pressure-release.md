# v0.7.0 pressure diagnostics and safe cleanup implementation plan

**Goal:** Address the September 2–9 audit and publish the CLI and Homebrew formula.
**Architecture:** Add host-pressure diagnostics beside the existing footprint measurement; retain the exact sampled process table for bounded forensic records. Separate reporting from reclaim decisions, and narrow mobile vetoes only for browser processes demonstrably owned by a different agent session.
**Stack:** macOS Bash 3.2, awk, existing jq dependency, Bats and ShellCheck.
**Evidence:** September 9 panic and the log audit in /tmp/memcap-week-audit-2026-09-09.md.

## Constraints

No real watch/clean/uninstall, Docker apply/restart, production state/config writes, or generic Python kill rule. All enforcement checks use isolated state/config and MC_DRY_RUN=1. Preserve age, identity, orphan/root, agent CLI, self-ancestry, server-held-resource, and pause safeguards. Complete all implementation before one final whole-diff review. Release commits/pushes are authorized by the explicit release request.

## Tasks

- [x] Add `libexec/diagnostics.sh` and `tests/diagnostics.bats`: `mc_host_pressure` reads disk/swap with unknown distinct from zero; `mc_pressure_capture` stores twelve bounded private snapshots of the largest twenty processes, including classification, parent, start identity, and abbreviated command. Log recovery and throttle persistent warnings; never treat allocated swap being full as an error by itself.
- [x] Update `common.sh`: timestamped stderr fallback for failed audit/state writes, atomic heartbeat/outcome/counter writes that retain prior data on failure; avoid coupling reclamation to successful diagnostics. Reproduce write failures using a sandbox path, not a full real disk.
- [x] Update `mc_watch`: report combined pressure regardless of tier 2 selection; retain original sample for diagnostics; surface over-budget/unreclaimed outcomes and current blockers. Refuse tier 2 on unexpected measurement degradation before it can act; keep safe orphan cleanup available and preserve deliberate MC_NO_TOP behavior.
- [x] Update mobile evidence: anchor executable matching, report exact blocker identities, allow only a proven different-session idle browser past a mobile veto; unknown ownership and all mobile simulators retain the veto. Test same-session, different-session, unavailable ancestry, command mentions, server-held resources, and actual dry-run integration.
- [x] Update status/config/init/docs: host disk/swap, snapshot location, effective allocation and remaining headroom, truthful degraded/blocked outcomes; no automatic budget changes. Version/changelog v0.7.0.
- [ ] Run regression tests red before implementation, then full required checks (ShellCheck, individually parsed Bash files, both 500+ Bats suites), and one final whole-diff review. Add no repeated review cycle.
- [ ] Open release PR, check actual CI, merge, tag and publish v0.7.0; update the `alextitov19/homebrew-memcap` formula URL and verified archive SHA256, validate and publish the tap. Verify installation in an isolated prefix or sandboxed CLI invocation without restarting Docker or writing production memcap state.

## Release validation status

Implementation and the single final review are complete. Normal Bats: 528/528; ShellCheck: 34 files plus individual test checks; system Bash parse: 17 files. Both deliberate negative controls failed as expected. Empty-home/no-Docker and GitHub CI results will be recorded in the release PR before publication.
