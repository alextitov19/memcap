# Workload queue implementation plan

Goal: implement the approved shared admission queue in this session.
Spec: ../specs/2026-09-21-workload-queue.md
Execution: native, no commits, one final review after the complete implementation.

1. Add failing behavioral tests in tests/test_scheduler.py and a Bats entrypoint:
   five concurrent jobs never exceed two; pressure blocks launch; unavailable
   measurement fails closed; output/exit status survive; expired requests do not
   launch; resources deduplicate; surviving children retain reservations; nested
   jobs cannot deadlock; worker limits cover direct and package-script calls;
   hook quoting, options and lightweight bypass preserve execution semantics.
2. Implement libexec/scheduler.py and scheduler_policy.py. Use flock around
   private atomic registry writes and admission/launch; use injectable samplers
   in unit tests, never live host assumptions. Track PID start identities and
   process-group members, including supervisor death and PID reuse.
3. Add libexec/scheduler.sh for config export, footprint/pressure sampling and
   the constrained cancellation bridge; connect run/queue/queue-hook and hook
   generation through bin/memcap and feedback.sh. Add a narrow scheduled scope
   to the existing kill choke point with fresh per-PID authorization.
4. Document defaults, installation requirements, opt-in hook output, status,
   timeout/cancellation and coverage limits. Include a queue row in status only
   when queue state exists. Integrate Python behavioral tests into existing Bats.
5. Run targeted tests; demonstrate admission negative control. Complete scope,
   then review the final diff once and fix clear blockers. Run ShellCheck, each
   Bash parse check, Bats normally and with isolated HOME; report exact counts.

Review focus: concurrent launch race, child surviving supervisor, PID reuse,
shell quoting/substitution, nested job and persistent-resource slot starvation.

## Completion evidence

Implemented all five steps in the working tree, with one final whole-diff review.
ShellCheck passed, all 20 Bash files parsed, and Ruff lint/format checks passed.
Bats passed 559/559 both normally and with isolated HOME. Each run includes
29 Python behavioral checks. Disabling the concurrency guard in a temporary
copy made the held-open five-job fixture fail (observed peak 3, expected 2).
The sandboxed live-host smoke check refused admission because the combined
budget was already in use; it launched nothing and left no queue reservations.

Review fixes: preserve Go program/test arguments when bounding build workers,
keep Vitest minimum workers within an explicit lower maximum, and prevent a
timed-out waiter from launching when capacity returns late. Sampler tests also
caught AWK redirection precedence and trailing blank snapshot rows.

No commit, release, live hook activation, Docker operation or installed-daemon
change was made. Client hook behavior was checked with protocol fixtures, not
by changing live Claude/Codex settings. No separate no-Docker machine or remote
CI run was performed. Hook timeout coverage and external MCP/setsid/simulator
launches remain the documented limits of admission control.
