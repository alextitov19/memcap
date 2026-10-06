# Installed tool compatibility implementation plan

**Goal:** Preserve lightweight execution and identify positive heavy demand with
the owner's new OrbStack, mise and developer tools, without changing live policy.

**Architecture:** Keep process accounting independent of the selected Docker
endpoint; both engines can coexist. Read local context metadata without contacting
or starting an engine. Extend bounded, non-executing command inspection rather
than treating unfamiliar tools as heavy. Keep unknown syntax native.

**Tech stack:** Bash 3.2, Python standard library, Bats/unittest.

**Spec:** User's October 4 request and updated global tool inventory.

## Constraints and review focus

- No engine restart, profile modification, queue manipulation or live policy edits.
- Sandbox all fixtures; enforcement tests remain dry-run. Heavy validation uses
  the live queue. No automatic commits or per-task reviews.
- Preserve agent protection for ambiguous Goose invocations; distinguish the
  known Homebrew database binary by installation identity.
- Explicit remote/unknown contexts must never select Docker Desktop for mutation.
- Documentation strings, option values and help commands must not become workloads.
- Inspect task files as bounded data, never execute task managers during inspection.
- Record unsupported dynamic task definitions as unknown, not proof of low memory.

## Tasks

- [x] Add failing tests for OrbStack process attribution, context precedence,
  stale Docker Desktop ceiling reporting, Goose DB identity, and paired new-tool
  light/heavy invocations. Run them before implementation.
- [x] Update `libexec/classify.sh` and `libexec/docker.sh`, with a bounded local
  context reader in `libexec/docker_runtime.py`. Exercise temporary Docker config,
  explicit endpoint precedence, malformed metadata and both engines together.
- [x] Extend `libexec/demand_policy.py` via `libexec/tool_demand.py` for mise,
  just, hyperfine and the concrete new build/test/media/scanning tools. Keep
  project inspection bounded and fingerprinted, including changed task definitions.
- [x] Document supported syntax and limitations; save compatibility fixtures and
  actual validation results. Run targeted regressions, ShellCheck, individual
  Bash parsing, normal Bats and isolated-HOME/no-Docker Bats.
- [x] Perform one whole-diff review; fix clear blockers, report actual counts
  and distinguish code validation from live installation.

## Execution evidence

- Original 13 compatibility tests executed against the old implementation and
  failed at assertions; the fixture commands were never executed. This is the
  negative control for runtime, attribution and routing fixes.
- Expanded suite: 21/21 compatibility tests pass, including hook routing,
  malformed/FIFO context metadata, help-vs-child-argument distinctions, task
  cycles, expired inspection budgets and engine-switch environment protection.
- Existing 18/18 demand-policy tests pass; focused Bats run passed 57/57.
- Three initial Docker fallback regressions were caught and fixed. An initial
  redundant-case ShellCheck warning was removed; full ShellCheck and individual
  Bash parsing subsequently passed with exit 0.
- Full normal Bats: 653/653 passed. Isolated-HOME Bats: 653/653 passed.
  Both suite commands exited 0. The original combined harness exited 1 because
  it retained the first lint warning's status; the corrected full ShellCheck
  and per-file Bash parse command separately exited 0. No failure is relabeled.
- Original 28-command replay: 11 missed heavy cases before, zero mismatches
  after; all labeled light cases stayed native. This is a synthetic routing
  comparison, not measured developer throughput or a claim about memory peaks.
- One final review covered context-to-mutation eligibility, process attribution
  and protection, wrapper demand and native inspection. Fixed the concrete help
  routing edge cases found there; no second review cycle.
- Baseline saved privately as `before-tool-compatibility-20261004` (8,153 events).
  Host was migrating Docker Desktop to OrbStack; both engines were running.
  Treat that environmental change separately from software performance changes.
- Implementation remains uncommitted and uninstalled; no engines, profiles or
  policy changed. Full-suite logs are preserved privately under release-history/
  tool-compatibility-validation-20261004. CI was not run for this uncommitted diff.
