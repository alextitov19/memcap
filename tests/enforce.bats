load helper
setup() {
  setup_common
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/roots.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/measure.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/enforce.sh"
  # Belt: no test in this file may ever send a real kill, whatever else goes wrong.
  # `mc_watch` records real sweep roots from live agent cwds and, if this machine is
  # over budget, runs tier 1/2/3 for real against real pids -- exactly the scenario
  # this belt exists to prevent, mirroring docker.bats's MC_DRY_RUN=1 for the same
  # reason. Individual tests below still set MC_DRY_RUN=1 explicitly before calling
  # enforcement functions directly; this covers the ones that shell out to
  # `bin/memcap watch`/`clean` and would otherwise inherit no override at all.
  export MC_DRY_RUN=1
}

@test "dry run reports without killing" {
  sleep 600 & victim=$!
  run env MC_DRY_RUN=1 "$MEMCAP_ROOT/bin/memcap" clean
  kill -0 $victim
  kill $victim 2>/dev/null || true
}

@test "paused state blocks all enforcement" {
  "$MEMCAP_ROOT/bin/memcap" off
  run "$MEMCAP_ROOT/bin/memcap" watch
  assert_contains "$output" "paused"
  "$MEMCAP_ROOT/bin/memcap" on
}

@test "watch exits zero when there is nothing to do" {
  run "$MEMCAP_ROOT/bin/memcap" watch
  [ "$status" -eq 0 ]
}

@test "init writes a config with a cap matching this machine" {
  run bash -c "yes '' | '$MEMCAP_ROOT/bin/memcap' init --no-service --no-docker"
  [ "$status" -eq 0 ]
  run cat "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  assert_contains "$output" "TOTAL_BUDGET_GB="
}

# --- Final review, small fix: init must validate numeric answers -------------
# Both the total cap and the Docker ceiling feed a `-ge` comparison and, later in
# watch/status, bash arithmetic under `set -u`. A non-numeric answer used to die
# there with a raw "integer expression expected" error instead of a message a
# user could act on, and a degenerate 0 total cap was accepted outright (it fails
# closed downstream -- watch refuses to enforce -- but silently, with init never
# saying why). Both are the same missing check, fixed together with mc_ask_int.
@test "init re-prompts on a non-numeric total cap instead of writing it" {
  run bash -c "printf 'sixteen\n16\n0\nno\n' | '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -eq 0 ]
  assert_contains "$output" "whole number"
  run cat "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  assert_contains "$output" "TOTAL_BUDGET_GB=16"
  assert_contains "$output" "DOCKER_BUDGET_GB=0"
}

@test "init re-prompts on a total cap of 0 instead of writing a degenerate config" {
  run bash -c "printf '0\n16\n0\nno\n' | '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -eq 0 ]
  assert_contains "$output" "at least 1"
  run cat "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  assert_contains "$output" "TOTAL_BUDGET_GB=16"
}

@test "init accepts a Docker ceiling of exactly 0 (skip Docker) without re-prompting" {
  run bash -c "printf '16\n0\nno\n' | '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -eq 0 ]
  assert_not_contains "$output" "whole number"
  assert_not_contains "$output" "at least"
  run cat "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  assert_contains "$output" "DOCKER_BUDGET_GB=0"
}

# --- Carried finding 1: TOCTOU on sweep roots ---------------------------------
# A root recorded while safe can be replaced by a symlink before tier 1 acts on it.
# mc_reap_orphans must re-validate with mc_root_is_safe immediately before using a
# root to match a kill candidate, not trust the state file blindly.
@test "TOCTOU: a root that turned unsafe since being recorded is not used to match orphans" {
  mkdir -p "$HOME/.mc-toctou-$$/proj"
  mc_record_root "$HOME/.mc-toctou-$$/proj"
  rm -rf "$HOME/.mc-toctou-$$/proj"
  ln -sfn /etc "$HOME/.mc-toctou-$$/proj"

  # Victim's command line literally contains the now-unsafe root string, so the
  # OLD (pre-fix) code would textually match it via grep -F and mark it a kill
  # target. perl keeps the argument visible in `ps -o command=`.
  perl -e 'sleep 600' "$HOME/.mc-toctou-$$/proj" &
  victim=$!
  sleep 0.2

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  MC_DRY_RUN=1
  run mc_reap_orphans

  kill "$victim" 2>/dev/null
  rm -rf "$HOME/.mc-toctou-$$"

  assert_not_contains "$output" "would kill"
}

# --- Final review, residual: mc_root_is_safe alone cannot catch a redirect ----
# mc_root_is_safe re-validates that the root still resolves somewhere "safe"
# (2+ levels under HOME), but a root swapped to point at a DIFFERENT directory
# that also happens to be safe-shaped would pass that check -- it can reject an
# unsafe redirect but not detect a safe-shaped one. Comparing the fresh
# canonical form to the exact string that was recorded catches either kind.
@test "TOCTOU: a root redirected to a different, still-safe-shaped directory is not used to match orphans" {
  mkdir -p "$HOME/.mc-redirect-$$/projA" "$HOME/.mc-redirect-$$/projB"
  mc_record_root "$HOME/.mc-redirect-$$/projA"
  rm -rf "$HOME/.mc-redirect-$$/projA"
  ln -sfn "$HOME/.mc-redirect-$$/projB" "$HOME/.mc-redirect-$$/projA"

  # Victim's command line contains the ORIGINAL stored root string (projA), but
  # that path now resolves to projB -- a different directory, still 2+ levels
  # under HOME, so mc_root_is_safe alone would have accepted it.
  perl -e 'sleep 600' "$HOME/.mc-redirect-$$/projA" &
  victim=$!
  sleep 0.2

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  MC_DRY_RUN=1
  run mc_reap_orphans

  kill "$victim" 2>/dev/null
  rm -rf "$HOME/.mc-redirect-$$"

  assert_not_contains "$output" "would kill"
}

# --- Final review, residual: a skipped root must not be silent ----------------
# The three skip cases above used to discard a root with no log line at all,
# even though the redirect case is exactly a TOCTOU event worth knowing about.
# Throttled (mc_log_throttled), not mc_log, since a root that fails here can
# keep failing every pass until mc_record_roots re-registers it.
@test "a redirected root logs why it was skipped, throttled" {
  mkdir -p "$HOME/.mc-redirlog-$$/projA" "$HOME/.mc-redirlog-$$/projB"
  mc_record_root "$HOME/.mc-redirlog-$$/projA"
  rm -rf "$HOME/.mc-redirlog-$$/projA"
  ln -sfn "$HOME/.mc-redirlog-$$/projB" "$HOME/.mc-redirlog-$$/projA"

  perl -e 'sleep 600' "$HOME/.mc-redirlog-$$/projA" &
  victim=$!
  sleep 0.2

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  MC_DRY_RUN=1
  run mc_reap_orphans

  kill "$victim" 2>/dev/null
  rm -rf "$HOME/.mc-redirlog-$$"

  grep -q "skipping sweep root" "$(mc_state_dir)/actions.log"
  grep -q "TOCTOU" "$(mc_state_dir)/actions.log"
}

# --- Final review, residual: root matching must be anchored -------------------
# grep -qF against the raw command line makes root `~/dev/foo` also match
# `~/dev/foobar`, and a process that merely names the root somewhere in an
# argument with nothing after it. Matching "$root/" or "$root " (command padded
# with spaces, the mc_self_ancestry trick) requires a real path-segment or
# end-of-token boundary after the root.
@test "an orphan under a sibling directory that merely starts with the root's name is not matched" {
  mkdir -p "$HOME/.mc-anchor-$$/foo" "$HOME/.mc-anchor-$$/foobar"
  mc_record_root "$HOME/.mc-anchor-$$/foo"

  perl -e 'sleep 600' "$HOME/.mc-anchor-$$/foobar/server.js" &
  victim=$!
  sleep 0.2

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  MC_DRY_RUN=1
  run mc_reap_orphans

  kill "$victim" 2>/dev/null
  rm -rf "$HOME/.mc-anchor-$$"

  assert_not_contains "$output" "would kill"
}

@test "an orphan genuinely under the root is still matched after anchoring" {
  mkdir -p "$HOME/.mc-anchor2-$$/foo"
  # Canonicalized once and reused for the victim's own command line, not the raw
  # "$HOME/..." string: mc_record_root stores mc_canonicalize's resolved form,
  # and on a $HOME that is itself a symlink (macOS's /tmp -> /private/tmp, /var
  # -> /private/var -- exactly what `mktemp -d`-based test runs, and sandboxed
  # CI environments, produce), the raw and resolved forms are different strings.
  # Matching against the raw one would fail for a reason that has nothing to do
  # with anchoring, the thing this test exists to verify.
  real_root=$(mc_canonicalize "$HOME/.mc-anchor2-$$/foo")
  mc_record_root "$HOME/.mc-anchor2-$$/foo"

  perl -e 'sleep 600' "$real_root/node_modules/.bin/vite" &
  victim=$!
  sleep 0.2

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  MC_DRY_RUN=1
  run mc_reap_orphans

  kill "$victim" 2>/dev/null
  rm -rf "$HOME/.mc-anchor2-$$"

  assert_contains "$output" "would kill"
}

# --- CI-portability review: tier1 must catch an orphan behind a symlink ------
# mc_record_roots stores the kernel's resolved (canonical) cwd; the argv match
# above compares that canonical root against the orphan's own argv, which is
# whatever string launched it. When a project sits behind a symlink -- a HOME
# under /tmp or /var (what `mktemp -d`-based test runs, and some sandboxed CI
# environments, produce), ~/dev pointed at an external volume, any symlinked
# ancestor -- argv carries the UNRESOLVED path and never textually contains the
# canonical root, so the argv-only match missed it. mc_reap_orphans now also
# compares the orphan's own canonical cwd against the canonical root. This test
# constructs that scenario directly (a symlinked ancestor directory) so it does
# not depend on the outer test invocation's own $HOME happening to be
# symlinked, and drives the victim's argv AND cwd through the raw, unresolved
# path -- exactly what 6af291e's fixture-canonicalization stopped exercising.
#
# The symlink's TARGET must stay under $HOME (a sibling directory, not a
# mktemp(1) path under /tmp or /var): mc_root_is_safe requires a root to
# resolve to 2+ levels under $HOME, and a target outside $HOME entirely would
# be rejected for that reason alone -- correctly so, but that rejection has
# nothing to do with the symlink-matching behavior this test exists to check.
@test "an orphan behind a symlinked ancestor directory is matched via its own canonical cwd" {
  mkdir -p "$HOME/.mc-symlink-target-$$/foo"
  ln -sfn "$HOME/.mc-symlink-target-$$" "$HOME/.mc-symlink-$$"
  mc_record_root "$HOME/.mc-symlink-$$/foo"

  # Both argv and cwd use the RAW, symlinked path -- mc_record_root stored the
  # resolved form ("$HOME/.mc-symlink-target-$$/foo"), a different string. The
  # kernel-reported cwd (via lsof, inside mc_pid_cwd) resolves through the
  # symlink regardless of which path was used to `cd` there, which is exactly
  # what makes the new cwd-based match work here where the argv-only match
  # cannot.
  ( cd "$HOME/.mc-symlink-$$/foo" 2>/dev/null && exec perl -e 'sleep 600' "$HOME/.mc-symlink-$$/foo/node_modules/.bin/vite" ) &
  victim=$!
  sleep 0.2

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  MC_DRY_RUN=1
  run mc_reap_orphans

  kill "$victim" 2>/dev/null
  rm -rf "$HOME/.mc-symlink-$$" "$HOME/.mc-symlink-target-$$"

  assert_contains "$output" "would kill"
}

# --- Final review, residual: mc_sweep_roots must not be word-split ------------
# `for root in $(mc_sweep_roots)` splits on whitespace, so a recorded root
# containing a space breaks into fragments. A fragment can be a real, existing,
# 2-level-under-HOME directory in its own right -- passing canonicalization and
# the safety check independently of the root it was carved out of -- and then
# match an unrelated process that merely lives under THAT fragment.
@test "a sweep root containing a space is not split into a matchable fragment" {
  mkdir -p "$HOME/.mc-space-$$/foo bar" "$HOME/.mc-space-$$/foo"
  mc_record_root "$HOME/.mc-space-$$/foo bar"

  # Victim lives under a SIBLING directory ("foo", no " bar") that merely shares
  # a path prefix with the recorded root. Word-splitting the recorded root on
  # its embedded space would produce "$HOME/.../foo" as an independent
  # fragment -- itself a real, existing, 2-level-under-HOME directory that
  # would pass every check and then match this unrelated victim.
  perl -e 'sleep 600' "$HOME/.mc-space-$$/foo/server.js" &
  victim=$!
  sleep 0.2

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  MC_DRY_RUN=1
  run mc_reap_orphans

  kill "$victim" 2>/dev/null
  rm -rf "$HOME/.mc-space-$$"

  assert_not_contains "$output" "would kill"
}

# --- Carried finding 2: DEVPIDS has zero test coverage ------------------------
# DEVPIDS is tier 2's input. Both branches need a real, non-empty list: candidates
# that are too young to touch, and a candidate old enough to be selected.
@test "tier2: DEVPIDS candidates younger than TIER2_MIN_AGE_SEC are left alone" {
  # Genuine finding from this conversion pass, not a cosmetic one: the ranked-
  # empty branch of mc_kill_over_budget logs "not touching active work" via
  # mc_log, which writes only to actions.log -- never to stdout. The ORIGINAL
  # `[[ "$output" == *"not touching active work"* ]]` checked the function's
  # captured stdout, which never contains that text, so this assertion was
  # always false. It was never enforced because it wasn't this test's last
  # command (the bash-3.2 non-final-`[[ ]]` issue this whole pass exists to
  # fix) -- converting it to a real assertion surfaced a test that had silently
  # never checked its own stated premise. osascript is stubbed because the real
  # mc_notify call in this branch had also never been stubbed here, and would
  # otherwise fire a real desktop notification on every run of this suite.
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  cat > "$fakebin/osascript" <<'SCRIPT'
#!/usr/bin/env bash
exit 0
SCRIPT
  chmod +x "$fakebin/osascript"

  sleep 600 & victim=$!
  sleep 0.2
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  MC_DRY_RUN=1
  PATH="$fakebin:$PATH" run mc_kill_over_budget

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  assert_not_contains "$output" "would kill"
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "not touching active work"
}

@test "tier2: an old-enough DEVPIDS candidate is selected as the kill target" {
  sleep 600 & victim=$!
  sleep 0.2
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  run mc_kill_over_budget

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  assert_contains "$output" "would kill"
  assert_contains "$output" "$victim"
}

# --- Carried finding 5: a negative agent budget must not be actionable --------
# DOCKER_BUDGET_GB >= TOTAL_BUDGET_GB (e.g. from a hand-edited config) makes
# agents_budget negative or zero. init must refuse to write such a config, and
# watch must refuse to act on one if it exists anyway.
@test "init refuses a hand-typed Docker ceiling that would leave agents no budget" {
  run bash -c "printf '10\n20\nno\n' | '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -eq 0 ]
  run cat "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  assert_contains "$output" "TOTAL_BUDGET_GB=10"
  assert_not_contains "$output" "DOCKER_BUDGET_GB=20"
  assert_contains "$output" "DOCKER_BUDGET_GB=4"
}

# --- mc_etime_secs: macOS `ps` has no `etimes`, only formatted `etime` -------
@test "mc_etime_secs parses mm:ss" {
  run mc_etime_secs "01:30"
  [ "$output" = "90" ]
}

@test "mc_etime_secs parses hh:mm:ss" {
  run mc_etime_secs "01:02:03"
  [ "$output" = "3723" ]
}

@test "mc_etime_secs parses dd-hh:mm:ss" {
  run mc_etime_secs "2-01:02:03"
  [ "$output" = "176523" ]
}

@test "mc_etime_secs rejects non-numeric input instead of miscomparing" {
  run mc_etime_secs "keyword not found"
  [ "$status" -ne 0 ]
}

# --- Final review, C1: tier 2 must trigger on agents NET of simulators -------
# classify.sh folds sim footprint into AGENT_KB too, but only tier 3 can reclaim it,
# and tier 3 declines outright whenever an agent session is alive -- which is the
# tool's entire premise. Left unguarded, tier 2 fires on an overage it structurally
# cannot fix and kills a dev server every pass without converging. mc_watch must
# trigger tier 2 (and tier 1's soft trigger) on AGENT_KB - SIM_KB, not the gross
# figure. Driven through the real `bin/memcap watch` wiring -- common/budget/
# detect/measure/classify/roots/status/enforce sourced in the same order bin/memcap
# uses -- with mc_ps_snapshot stubbed for a deterministic fixture and
# mc_kill_over_budget stubbed to record whether tier 2 was reached, since the actual
# kill path is exercised elsewhere. MC_DRY_RUN=1 throughout per this machine's
# safety rules, though nothing here reaches a real kill or a real `xcrun simctl` call.
@test "C1: tier2 does not fire when the overage is entirely sim-attributable" {
  run env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    mc_ps_snapshot() { printf '9001 1 3000000 /usr/local/bin/claude\n9002 1 9000000 /path/ms-playwright/chromium/chrome\n'; }
    mc_kill_over_budget() { echo TIER2_FIRED; }
    mc_record_roots() { :; }
    mc_watch
  "
  assert_not_contains "$output" "TIER2_FIRED"
}

@test "C1: tier2 still fires when agents are genuinely over budget on their own" {
  run env TOTAL_BUDGET_GB=5 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    mc_ps_snapshot() { printf '9002 1 9000000 /usr/local/bin/claude\n'; }
    mc_kill_over_budget() { echo TIER2_FIRED; }
    mc_record_roots() { :; }
    mc_watch
  "
  assert_contains "$output" "TIER2_FIRED"
}

# --- Final review follow-up, SILENT-GAP: memcap must not go quiet when sims blow
# the combined cap. Post-C1, tier 2 correctly declines to kill a dev server when
# the overage is sim-attributable (killing one would not reclaim a byte) -- but
# mc_notify's only two call sites are both inside mc_kill_over_budget, which by
# construction no longer runs in exactly that state, and nothing ever compared the
# combined figure against `cap`. So a machine sitting over its combined budget from
# simulator/browser memory alone -- the state this tool exists for -- used to
# produce neither a log line nor a notification. mc_watch now logs unconditionally
# and notifies once (through mc_notify's existing 5-minute rate limiter, no new
# machinery) when combined exceeds cap but agents net of sims are under budget.
@test "SILENT-GAP: a real pass logs and notifies once when combined exceeds cap but net is under budget" {
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  capture="$BATS_TEST_TMPDIR/osascript-arg"
  cat > "$fakebin/osascript" <<SCRIPT
#!/usr/bin/env bash
printf '%s' "\$2" >> "$capture"
SCRIPT
  chmod +x "$fakebin/osascript"

  # Same fixture as the first C1 test: combined (12 GB) > cap (10 GB) entirely
  # because of the 9 GB Playwright process, while agents net of sims (3 GB) are
  # comfortably under the 10 GB agent budget -- tier 2 correctly declines.
  # MC_DRY_RUN=0 to prove the REAL (non-dry) path notifies; nothing here can touch
  # anything real -- mc_ps_snapshot is a fixed fixture, mc_kill_over_budget is
  # stubbed, and osascript is stubbed to a capture file instead of a real
  # notification.
  run env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=0 PATH="$fakebin:$PATH" bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    mc_ps_snapshot() { printf '9001 1 3000000 /usr/local/bin/claude\n9002 1 9000000 /path/ms-playwright/chromium/chrome\n'; }
    mc_kill_over_budget() { echo TIER2_FIRED; }
    mc_record_roots() { :; }
    mc_watch
  "
  # assert_not_contains/assert_contains, not `[[ ]]`, for every check that is not
  # this test's last command: bash 3.2 (this session's /bin/bash, and what `bats`
  # itself runs under absent a newer bash on PATH) does not fail a test on a
  # non-final `[[ ]]` that evaluates false -- confirmed empirically and fixed
  # throughout this branch's earlier tests. These are real function calls, so
  # they correctly participate in bash 3.2's error handling regardless of
  # position, and print what was expected/seen on failure.
  assert_not_contains "$output" "TIER2_FIRED"
  run cat "$MEMCAP_STATE_HOME/memcap/actions.log"
  assert_contains "$output" "combined"
  assert_contains "$output" "cap"
  run cat "$capture"
  [ -n "$output" ]
}

@test "SILENT-GAP: a normal under-budget state logs and notifies neither" {
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  capture="$BATS_TEST_TMPDIR/osascript-arg"
  cat > "$fakebin/osascript" <<SCRIPT
#!/usr/bin/env bash
printf '%s' "\$2" >> "$capture"
SCRIPT
  chmod +x "$fakebin/osascript"

  # A lone 2 GB agent process against a 10 GB budget: combined is nowhere near cap.
  run env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 PATH="$fakebin:$PATH" bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    mc_ps_snapshot() { printf '9001 1 2000000 /usr/local/bin/claude\n'; }
    mc_kill_over_budget() { echo TIER2_FIRED; }
    mc_record_roots() { :; }
    mc_watch
  "
  run cat "$MEMCAP_STATE_HOME/memcap/actions.log"
  # assert_not_contains, not [[ ]]: see the comment in the previous test for why.
  assert_not_contains "$output" "combined"
  [ ! -s "$capture" ]
}

@test "SILENT-GAP: a dry run logs the same combined-over-cap state but does not notify" {
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  capture="$BATS_TEST_TMPDIR/osascript-arg"
  cat > "$fakebin/osascript" <<SCRIPT
#!/usr/bin/env bash
printf '%s' "\$2" >> "$capture"
SCRIPT
  chmod +x "$fakebin/osascript"

  # Identical fixture to the first test above, but MC_DRY_RUN=1: the log line
  # (an accurate description of current state either way) still fires, but the
  # notification -- gated the same way I2 gated the tier-2 "killed" notification --
  # must not, matching MC_DRY_RUN's contract everywhere else in this codebase.
  run env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 PATH="$fakebin:$PATH" bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    mc_ps_snapshot() { printf '9001 1 3000000 /usr/local/bin/claude\n9002 1 9000000 /path/ms-playwright/chromium/chrome\n'; }
    mc_kill_over_budget() { echo TIER2_FIRED; }
    mc_record_roots() { :; }
    mc_watch
  "
  run cat "$MEMCAP_STATE_HOME/memcap/actions.log"
  # assert_contains, not [[ ]]: see the comment in the first SILENT-GAP test.
  assert_contains "$output" "combined"
  assert_contains "$output" "cap"
  [ ! -s "$capture" ]
}

# --- Final review follow-up, THROTTLE: the log must not drown its own signal --
# 24 hours of real running showed two per-pass status lines -- tier3 declining,
# and the combined-cap line above -- making up 94% of actions.log, burying the
# 210 kill records the file exists for. mc_log_throttled (common.sh) must gate
# those two lines; kill records must stay unthrottled.
@test "THROTTLE: kill records remain unthrottled -- two consecutive tier1 reaps both log" {
  mkdir -p "$HOME/.mc-throttle-$$/proj"
  # Canonicalized once and reused for both victims' command lines -- see the
  # anchoring test above for why matching against the raw "$HOME/..." string
  # breaks whenever $HOME is itself a symlink, as `mktemp -d`-based test runs
  # produce on macOS.
  real_root=$(mc_canonicalize "$HOME/.mc-throttle-$$/proj")
  mc_record_root "$HOME/.mc-throttle-$$/proj"

  perl -e 'sleep 600' "$real_root" &
  victim1=$!
  sleep 0.2
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim1"
  # A REAL (non-dry) kill, unlike the rest of this file: this is exactly what is
  # under test -- mc_log's kill-record line must fire every time, unthrottled --
  # and both victims are dummy perl processes this test owns and spawned itself,
  # never anything reachable from a real snapshot.
  MC_DRY_RUN=0
  mc_reap_orphans

  perl -e 'sleep 600' "$real_root" &
  victim2=$!
  sleep 0.2
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim2"
  MC_DRY_RUN=0
  mc_reap_orphans

  rm -rf "$HOME/.mc-throttle-$$"

  run grep -c "tier1 orphan" "$MEMCAP_STATE_HOME/memcap/actions.log"
  [ "$output" = "2" ]
}

@test "THROTTLE: the tier3-decline and combined-cap keys throttle independently" {
  sleep 600 & agent=$!
  sleep 0.2
  run env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    mc_ps_snapshot() { printf '$agent 1 3000000 /usr/local/bin/claude\n9002 1 9000000 /path/ms-playwright/chromium/chrome\n'; }
    mc_kill_over_budget() { echo TIER2_FIRED; }
    mc_record_roots() { :; }
    mc_watch
  "
  kill "$agent" 2>/dev/null

  # A real, alive pid for the agent (not the C1 tests' fake 9001) so
  # mc_no_live_session genuinely sees a live session and tier3 actually declines
  # through that branch in the same pass the combined-cap line fires, proving one
  # key firing does not suppress the other.
  log="$MEMCAP_STATE_HOME/memcap/actions.log"
  grep -q "declining -- an agent session is alive" "$log"
  grep -q "combined.*exceeds" "$log"
}

@test "THROTTLE: dropping under the combined cap and back over it re-logs despite the window" {
  over='
    mc_ps_snapshot() { printf "9001 1 3000000 /usr/local/bin/claude\n9002 1 9000000 /path/ms-playwright/chromium/chrome\n"; }
  '
  under='
    mc_ps_snapshot() { printf "9001 1 2000000 /usr/local/bin/claude\n"; }
  '
  common="
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    mc_kill_over_budget() { :; }
    mc_record_roots() { :; }
  "
  # Pass 1: over cap -- logs. Pass 2: under cap -- clears the key. Pass 3: over
  # cap again, well within the default 1800s window -- must log AGAIN, because
  # pass 2 cleared it. All three run under the SAME MEMCAP_STATE_HOME, so the
  # throttle stamp genuinely persists across these three separate invocations,
  # same as it would across real 60-second polls.
  env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 bash -c "$common $over mc_watch" >/dev/null
  env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 bash -c "$common $under mc_watch" >/dev/null
  env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 bash -c "$common $over mc_watch" >/dev/null

  run grep -c "combined.*exceeds" "$MEMCAP_STATE_HOME/memcap/actions.log"
  [ "$output" = "2" ]
}

@test "THROTTLE: two consecutive over-cap passes with no drop between them log only once" {
  # The contrast case for the test above: without an intervening under-cap pass
  # to clear the key, back-to-back over-cap passes must throttle to a single log
  # line. This is the assertion that actually distinguishes throttled from
  # unthrottled behavior -- the state-change test above would pass even with no
  # throttling at all, since both its over-cap passes are separated by a clear.
  over="
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    mc_ps_snapshot() { printf '9001 1 3000000 /usr/local/bin/claude\n9002 1 9000000 /path/ms-playwright/chromium/chrome\n'; }
    mc_kill_over_budget() { :; }
    mc_record_roots() { :; }
    mc_watch
  "
  env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 bash -c "$over" >/dev/null
  env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 bash -c "$over" >/dev/null

  run grep -c "combined.*exceeds" "$MEMCAP_STATE_HOME/memcap/actions.log"
  [ "$output" = "1" ]
}

@test "watch refuses to act when DOCKER_BUDGET_GB leaves no room for agents" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  cat > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" <<-'EOF'
	TOTAL_BUDGET_GB=8
	DOCKER_BUDGET_GB=10
	EOF
  run "$MEMCAP_ROOT/bin/memcap" watch
  [ "$status" -ne 0 ]
  assert_contains "$output" "misconfigured"
}

# --- Review round 1, Finding 1 (CRITICAL): never kill an agent CLI via a subtree
# walk. mc_kill_pids is the single choke point every tier's kill list passes
# through, so the protection filter is tested there directly, plus once more at
# the tier-2 level to prove a real subtree gets it right end to end.
@test "mc_kill_pids filters out a pid present in AGENTPIDS" {
  sleep 600 & victim=$!
  # shellcheck disable=SC2034  # consumed by mc_filter_protected
  AGENTPIDS="$victim"
  MC_DRY_RUN=1
  # run, not a bare `output=$(...)`: mc_kill_pids now returns non-zero (I2) whenever
  # it kills nothing, including this filtered-empty case, and a bare assignment's
  # exit status trips bats' own error trap before the cleanup `kill` below can run.
  run mc_kill_pids "$victim" "test"
  kill -0 "$victim"
  kill "$victim" 2>/dev/null
  [ -z "$output" ]
}

@test "mc_kill_pids filters out memcap's own pid" {
  MC_DRY_RUN=1
  run mc_kill_pids "$$" "test"
  [ -z "$output" ]
}

@test "mc_kill_pids filters out its own parent, not just itself" {
  parent=$(ps -o ppid= -p $$ | tr -d ' ')
  MC_DRY_RUN=1
  run mc_kill_pids "$parent" "test"
  [ -z "$output" ]
}

@test "mc_kill_pids does not filter out an ordinary, unrelated pid" {
  sleep 600 & victim=$!
  AGENTPIDS=""
  MC_DRY_RUN=1
  # Dry run is also a non-kill outcome (I2), so this now returns non-zero too --
  # `run` again, for the same reason as above.
  run mc_kill_pids "$victim" "test"
  kill -0 "$victim"
  kill "$victim" 2>/dev/null
  assert_contains "$output" "would kill"
  assert_contains "$output" "$victim"
}

# --- Final review, small fix: an empty rss reading must not corrupt tier2 ranking
# If a candidate exits between the age check and the rss read, `kb` comes back
# empty and the ranked line becomes " $pid" instead of "$kb $pid". `sort -rn` then
# parses that bare pid as the sort key -- and a pid number routinely exceeds a real
# kb value, so the exited candidate's line can sort ABOVE a legitimate, still-alive
# candidate. `awk '{print $2}'` then extracts nothing from the single-field line,
# and the pass silently kills nothing even though a real over-budget candidate was
# right there. `ps` and `pgrep` are stubbed so this is fully deterministic: pid
# 999000 mimics the exited candidate (age succeeds, rss comes back empty), pid 123
# mimics a real, legitimate candidate with 500000 KB -- chosen smaller than 999000
# so the bug's mis-sort would rank the exited pid first.
@test "tier2: a candidate with no rss reading is skipped instead of corrupting the ranking" {
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  cat > "$fakebin/ps" <<'SCRIPT'
#!/usr/bin/env bash
pid="" mode=""
for a in "$@"; do
  case "$a" in
    etime=) mode=etime ;;
    rss=)   mode=rss ;;
    [0-9]*) pid="$a" ;;
  esac
done
case "$pid:$mode" in
  999000:etime) echo "  05:00" ;;
  999000:rss)   : ;;                 # gone -- no output, exactly like an exited pid
  123:etime)    echo "  05:00" ;;
  123:rss)      echo " 500000" ;;
esac
exit 0
SCRIPT
  chmod +x "$fakebin/ps"
  cat > "$fakebin/pgrep" <<'SCRIPT'
#!/usr/bin/env bash
exit 1
SCRIPT
  chmod +x "$fakebin/pgrep"

  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="999000 123"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  PATH="$fakebin:$PATH" run mc_kill_over_budget

  assert_contains "$output" "would kill"
  assert_contains "$output" "123"
  assert_not_contains "$output" "999000"
}

# --- Final review, residual: tier2 must rank by footprint, not summed RSS -----
# mc_watch's trigger decision is built on physical footprint (measure.sh) because
# summed ps RSS over-counts shared pages ~2.5x. Ranking tier-2 victims by RSS
# instead of the metric that made the decision could select the wrong candidate.
@test "tier2 ranks victims by physical footprint, not summed ps RSS" {
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  cat > "$fakebin/ps" <<'SCRIPT'
#!/usr/bin/env bash
pid="" mode=""
for a in "$@"; do
  case "$a" in
    etime=) mode=etime ;;
    rss=)   mode=rss ;;
    [0-9]*) pid="$a" ;;
  esac
done
case "$pid:$mode" in
  5010:etime) echo "  10:00" ;;
  5010:rss)   echo " 1000" ;;        # tiny RSS -- would lose if ranking used this
  5020:etime) echo "  10:00" ;;
  5020:rss)   echo " 50000000" ;;    # huge RSS -- would win if ranking used this
esac
exit 0
SCRIPT
  chmod +x "$fakebin/ps"
  cat > "$fakebin/top" <<'SCRIPT'
#!/usr/bin/env bash
pid="" prev=""
for a in "$@"; do
  [ "$prev" = "-pid" ] && pid="$a"
  prev="$a"
done
case "$pid" in
  5010) echo "5010  9000M" ;;        # the higher FOOTPRINT of the two
  5020) echo "5020  500M" ;;
esac
exit 0
SCRIPT
  chmod +x "$fakebin/top"
  cat > "$fakebin/pgrep" <<'SCRIPT'
#!/usr/bin/env bash
exit 1
SCRIPT
  chmod +x "$fakebin/pgrep"

  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="5010 5020"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  PATH="$fakebin:$PATH" run mc_kill_over_budget

  # 5010 has the higher footprint (9000M via top) despite the lower RSS (1000K
  # via ps); 5020 is the reverse. Ranking by RSS -- the pre-fix behavior -- would
  # have selected 5020 instead.
  assert_contains "$output" "would kill"
  assert_contains "$output" "5010"
  assert_not_contains "$output" "5020"
}

@test "tier2: a subtree containing an agent pid kills the server but spares the agent" {
  bash -c 'sleep 600 & wait' >/dev/null 2>&1 & server=$!
  sleep 0.3
  agent_child=$(pgrep -P "$server" | head -1)
  [ -n "$agent_child" ]

  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$server"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # AGENTPIDS marks the child as an agent CLI living beneath the dev server --
  # an ordinary shape in agentic workflows (e.g. `npm run dev` shelling out to one).
  # shellcheck disable=SC2034  # consumed by mc_filter_protected
  AGENTPIDS="$agent_child"
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  run mc_kill_over_budget

  kill "$agent_child" 2>/dev/null
  kill "$server" 2>/dev/null

  assert_contains "$output" "would kill"
  assert_contains "$output" "$server"
  assert_not_contains "$output" "$agent_child"
}

# --- Review round 1, Finding 3: `clean` must honour the pause file too. `memcap
# off` is a kill switch -- it should stop manual sweeps, not just the watch loop.
@test "clean refuses while paused" {
  "$MEMCAP_ROOT/bin/memcap" off
  run "$MEMCAP_ROOT/bin/memcap" clean
  assert_contains "$output" "paused"
  "$MEMCAP_ROOT/bin/memcap" on
}

# --- Follow-up: tier 3 must honour SIM_IDLE_GRACE_SEC ------------------------
# SIM_IDLE_GRACE_SEC is written by init but was read by no code, so a hand-booted
# simulator (Simulator.app or `simctl` run directly, no Xcode, no agent session) was
# reaped on the very first poll. mc_reap_sims must stamp the first idle sighting and
# wait out the grace before reaping, and clear the stamp whenever a session reappears.
@test "tier3: first idle poll stamps the grace period and kills nothing" {
  sleep 600 & victim=$!
  AGENTPIDS=""
  SIMPIDS="$victim"
  MC_DRY_RUN=1
  run mc_reap_sims

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  [ -z "$output" ]
  [ -f "$(mc_sims_idle_stamp "$victim")" ]
}

@test "tier3: once the idle stamp is past SIM_IDLE_GRACE_SEC the reap proceeds" {
  # A real sim-pattern match is needed once the reap actually proceeds -- a plain
  # sleep would never be selected by mc_reap_sims's own command-line filter. perl
  # keeps the marker argument visible in `ps -o command=`.
  perl -e 'sleep 600' "ms-playwright-fixture" &
  victim=$!
  sleep 0.2
  AGENTPIDS=""
  SIMPIDS="$victim"
  MC_DRY_RUN=1

  run mc_reap_sims
  [ -z "$output" ]
  stamp="$(mc_sims_idle_stamp "$victim")"
  [ -f "$stamp" ]

  # Grace set to 0 rather than sleeping for real: time has moved forward at least
  # zero seconds since the first poll, so the gate opens on this second poll.
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIM_IDLE_GRACE_SEC=0
  run mc_reap_sims

  kill "$victim" 2>/dev/null

  assert_contains "$output" "would kill"
  assert_contains "$output" "$victim"
  [ ! -f "$stamp" ]
}

# --- Final review, I6: the idle grace stamp must be per-pid, not machine-wide -
# One shared `sims-idle` stamp meant a hand-booted simulator inherited whichever
# timestamp an unrelated, already-idle sim process had accumulated, and could be
# reaped with none of its own grace. Each tracked sim pid now gets its own stamp,
# and a pid seen idle for the first time this pass blocks the WHOLE reap -- not just
# itself -- until it too clears the grace, rather than being swept in in with an
# older pid's head start.
@test "I6: a freshly-tracked sim pid blocks the reap even though another tracked sim already cleared its own grace" {
  perl -e 'sleep 600' "ms-playwright-fixture" & old=$!
  sleep 600 & fresh=$!
  sleep 0.2
  AGENTPIDS=""
  SIMPIDS="$old $fresh"
  MC_DRY_RUN=1
  # $old gets a stamp far enough in the past to have cleared any real-world grace on
  # its own -- mirroring a sim that has genuinely been idle a while. $fresh gets none:
  # it is tracked for the first time on this very pass, exactly like a simulator just
  # booted by hand. Under the old shared-stamp design this single old timestamp would
  # have applied to $fresh too, and $old (a real kill-pattern match) would have been
  # reaped immediately -- taking $fresh's grace away from it in the process.
  mkdir -p "$(mc_sims_idle_dir)"
  echo 1 > "$(mc_sims_idle_stamp "$old")"

  run mc_reap_sims

  kill "$old" "$fresh" 2>/dev/null

  assert_not_contains "$output" "would kill"
  [ -f "$(mc_sims_idle_stamp "$fresh")" ]
}

@test "I6: once every tracked sim pid has individually cleared the grace, the reap proceeds" {
  perl -e 'sleep 600' "ms-playwright-fixture" & old=$!
  sleep 600 & fresh=$!
  sleep 0.2
  AGENTPIDS=""
  SIMPIDS="$old $fresh"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  echo 1 > "$(mc_sims_idle_stamp "$old")"
  run mc_reap_sims
  # assert_not_contains, not a standalone `[[ ]]`: this check's own $output is
  # about to be overwritten by the next `run` below, so it can't share a single
  # combined [[ ]] with the checks after it -- but it's still a real function
  # call, so it correctly participates in bash 3.2's error handling where a bare
  # `[[ ]]` would not (see classify.bats).
  assert_not_contains "$output" "would kill"

  # Grace set to 0 rather than sleeping for real, same trick as the single-pid test
  # above: $fresh's own stamp (written on the poll just above) now also counts as
  # cleared, so both pids are individually ready and the pass proceeds.
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIM_IDLE_GRACE_SEC=0
  run mc_reap_sims

  kill "$old" "$fresh" 2>/dev/null

  assert_contains "$output" "would kill"
  assert_contains "$output" "$old"
}

@test "I6: mc_hands_on_mobile also treats Simulator.app itself as hands-on" {
  perl -e 'sleep 600' "/Applications/Xcode.app/Contents/Developer/Applications/Simulator.app/Contents/MacOS/Simulator" &
  victim=$!
  sleep 0.2
  run mc_hands_on_mobile
  kill "$victim" 2>/dev/null
  [ "$status" -eq 0 ]
}

# --- Final review, I2: dry run must not notify that a kill happened ---------
# mc_notify was called unconditionally after mc_kill_pids, so MC_DRY_RUN=1 -- and a
# real pass where mc_filter_protected removed every candidate -- told the user a dev
# server was killed when nothing was, contradicting the dry-run guarantee and burning
# the 5-minute notification rate limit. osascript is stubbed so this cannot fire a
# real desktop notification, matching scaffold.bats's stub_osascript pattern.
@test "I2: a dry-run tier2 pass does not send the 'killed a leaked dev server' notification" {
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  capture="$BATS_TEST_TMPDIR/osascript-arg"
  cat > "$fakebin/osascript" <<SCRIPT
#!/usr/bin/env bash
printf '%s' "\$2" >> "$capture"
SCRIPT
  chmod +x "$fakebin/osascript"

  sleep 600 & victim=$!
  sleep 0.2
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  PATH="$fakebin:$PATH" run mc_kill_over_budget

  kill "$victim" 2>/dev/null

  assert_contains "$output" "would kill"
  [ ! -f "$capture" ]
}

@test "I2: a tier2 pass where protection removed every candidate does not notify either" {
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  capture="$BATS_TEST_TMPDIR/osascript-arg"
  cat > "$fakebin/osascript" <<SCRIPT
#!/usr/bin/env bash
printf '%s' "\$2" >> "$capture"
SCRIPT
  chmod +x "$fakebin/osascript"

  sleep 600 & victim=$!
  sleep 0.2
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # The candidate is itself in AGENTPIDS, so mc_filter_protected removes it before
  # mc_kill_pids ever reaches the MC_DRY_RUN check -- this must not claim a kill
  # happened regardless of dry-run status. MC_DRY_RUN=1 kept per this machine's
  # safety rules; it is not what this test is exercising.
  # shellcheck disable=SC2034  # consumed by mc_filter_protected
  AGENTPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  PATH="$fakebin:$PATH" run mc_kill_over_budget

  kill "$victim" 2>/dev/null

  [ ! -f "$capture" ]
}

# --- Final review follow-up: log why tier 3 declined -------------------------
# mc_reap_sims used to return silently whenever a live session or hands-on mobile
# work blocked it, so a wedge (a broken mc_hands_on_mobile pattern, say) would leave
# actions.log with nothing to diagnose it, despite the README promising the log
# records enforcement decisions.
@test "tier3: a live agent session clears every existing idle stamp, and logs why it declined" {
  dir="$(mc_sims_idle_dir)"
  mkdir -p "$dir"
  echo "1" > "$(mc_sims_idle_stamp 99999)"

  sleep 600 & agent=$!
  # shellcheck disable=SC2034  # consumed by mc_no_live_session, sourced from enforce.sh
  AGENTPIDS="$agent"
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  run mc_reap_sims

  kill "$agent" 2>/dev/null

  [ ! -d "$dir" ]
  # grep -q, not [[ ]]: a real external command's failure correctly trips bash
  # 3.2's error handling regardless of position, unlike a bare `[[ ]]` (see the
  # SILENT-GAP tests above for the full explanation) -- moot here since this is
  # already the test's last check, but kept consistent with the rest of this file.
  grep -q "declining -- an agent session is alive" "$(mc_state_dir)/actions.log"
}

@test "tier3: hands-on mobile work (Simulator.app) declines and logs why" {
  perl -e 'sleep 600' "/Applications/Xcode.app/Contents/Developer/Applications/Simulator.app/Contents/MacOS/Simulator" &
  sim=$!
  sleep 0.2
  # shellcheck disable=SC2034  # consumed by mc_no_live_session, sourced from enforce.sh
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  run mc_reap_sims

  kill "$sim" 2>/dev/null

  grep -q "declining -- hands-on mobile work" "$(mc_state_dir)/actions.log"
}
