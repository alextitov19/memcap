load helper
bats_require_minimum_version 1.5.0
setup() {
  setup_common
  export BUDGET_MODE="split" # Explicit legacy-policy regression coverage.
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
  # Sourced in the same order bin/memcap uses, and specifically so mc_num/mc_frac
  # (contract C1) are the REAL ones here rather than enforce.sh's standalone
  # fallbacks -- the config-warning assertions below depend on the real throttled
  # log line, and every numeric knob in this file is meant to reach mc_num.
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/config.sh"
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

  # Sandbox for tier 3's `xcrun simctl shutdown`. Before this, `xcrun` was
  # stubbed NOWHERE in tests/ -- the two SIMPIDS="" tests could reach the shutdown
  # branch and were safe only by the accident of MC_DRY_RUN=1, so a future test
  # that forgot the flag would have shut down a developer's booted simulators for
  # real. MC_XCRUN_BIN is the same indirection service.sh uses for launchctl and
  # brew: a guarantee rather than a PATH convention, so no test can reach the real
  # binary by forgetting anything.
  #
  # The stub answers all three calls memcap makes and logs every one, keyed on the
  # WHOLE argument list rather than "$2": `list devices booted` and `list devices
  # booted -j` differ only in the last word, and a stub that fed plain text to the
  # `-j` parse would make every device-level assertion below pass or fail
  # depending on whether jq happens to be installed on the machine running bats.
  FAKE_XCRUN_LOG="$BATS_TEST_TMPDIR/xcrun.calls"
  FAKE_XCRUN_BOOTED="$BATS_TEST_TMPDIR/xcrun.booted"
  FAKE_XCRUN_BOOTED_JSON="$BATS_TEST_TMPDIR/xcrun.booted.json"
  FAKE_XCRUN_SHUTDOWN_RC="$BATS_TEST_TMPDIR/xcrun.shutdown-rc"
  FAKE_XCRUN_SHUTDOWN_ERR="$BATS_TEST_TMPDIR/xcrun.shutdown-err"
  export FAKE_XCRUN_LOG FAKE_XCRUN_BOOTED FAKE_XCRUN_BOOTED_JSON
  export FAKE_XCRUN_SHUTDOWN_RC FAKE_XCRUN_SHUTDOWN_ERR
  : > "$FAKE_XCRUN_LOG"
  : > "$FAKE_XCRUN_BOOTED"
  : > "$FAKE_XCRUN_BOOTED_JSON"
  rm -f "$FAKE_XCRUN_SHUTDOWN_RC" "$FAKE_XCRUN_SHUTDOWN_ERR"
  MC_XCRUN_BIN="$BATS_TEST_TMPDIR/fake-xcrun"
  cat > "$MC_XCRUN_BIN" <<'SCRIPT'
#!/bin/sh
echo "$@" >> "$FAKE_XCRUN_LOG"
case "$*" in
  "simctl list devices booted -j") cat "$FAKE_XCRUN_BOOTED_JSON" 2>/dev/null ;;
  "simctl list devices booted") cat "$FAKE_XCRUN_BOOTED" 2>/dev/null ;;
  "simctl shutdown "*)
    if [ -f "$FAKE_XCRUN_SHUTDOWN_ERR" ]; then cat "$FAKE_XCRUN_SHUTDOWN_ERR" >&2; fi
    if [ -f "$FAKE_XCRUN_SHUTDOWN_RC" ]; then exit "$(cat "$FAKE_XCRUN_SHUTDOWN_RC")"; fi
    ;;
esac
exit 0
SCRIPT
  chmod +x "$MC_XCRUN_BIN"
  export MC_XCRUN_BIN

  # Both halves of the protected set, always defined. mc_classify emits them
  # together on every real pass, and mc_filter_protected now REFUSES to kill when
  # either is unset rather than defaulting an empty protected set -- a fail-open
  # default on the protection filter itself was one of this round's findings. A
  # test that wants protection sets one or both to a pid; the baseline is "no
  # protected pids, but classification did run".
  # shellcheck disable=SC2034  # consumed by mc_filter_protected, sourced from enforce.sh
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_filter_protected, sourced from enforce.sh
  PROTECTEDPIDS=""
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

# --- Heartbeat: mc_watch must stamp every path that completes -----------------
# `status`'s only way to tell "not running" from "nothing to do" is this stamp.
# It has to land on the paused and misconfigured-budget early returns too, not
# just the normal completion at the bottom -- those are exactly the states
# where a real user is most likely to be looking at `status` wondering whether
# the silence means the daemon died.
@test "watch stamps a heartbeat on a normal completed pass" {
  run "$MEMCAP_ROOT/bin/memcap" watch
  [ -f "$MEMCAP_STATE_HOME/memcap/last-pass" ]
}

@test "watch stamps a heartbeat on the paused early return" {
  "$MEMCAP_ROOT/bin/memcap" off
  run "$MEMCAP_ROOT/bin/memcap" watch
  [ -f "$MEMCAP_STATE_HOME/memcap/last-pass" ]
  "$MEMCAP_ROOT/bin/memcap" on
}

@test "watch stamps a heartbeat on the misconfigured-budget early return" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  cat > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" <<-'EOF'
	TOTAL_BUDGET_GB=10
	DOCKER_BUDGET_GB=10
	EOF
  run "$MEMCAP_ROOT/bin/memcap" watch
  [ "$status" -eq 1 ]
  [ -f "$MEMCAP_STATE_HOME/memcap/last-pass" ]
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
  wait_spawned "$victim"

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  # Tier 1 now has its own age gate (TIER1_MIN_AGE_SEC, default 300s), so a
  # freshly-spawned fixture would be spared for a reason that has nothing to do
  # with what this test is checking. Zeroed here; the gate itself is tested
  # directly further down.
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  TIER1_MIN_AGE_SEC=0
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
  wait_spawned "$victim"

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  # Tier 1 now has its own age gate (TIER1_MIN_AGE_SEC, default 300s), so a
  # freshly-spawned fixture would be spared for a reason that has nothing to do
  # with what this test is checking. Zeroed here; the gate itself is tested
  # directly further down.
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  TIER1_MIN_AGE_SEC=0
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
  wait_spawned "$victim"

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  # Tier 1 now has its own age gate (TIER1_MIN_AGE_SEC, default 300s), so a
  # freshly-spawned fixture would be spared for a reason that has nothing to do
  # with what this test is checking. Zeroed here; the gate itself is tested
  # directly further down.
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  TIER1_MIN_AGE_SEC=0
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
  wait_spawned "$victim"

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  # Tier 1 now has its own age gate (TIER1_MIN_AGE_SEC, default 300s), so a
  # freshly-spawned fixture would be spared for a reason that has nothing to do
  # with what this test is checking. Zeroed here; the gate itself is tested
  # directly further down.
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  TIER1_MIN_AGE_SEC=0
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
  wait_spawned "$victim"

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  # Tier 1 now has its own age gate (TIER1_MIN_AGE_SEC, default 300s), so a
  # freshly-spawned fixture would be spared for a reason that has nothing to do
  # with what this test is checking. Zeroed here; the gate itself is tested
  # directly further down.
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  TIER1_MIN_AGE_SEC=0
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
  wait_spawned "$victim"

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  # Tier 1 now has its own age gate (TIER1_MIN_AGE_SEC, default 300s), so a
  # freshly-spawned fixture would be spared for a reason that has nothing to do
  # with what this test is checking. Zeroed here; the gate itself is tested
  # directly further down.
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  TIER1_MIN_AGE_SEC=0
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
  wait_spawned "$victim"

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  # Tier 1 now has its own age gate (TIER1_MIN_AGE_SEC, default 300s), so a
  # freshly-spawned fixture would be spared for a reason that has nothing to do
  # with what this test is checking. Zeroed here; the gate itself is tested
  # directly further down.
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  TIER1_MIN_AGE_SEC=0
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
  wait_spawned "$victim"
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
  wait_spawned "$victim"
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
  wait_spawned "$victim1"
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim1"
  # A REAL (non-dry) kill, unlike the rest of this file: this is exactly what is
  # under test -- mc_log's kill-record line must fire every time, unthrottled --
  # and both victims are dummy perl processes this test owns and spawned itself,
  # never anything reachable from a real snapshot.
  # Tier 1 age gate zeroed -- see the anchoring test above.
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  TIER1_MIN_AGE_SEC=0
  MC_DRY_RUN=0
  mc_reap_orphans

  perl -e 'sleep 600' "$real_root" &
  victim2=$!
  wait_spawned "$victim2"
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim2"
  # Tier 1 age gate zeroed -- see the anchoring test above.
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  TIER1_MIN_AGE_SEC=0
  MC_DRY_RUN=0
  mc_reap_orphans

  rm -rf "$HOME/.mc-throttle-$$"

  run grep -c "tier1 orphan" "$MEMCAP_STATE_HOME/memcap/actions.log"
  [ "$output" = "2" ]
}

@test "THROTTLE: the tier3-decline and combined-cap keys throttle independently" {
  sleep 600 & agent=$!
  wait_spawned "$agent"
  # TIER3_REQUIRE_NO_SESSION=1 restores the pre-v0.3.0 blanket session veto --
  # a live agent session no longer declines tier 3 by itself otherwise. What
  # this test actually verifies (two throttle keys firing in the same pass
  # don't suppress each other) doesn't depend on which reason tier 3 declined
  # for; the escape hatch is the simplest way to still exercise the
  # tier3-agent-alive key specifically.
  run env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 TIER3_REQUIRE_NO_SESSION=1 bash -c "
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

# --- EPERM: watch must say when it cannot read Docker's settings at all -------
# The daemon-side half of the fix in tests/docker.bats. `watch` sources docker.sh
# (bin/memcap does) but every module-sourcing test below must do the same, or the
# `command -v mc_docker_ceiling_drift` guard in mc_watch skips the whole block
# and the test passes having exercised nothing.
#
# MC_DOCKER_STORE is always pointed somewhere deliberate here. Left alone it
# defaults to the REAL settings-store.json on the developer's machine, which is
# readable from a terminal and holds whatever ceiling that machine happens to
# have -- a test whose result depends on what the developer is running, which is
# the class of test AGENTS.md rules out.
mc_watch_modules() {
  printf "%s" "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    source '$MEMCAP_ROOT/libexec/docker.sh'
    mc_kill_over_budget() { :; }
    mc_record_roots() { :; }
  "
}

@test "EPERM: watch logs that the ceiling check is blind, once, and throttles it" {
  if [ "$(id -u)" -eq 0 ]; then skip "chmod 000 is not a barrier to root"; fi
  store="$BATS_TEST_TMPDIR/locked.json"
  printf '{"MemoryMiB": 6144}\n' > "$store"
  chmod 000 "$store"

  pass="$(mc_watch_modules)
    mc_ps_snapshot() { printf '9001 1 2000000 /usr/local/bin/claude\n'; }
    mc_watch
  "
  env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=4 MC_DRY_RUN=1 MC_DOCKER_STORE="$store" bash -c "$pass" >/dev/null
  log="$MEMCAP_STATE_HOME/memcap/actions.log"
  run grep -c "VM-ceiling check is blind" "$log"
  [ "$output" = "1" ]
  run cat "$log"
  assert_contains "$output" "macOS denies launchd agents access"
  assert_contains "$output" "memcap status"

  # Second pass, same window: one line per condition, not one per 60 seconds.
  # 186 combined-cap lines in eight days is what an unthrottled per-pass line
  # looks like in this log.
  env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=4 MC_DRY_RUN=1 MC_DOCKER_STORE="$store" bash -c "$pass" >/dev/null
  run grep -c "VM-ceiling check is blind" "$log"
  [ "$output" = "1" ]
}

@test "EPERM: with a cached reading watch logs the drift, not the blind line" {
  if [ "$(id -u)" -eq 0 ]; then skip "chmod 000 is not a barrier to root"; fi
  store="$BATS_TEST_TMPDIR/locked.json"
  printf '{"MemoryMiB": 6144}\n' > "$store"
  chmod 000 "$store"
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  printf '6144 %s\n' "$(( $(date +%s) - 7200 ))" > "$MEMCAP_STATE_HOME/memcap/docker-ceiling"

  pass="$(mc_watch_modules)
    mc_ps_snapshot() { printf '9001 1 2000000 /usr/local/bin/claude\n'; }
    mc_watch
  "
  env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=4 MC_DRY_RUN=1 MC_DOCKER_STORE="$store" bash -c "$pass" >/dev/null

  run cat "$MEMCAP_STATE_HOME/memcap/actions.log"
  # This is the line v0.5.1 promised and never once produced on the author's
  # machine: the daemon now reports the drift from the value `status` cached for
  # it, and says that is where the number came from.
  assert_contains "$output" "Docker is enforcing a 6 GB VM ceiling, not the 4 GB in your config"
  assert_contains "$output" "last read from a terminal, 2h ago"
  # The negative control: a cached reading is not a blind check, so the blind
  # line must NOT also fire. Two lines describing the same pass two different
  # ways is how a log stops being read.
  assert_not_contains "$output" "VM-ceiling check is blind"
}

@test "EPERM: a readable store clears the blind key so the next outage re-logs" {
  store="$BATS_TEST_TMPDIR/settings.json"
  printf '{"MemoryMiB": 4096}\n' > "$store"
  mkdir -p "$MEMCAP_STATE_HOME/memcap/log-throttle"
  # Both keys stamped as though an earlier pass had logged them. A window left
  # ticking after its condition has gone is how a state change gets swallowed.
  date +%s > "$MEMCAP_STATE_HOME/memcap/log-throttle/docker-ceiling-unreadable"
  date +%s > "$MEMCAP_STATE_HOME/memcap/log-throttle/docker-ceiling-drift"

  pass="$(mc_watch_modules)
    mc_ps_snapshot() { printf '9001 1 2000000 /usr/local/bin/claude\n'; }
    mc_watch
  "
  env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=4 MC_DRY_RUN=1 MC_DOCKER_STORE="$store" bash -c "$pass" >/dev/null

  [ ! -f "$MEMCAP_STATE_HOME/memcap/log-throttle/docker-ceiling-unreadable" ]
  # 4096 MiB is exactly the 4 GB the config asks for, so there is no drift either.
  [ ! -f "$MEMCAP_STATE_HOME/memcap/log-throttle/docker-ceiling-drift" ]
  run cat "$MEMCAP_STATE_HOME/memcap/actions.log"
  assert_not_contains "$output" "VM-ceiling check is blind"
  # And the pass that could read the file leaves the cache behind for the passes
  # that cannot -- this is the whole mechanism, seeded here by `watch` itself
  # because this test's shell CAN read the fixture store.
  run cat "$MEMCAP_STATE_HOME/memcap/docker-ceiling"
  assert_matches "$output" '^4096 [0-9]+$'
}

@test "EPERM: an unmanaged Docker is not told its ceiling check is blind" {
  if [ "$(id -u)" -eq 0 ]; then skip "chmod 000 is not a barrier to root"; fi
  # DOCKER_BUDGET_GB=0 is "memcap is not managing Docker". There is no ceiling
  # check to be blind about, so an unreadable settings file is not news.
  store="$BATS_TEST_TMPDIR/locked.json"
  printf '{"MemoryMiB": 6144}\n' > "$store"
  chmod 000 "$store"

  pass="$(mc_watch_modules)
    mc_ps_snapshot() { printf '9001 1 2000000 /usr/local/bin/claude\n'; }
    mc_watch
  "
  env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 MC_DOCKER_STORE="$store" bash -c "$pass" >/dev/null
  run cat "$MEMCAP_STATE_HOME/memcap/actions.log"
  assert_not_contains "$output" "VM-ceiling check is blind"
}

# --- ATTRIBUTION: the combined-over-cap line must name the real excess --------
# 186 of these lines since 08-27, every one of them blaming simulators. The
# morning that made it undeniable: combined 17.20 GB against a 16 GB cap, agents
# net of sims 9.45 GB, sims about 1.15 GB -- and Docker holding 6.6 GB against a
# 4 GB budget, because the ceiling had never been applied. Tier 3 could have
# reclaimed every simulator on the machine and it would STILL have been over the
# cap. The line promised a reclaim that could not happen, and never named the one
# action that would have fixed it.
#
# Three fixtures, one per attribution, with the store pointed at a path that does
# not exist so no drift string is appended and the assertions stay exact.
# Footprints are given in KB (1 GB = 1048576 KB) so the rendered GB figures are
# exact rather than nearly right.
@test "ATTRIBUTION: a Docker overage names Docker, not the simulators" {
  # claude 9.50 + sims 0.50 = 10.00 GB of agent footprint, net 9.50 against a
  # 12 GB agent budget (so tier 2 correctly declines), Docker 7.50 GB against a
  # 4 GB budget. Combined 17.50 against a 16 GB cap: an overage of 1.50 GB, of
  # which Docker is 3.50 GB over on its own and the simulators only 0.50 GB.
  pass="$(mc_watch_modules)
    mc_ps_snapshot() {
      printf '9001 1 9961472 /usr/local/bin/claude\n'
      printf '9002 1 524288 /path/ms-playwright/chromium/chrome\n'
      printf '9003 1 7864320 /Applications/Docker.app/Contents/MacOS/com.docker.backend\n'
    }
    mc_watch
  "
  env TOTAL_BUDGET_GB=16 DOCKER_BUDGET_GB=4 MC_DRY_RUN=1 \
    MC_DOCKER_STORE="$BATS_TEST_TMPDIR/no-such-store.json" bash -c "$pass" >/dev/null

  run cat "$MEMCAP_STATE_HOME/memcap/actions.log"
  assert_contains "$output" "combined 17.50 GB exceeds the 16 GB cap"
  assert_contains "$output" "the excess is Docker's: 7.50 GB against its 4 GB budget"
  assert_contains "$output" "No tier reclaims Docker memory"
  assert_contains "$output" "memcap docker apply"
  # The negative control, and the whole point of the finding: the promise that
  # tier 3 will fix this must not appear, because tier 3 cannot. Reclaiming
  # every simulator here would leave the machine 1.00 GB over its cap.
  assert_not_contains "$output" "the excess is simulator/browser memory"
  assert_not_contains "$output" "tier 3 will reclaim it"
}

@test "ATTRIBUTION: a simulator overage still reads exactly as it did" {
  # Docker present but INSIDE its budget (2 GB against 4), so the sim wording is
  # reached by the real arithmetic rather than by Docker being absent. claude
  # 11.50 + sims 3.00 = 14.50, net 11.50 under the 12 GB agent budget, Docker
  # 2.00: combined 16.50 against a 16 GB cap, an overage of 0.50 GB that the
  # simulators cover on their own.
  pass="$(mc_watch_modules)
    mc_ps_snapshot() {
      printf '9001 1 12058624 /usr/local/bin/claude\n'
      printf '9002 1 3145728 /path/ms-playwright/chromium/chrome\n'
      printf '9003 1 2097152 /Applications/Docker.app/Contents/MacOS/com.docker.backend\n'
    }
    mc_watch
  "
  env TOTAL_BUDGET_GB=16 DOCKER_BUDGET_GB=4 MC_DRY_RUN=1 \
    MC_DOCKER_STORE="$BATS_TEST_TMPDIR/no-such-store.json" bash -c "$pass" >/dev/null

  run cat "$MEMCAP_STATE_HOME/memcap/actions.log"
  assert_contains "$output" "combined 16.50 GB exceeds the 16 GB cap"
  assert_contains "$output" "the excess is simulator/browser memory tier 2 cannot reclaim"
  assert_contains "$output" "tier 3 will reclaim it once it has been idle past its grace"
  assert_not_contains "$output" "the excess is Docker's"
  assert_not_contains "$output" "memcap docker apply"
}

@test "ATTRIBUTION: when both contribute, both are named and only one is promised" {
  # claude 11.50 + sims 1.00 = 12.50, net 11.50 under the 12 GB budget, Docker
  # 5.00 against 4: combined 17.50 against a 16 GB cap. The overage is 1.50 GB
  # and NEITHER component covers it alone -- Docker is 1.00 GB over, the sims are
  # 1.00 GB. Saying either one is "the excess" would be false.
  pass="$(mc_watch_modules)
    mc_ps_snapshot() {
      printf '9001 1 12058624 /usr/local/bin/claude\n'
      printf '9002 1 1048576 /path/ms-playwright/chromium/chrome\n'
      printf '9003 1 5242880 /Applications/Docker.app/Contents/MacOS/com.docker.backend\n'
    }
    mc_watch
  "
  env TOTAL_BUDGET_GB=16 DOCKER_BUDGET_GB=4 MC_DRY_RUN=1 \
    MC_DOCKER_STORE="$BATS_TEST_TMPDIR/no-such-store.json" bash -c "$pass" >/dev/null

  run cat "$MEMCAP_STATE_HOME/memcap/actions.log"
  assert_contains "$output" "combined 17.50 GB exceeds the 16 GB cap"
  assert_contains "$output" "Docker is 1.00 GB over its 4 GB budget and 1.00 GB is simulator/browser memory"
  assert_contains "$output" "tier 3 can reclaim at most the latter"
  assert_contains "$output" "the Docker part needs: memcap docker apply"
  assert_not_contains "$output" "the excess is Docker's"
  assert_not_contains "$output" "the excess is simulator/browser memory"
}

@test "ATTRIBUTION: independent rounding cannot blame a Docker that is exactly at budget" {
  # agent_gb, sim_gb and agent_net_gb are each rounded to two decimals on their
  # own, and the overage is computed from the rounded combined figure, so the
  # identity "sims cover the overage when Docker is within budget" holds only in
  # exact arithmetic. Here: claude 12.002 GB + sims 0.994 GB = 12.996 GB gross,
  # which renders as 13.00; sims render as 0.99; net renders as 12.00 (inside the
  # 12 GB budget, so this branch is reached); Docker exactly 4.00 GB against 4.
  # Combined 17.00, overage 1.00, and 0.99 < 1.00 -- so a test on "do the sims
  # cover it" fails by a rounding penny and the pass fell through to the variant
  # that reads "Docker is 0.00 GB over its 4 GB budget ... the Docker part needs:
  # memcap docker apply". A false Docker blame from the change meant to end
  # misattribution. Docker being within budget is decisive on its own.
  pass="$(mc_watch_modules)
    mc_ps_snapshot() {
      printf '9001 1 12585006 /usr/local/bin/claude\n'
      printf '9002 1 1042285 /path/ms-playwright/chromium/chrome\n'
      printf '9003 1 4194304 /Applications/Docker.app/Contents/MacOS/com.docker.backend\n'
    }
    mc_watch
  "
  env TOTAL_BUDGET_GB=16 DOCKER_BUDGET_GB=4 MC_DRY_RUN=1 \
    MC_DOCKER_STORE="$BATS_TEST_TMPDIR/no-such-store.json" bash -c "$pass" >/dev/null

  run cat "$MEMCAP_STATE_HOME/memcap/actions.log"
  assert_contains "$output" "combined 17.00 GB exceeds the 16 GB cap"
  assert_contains "$output" "the excess is simulator/browser memory tier 2 cannot reclaim"
  assert_not_contains "$output" "Docker is 0.00 GB over"
  assert_not_contains "$output" "memcap docker apply"
}

@test "ATTRIBUTION: the Docker variant carries the drift that explains it" {
  # "Docker is over its budget" and "the ceiling in your config was never
  # applied" are the same sentence read from two ends. The same fixture as the
  # Docker test above, with Docker's own settings readable and holding 6 GB
  # against the 4 GB the config asks for.
  store="$BATS_TEST_TMPDIR/settings.json"
  printf '{"MemoryMiB": 6144}\n' > "$store"
  pass="$(mc_watch_modules)
    mc_ps_snapshot() {
      printf '9001 1 9961472 /usr/local/bin/claude\n'
      printf '9002 1 524288 /path/ms-playwright/chromium/chrome\n'
      printf '9003 1 7864320 /Applications/Docker.app/Contents/MacOS/com.docker.backend\n'
    }
    mc_watch
  "
  env TOTAL_BUDGET_GB=16 DOCKER_BUDGET_GB=4 MC_DRY_RUN=1 MC_DOCKER_STORE="$store" bash -c "$pass" >/dev/null

  run grep "exceeds the 16 GB cap" "$MEMCAP_STATE_HOME/memcap/actions.log"
  assert_contains "$output" "the excess is Docker's"
  assert_contains "$output" "Docker is enforcing a 6 GB VM ceiling, not the 4 GB in your config"
}

@test "ATTRIBUTION: the notification matches the variant the log chose" {
  # MC_DRY_RUN=0 to prove the REAL path notifies, as the SILENT-GAP tests do.
  # Nothing here can touch anything real: the snapshot is a fixture, tier 2 is
  # stubbed out, and osascript is a capture file. The notification is the only
  # part of this a user sees on the day it happens, so it must not still be
  # blaming simulators after the log line stopped.
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  capture="$BATS_TEST_TMPDIR/osascript-arg"
  cat > "$fakebin/osascript" <<SCRIPT
#!/usr/bin/env bash
printf '%s' "\$2" >> "$capture"
SCRIPT
  chmod +x "$fakebin/osascript"

  pass="$(mc_watch_modules)
    mc_ps_snapshot() {
      printf '9001 1 9961472 /usr/local/bin/claude\n'
      printf '9002 1 524288 /path/ms-playwright/chromium/chrome\n'
      printf '9003 1 7864320 /Applications/Docker.app/Contents/MacOS/com.docker.backend\n'
    }
    mc_watch
  "
  env TOTAL_BUDGET_GB=16 DOCKER_BUDGET_GB=4 MC_DRY_RUN=0 PATH="$fakebin:$PATH" \
    MC_DOCKER_STORE="$BATS_TEST_TMPDIR/no-such-store.json" bash -c "$pass" >/dev/null

  run cat "$capture"
  assert_contains "$output" "Docker is holding 7.50 GB against its 4 GB budget"
  assert_contains "$output" "memcap docker apply"
  assert_not_contains "$output" "simulator/browser memory"
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
    ppid=)  mode=ppid ;;
    [0-9]*) pid="$a" ;;
  esac
done
# mc_self_ancestry walks memcap's own parents through ps and now FAILS CLOSED on
# a ps that answers nothing -- the old version silently reduced the protected set
# to $$ alone. A stub that does not answer `-o ppid=` would therefore make
# mc_kill_pids refuse outright, for a reason unrelated to what these tests check.
case "$mode" in
  ppid) echo " 1"; exit 0 ;;
esac
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

  # Straight to the stubbed `ps -o rss=` fallback, which is the reading this test
  # is actually about. Without it mc_footprint_kb runs the REAL `top` first, and
  # pid 123 is a real process on macOS -- so the number being ranked would come
  # from the machine rather than the fixture.
  # shellcheck disable=SC2034  # read by mc_ps_snapshot, sourced from measure.sh
  MC_NO_TOP=1
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
    ppid=)  mode=ppid ;;
    [0-9]*) pid="$a" ;;
  esac
done
# mc_self_ancestry walks memcap's own parents through ps and now FAILS CLOSED on
# a ps that answers nothing -- the old version silently reduced the protected set
# to $$ alone. A stub that does not answer `-o ppid=` would therefore make
# mc_kill_pids refuse outright, for a reason unrelated to what these tests check.
case "$mode" in
  ppid) echo " 1"; exit 0 ;;
esac
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
  # Poll for the child rather than sleeping a fixed 0.3s: this waits on a
  # grandchild being forked, which under full-suite load can outlast any
  # constant anyone picks. Same race wait_spawned exists for.
  agent_child=""
  for _ in $(seq 1 250); do
    agent_child=$(pgrep -P "$server" | head -1)
    [ -n "$agent_child" ] && break
    sleep 0.02
  done
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
  # Its only child has exited, so the shell's `wait` finishes on its own.
  # Reap it instead of racing a second kill against that normal exit.
  wait "$server" 2>/dev/null || true

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
  wait_spawned "$victim"
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
  # v0.3.0: mc_reap_sims no longer wipes the stamp dir/file itself -- a dry
  # run changes nothing for real, so the pid is still alive and its stamp
  # still exists. Stale stamps are pruned on a LATER pass once the pid is
  # genuinely gone (kill -0 fails), not unconditionally at kill time.
  [ -f "$stamp" ]
}

# --- Final review, I6: the idle grace stamp must be per-pid, not machine-wide -
# One shared `sims-idle` stamp meant a hand-booted simulator inherited whichever
# timestamp an unrelated, already-idle sim process had accumulated, and could be
# reaped with none of its own grace. Each tracked sim pid gets its own stamp.
#
# What I6 got WRONG, and this round reverses: it also made readiness a global
# conjunction -- no pid could be reaped until EVERY tracked pid had individually
# cleared the grace. That was chosen as the conservative reading of "protect the
# newest one", and measured against real churn it is not conservative, it is
# inert. 239 distinct sim-classified pids appeared on this machine in 9.5
# minutes, including 28 Playwright Firefox processes born and dead inside a
# single 60-second pass; under a global conjunction one fresh renderer protects
# an unrelated stale Chrome forever, which is most of why tier 3 reclaimed
# nothing even in the passes where it was not blocked outright. Readiness is now
# per pid: each earns, and spends, its own clock.
@test "PER-PID: a freshly-tracked sim does not hold back one that has cleared its own grace" {
  perl -e 'sleep 600' "ms-playwright-fixture" & old=$!
  perl -e 'sleep 600' "ms-playwright-other-fixture" & fresh=$!
  wait_spawned "$old" "$fresh"
  AGENTPIDS=""
  SIMPIDS="$old $fresh"
  MC_DRY_RUN=1
  # $old gets a stamp far enough in the past to have cleared any real-world grace
  # on its own. $fresh gets none: it is tracked for the first time on this very
  # pass, exactly like a browser just launched. Both match the reclaim pattern, so
  # the only thing separating them is their own clocks.
  mkdir -p "$(mc_sims_idle_dir)"
  # Second field is the CPU-time baseline -- set absurdly high so $old's real,
  # near-zero CPU usage can never look like it "advanced" past this baseline and
  # reset the clock, regardless of how long this test takes to run.
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$old")"

  run mc_reap_sims

  kill "$old" "$fresh" 2>/dev/null

  assert_contains "$output" "would kill"
  assert_contains "$output" "$old"
  assert_not_contains "$output" "$fresh"
  [ -f "$(mc_sims_idle_stamp "$fresh")" ]
}

@test "PER-PID: the freshly-tracked sim is reclaimed on a later pass, once its own clock clears" {
  perl -e 'sleep 600' "ms-playwright-fixture" & fresh=$!
  wait_spawned "$fresh"
  AGENTPIDS=""
  SIMPIDS="$fresh"
  MC_DRY_RUN=1

  run mc_reap_sims
  # assert_not_contains, not a standalone `[[ ]]`: this check's own $output is
  # about to be overwritten by the next `run` below, so it can't share a single
  # combined [[ ]] with the checks after it -- but it's still a real function
  # call, so it correctly participates in bash 3.2's error handling where a bare
  # `[[ ]]` would not (see classify.bats).
  assert_not_contains "$output" "would kill"

  # Grace set to 0 rather than sleeping for real: the stamp written on the poll
  # above is now, by definition, at least zero seconds old.
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIM_IDLE_GRACE_SEC=0
  run mc_reap_sims

  kill "$fresh" 2>/dev/null

  assert_contains "$output" "would kill"
  assert_contains "$output" "$fresh"
}

@test "I6: mc_hands_on_mobile also treats Simulator.app itself as hands-on" {
  # setup_common forces MC_HANDS_ON_MOBILE=0 suite-wide (an ambient real
  # Xcode/Simulator.app/Android Studio on whatever machine runs the suite
  # would otherwise make this and every other tier-3 test non-deterministic).
  # This test exists specifically to exercise the real pgrep pattern, so it
  # unsets the override for its own safe, synthetic fixture.
  unset MC_HANDS_ON_MOBILE
  perl -e '$0=shift; sleep 600' "/Applications/Xcode.app/Contents/Developer/Applications/Simulator.app/Contents/MacOS/Simulator sleep 600" &
  victim=$!
  wait_spawned "$victim"
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
  wait_spawned "$victim"
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
  wait_spawned "$victim"
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

# --- v0.3.0: tier 3 must not decline, or wipe idle history, on a live session
# alone --------------------------------------------------------------------
# Production data: tier 3 fired zero times in 1,643 opportunities on a machine
# that always has an agent session open, and the `rm -rf "$dir"` on every
# decline reset every sim's idle clock to zero on every single pass -- tier 3
# could not fire even in principle under continuous agent use. "An agent
# session is alive" is no longer sufficient reason to decline by itself, and
# a decline for any OTHER reason must not erase idle history that was
# genuinely accumulating.
@test "a live agent session alone no longer declines tier 3 or clears idle stamps" {
  # A real, currently-tracked sim pid, not a fabricated one: mc_reap_sims's own
  # pruning step (unrelated to any veto -- it always runs first) removes any
  # stamp whose pid isn't both alive AND in $SIMPIDS, so a fake never-alive
  # pid's stamp would be pruned regardless of what this test is actually
  # trying to verify.
  perl -e 'sleep 600' "ms-playwright-fixture" & sim=$!
  sleep 600 & agent=$!
  wait_spawned "$sim" "$agent"
  # shellcheck disable=SC2034  # consumed by mc_no_live_session, sourced from enforce.sh
  AGENTPIDS="$agent"
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$sim"
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  run mc_reap_sims

  kill "$sim" "$agent" 2>/dev/null

  assert_not_contains "$output" "declining"
  [ -f "$(mc_sims_idle_stamp "$sim")" ]
  run ! grep -q "declining -- an agent session is alive" "$(mc_state_dir)/actions.log"
}

# The escape hatch restores the OLD, pre-v0.3.0 behavior exactly, for anyone
# who wants the maximally conservative posture back.
@test "TIER3_REQUIRE_NO_SESSION=1 restores the blanket session veto and logs why" {
  sleep 600 & agent=$!
  wait_spawned "$agent"
  # shellcheck disable=SC2034  # consumed by mc_no_live_session, sourced from enforce.sh
  AGENTPIDS="$agent"
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  TIER3_REQUIRE_NO_SESSION=1
  run mc_reap_sims

  kill "$agent" 2>/dev/null

  # grep -q, not [[ ]]: a real external command's failure correctly trips bash
  # 3.2's error handling regardless of position, unlike a bare `[[ ]]` (see the
  # SILENT-GAP tests above for the full explanation) -- moot here since this is
  # already the test's last check, but kept consistent with the rest of this file.
  grep -q "declining -- an agent session is alive" "$(mc_state_dir)/actions.log"
}

@test "tier3: hands-on mobile work (Simulator.app) declines and logs why" {
  # See I6's mc_hands_on_mobile test above for why this override exists and
  # is unset here.
  unset MC_HANDS_ON_MOBILE
  perl -e '$0=shift; sleep 600' "/Applications/Xcode.app/Contents/Developer/Applications/Simulator.app/Contents/MacOS/Simulator sleep 600" &
  sim=$!
  wait_spawned "$sim"
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

# --- v0.3.0: mc_cputime_secs must parse `ps -o time=`'s actual shape --------
# Confirmed empirically (not assumed) that `ps -o time=` is NOT the same shape
# as `ps -o etime=`: it never rolls into an hour/day segment -- a process at 6
# days' uptime showed "2960:52.31", not "49:15:52" or "2-01:15:52". Plain
# MINUTES:SECONDS(.hundredths), minutes unbounded.
@test "mc_cputime_secs parses ps -o time='s actual MM:SS(.ff) shape, unbounded minutes" {
  run mc_cputime_secs "0:00.02"
  [ "$output" = "0" ]
  run mc_cputime_secs "1:05.99"
  [ "$output" = "65" ]
  # A process running for days: minutes alone exceed 999, never rolling into
  # an hour or day segment the way etime does.
  run mc_cputime_secs "2960:52.31"
  [ "$output" = "177652" ]
}

@test "mc_cputime_secs fails closed on empty or malformed input" {
  run mc_cputime_secs ""
  [ "$status" -ne 0 ]
  run mc_cputime_secs "not-a-time"
  [ "$status" -ne 0 ]
}

# --- v0.3.0: the CPU-flat idleness test itself -------------------------------
# "An agent session is alive" was a bad proxy for "a simulator is in use" --
# it never released. A booted-but-unused simulator burns approximately zero
# CPU; that is the signal that replaces the blanket veto. These pre-seed the
# stamp file directly with a controlled epoch and CPU baseline rather than
# waiting out a real SIM_IDLE_GRACE_SEC or sleeping for real CPU to accrue.
@test "a sim with flat CPU across the grace is reclaimed" {
  perl -e 'sleep 600' "ms-playwright-fixture" & sim=$!
  wait_spawned "$sim"
  AGENTPIDS=""
  SIMPIDS="$sim"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  # Timestamp far enough in the past to have cleared any real-world grace;
  # baseline set absurdly high so the fixture's own real (near-zero) CPU
  # usage can never look like it "advanced" past it, regardless of how long
  # this test happens to take to run.
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$sim")"

  run mc_reap_sims

  kill "$sim" 2>/dev/null

  assert_contains "$output" "would kill"
  assert_contains "$output" "$sim"
}

@test "a sim whose CPU advanced is not reclaimed, and its clock resets" {
  # A tight loop actually burns CPU, unlike sleep -- this is what "in use"
  # looks like. SIM_ACTIVE_CPU_SEC=0 makes any nonzero advance count as
  # activity, so the test doesn't need to wait out a multi-second threshold
  # for a deterministic result.
  perl -e 'my $e=time()+2; while(time()<$e){1+1} sleep 600' "ms-playwright-fixture" & sim=$!
  wait_spawned "$sim"
  AGENTPIDS=""
  SIMPIDS="$sim"
  MC_DRY_RUN=1
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIM_ACTIVE_CPU_SEC=0
  mkdir -p "$(mc_sims_idle_dir)"
  # Old timestamp (already past any real grace) but a baseline of 0 -- any
  # CPU the busy loop has accumulated by the time this runs looks like an
  # advance against that baseline.
  printf '1 0\n' > "$(mc_sims_idle_stamp "$sim")"

  run mc_reap_sims

  kill "$sim" 2>/dev/null

  assert_not_contains "$output" "would kill"
  # The clock reset: read the stamp back and confirm its timestamp moved off
  # the deliberately-ancient "1" this test seeded it with.
  read -r new_first _ < "$(mc_sims_idle_stamp "$sim")"
  [ "$new_first" != "1" ]
}

# --- v0.3.0: each veto blocks independently, and none of them wipe stamps ---
@test "active mobile tooling (via the override) blocks the reap and preserves its stamp" {
  perl -e 'sleep 600' "ms-playwright-fixture" & sim=$!
  wait_spawned "$sim"
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$sim"
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  # shellcheck disable=SC2034  # consumed by mc_active_mobile_tooling, sourced from enforce.sh
  MC_ACTIVE_MOBILE_TOOLING=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$sim")"

  run mc_reap_sims

  kill "$sim" 2>/dev/null

  assert_not_contains "$output" "would kill"
  [ -f "$(mc_sims_idle_stamp "$sim")" ]
  grep -q "declining -- active mobile tooling detected" "$(mc_state_dir)/actions.log"
}

# Spot-checks the real pattern match itself (not just the override plumbing)
# for one representative case -- maestro, since it runs as a JVM process and
# is matched by argv, the least "obviously correct at a glance" of the five.
# Unsets the suite-wide override (see the mc_hands_on_mobile test above for
# why it exists) so this fixture exercises the actual regex.
@test "mc_active_mobile_tooling's real pattern matches a maestro-shaped fixture" {
  unset MC_ACTIVE_MOBILE_TOOLING
  perl -e 'sleep 600' ".maestro/lib/fake.jar" & victim=$!
  wait_spawned "$victim"
  run mc_active_mobile_tooling
  kill "$victim" 2>/dev/null
  [ "$status" -eq 0 ]
}

# --- Production bug found post-ship: matching a tool's mere EXISTENCE vetoed
# tier 3 permanently the moment one of them runs as a background service, not
# just while driving a simulator. maestro's own MCP server (`java ...
# maestro.cli.AppKt mcp`) idles for days between requests on the author's real
# machine and matched the maestro pattern, reproducing the exact "tier 3 fired
# zero times in 1,643 opportunities" bug this whole change exists to fix --
# just with a different permanent veto standing in for the old one. Both tests
# below assert against mc_reap_sims ITSELF with a long-lived fixture, not
# against mc_active_mobile_tooling in isolation -- an isolated assertion is
# exactly what let the first version of this ship with the bug still live.
@test "a long-lived, CPU-idle maestro-pattern process does not permanently veto a reclaim" {
  unset MC_ACTIVE_MOBILE_TOOLING
  # Narrowed to this test's own fixture. The real pattern also matches the
  # maestro MCP servers running on the author's machine, and those are live
  # processes: if one of them does any real work between this test's two passes,
  # the veto correctly reports "active" and the test fails for a reason that has
  # nothing to do with what it is checking. Observed as an intermittent failure
  # under full-suite load. The pattern itself is spot-checked against a
  # maestro-shaped fixture by its own test above.
  # shellcheck disable=SC2034  # consumed by mc_mobile_tooling_pids
  MC_MOBILE_TOOLING_ARGV_PATTERN="memcap-idle-fixture-$$-$BATS_TEST_NUMBER"
  # shellcheck disable=SC2034  # consumed by mc_mobile_tooling_pids
  MC_MOBILE_TOOLING_EXACT=''
  # Mimics maestro's MCP server: matches the maestro pattern, never exits,
  # never does meaningful CPU work -- exactly the shape of the real process
  # that reproduced this bug in production.
  perl -e 'sleep 600' "$MC_MOBILE_TOOLING_ARGV_PATTERN" & tooling=$!
  perl -e 'sleep 600' "ms-playwright-fixture" & sim=$!
  wait_spawned "$tooling" "$sim"
  AGENTPIDS=""
  SIMPIDS="$sim"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$sim")"

  # First pass: the tooling process has never been tracked before, so it
  # conservatively counts as active -- the same bootstrapping mc_reap_sims
  # itself uses for a freshly-seen sim ("haven't watched it long enough to
  # call it idle yet"). The sim is already past its own grace, proving the
  # reap is blocked by the TOOLING veto specifically, not by the sim's clock.
  run mc_reap_sims
  assert_not_contains "$output" "would kill"

  # Second pass: the tooling's own idle window has now cleared (zeroed here
  # rather than waiting out the real default) and its CPU is still flat --
  # the reap proceeds.
  # shellcheck disable=SC2034  # consumed by mc_active_mobile_tooling, sourced from enforce.sh
  MOBILE_TOOLING_IDLE_SEC=0
  run mc_reap_sims

  kill "$tooling" "$sim" 2>/dev/null

  assert_contains "$output" "would kill"
  assert_contains "$output" "$sim"
}

@test "an actively busy maestro-pattern process keeps vetoing a reclaim" {
  unset MC_ACTIVE_MOBILE_TOOLING
  # Narrowed to this test's own fixture -- see the previous test for why.
  # shellcheck disable=SC2034  # consumed by mc_mobile_tooling_pids
  MC_MOBILE_TOOLING_ARGV_PATTERN="memcap-busy-fixture-$$-$BATS_TEST_NUMBER"
  # shellcheck disable=SC2034  # consumed by mc_mobile_tooling_pids
  MC_MOBILE_TOOLING_EXACT=''
  # A tight loop actually burns CPU, unlike sleep -- this is what a real
  # maestro flow driving a simulator looks like, as opposed to its MCP server
  # idling between requests.
  perl -e 'my $e=time()+2; while(time()<$e){1+1} sleep 600' "$MC_MOBILE_TOOLING_ARGV_PATTERN" & tooling=$!
  perl -e 'sleep 600' "ms-playwright-fixture" & sim=$!
  wait_spawned "$tooling" "$sim"
  AGENTPIDS=""
  SIMPIDS="$sim"
  MC_DRY_RUN=1
  # shellcheck disable=SC2034  # consumed by mc_active_mobile_tooling, sourced from enforce.sh
  SIM_ACTIVE_CPU_SEC=0
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$sim")"

  run mc_reap_sims
  # shellcheck disable=SC2034,SC2030  # consumed by mc_active_mobile_tooling,
  # sourced from enforce.sh; each bats @test body is its own subshell, so this
  # cannot leak into the default-window test below (which asserts it is unset).
  MOBILE_TOOLING_IDLE_SEC=0
  run mc_reap_sims

  kill "$tooling" "$sim" 2>/dev/null

  assert_not_contains "$output" "would kill"
}

# --- v0.6.0: the tooling veto had no hysteresis, because its window was
# shorter than one enforcement pass. Nine days of production logs carried 544
# "tier3: declining -- active mobile tooling detected" lines, alternating
# minute-to-minute with the hands-on veto: a pass takes ~64 seconds (launchd
# coalesces the 60s StartInterval), so at MOBILE_TOOLING_IDLE_SEC=60 an idle
# maestro MCP server that handled a single request vetoed for exactly one pass
# and stopped vetoing on the very next one -- and since each transition clears
# the throttle key, every flap logged.
#
# Both assertions below deliberately leave MOBILE_TOOLING_IDLE_SEC UNSET, which
# is the whole point: every other test in this file sets it, so nothing else
# here would notice the default changing (or reverting). 200s must still veto --
# it would not have at the old default of 60 -- and 400s must not, which keeps
# this a test of the window rather than of a veto that never releases.
@test "MOBILE_TOOLING_IDLE_SEC's default outlasts one enforcement pass" {
  unset MC_ACTIVE_MOBILE_TOOLING
  # Not merely absent: asserted absent, so a future setup() that sets it turns
  # this test red rather than quietly making it test something else.
  unset MOBILE_TOOLING_IDLE_SEC
  # shellcheck disable=SC2031  # a @test body is its own subshell; the earlier
  # test's assignment cannot reach this one, which is the property being pinned
  [ -z "${MOBILE_TOOLING_IDLE_SEC:-}" ]
  # Narrowed to this test's own fixture -- see the two tests above for why the
  # real pattern cannot be used here (this machine runs maestro MCP servers).
  # shellcheck disable=SC2034  # consumed by mc_mobile_tooling_pids
  MC_MOBILE_TOOLING_ARGV_PATTERN="memcap-hysteresis-fixture-$$-$BATS_TEST_NUMBER"
  # shellcheck disable=SC2034  # consumed by mc_mobile_tooling_pids
  MC_MOBILE_TOOLING_EXACT=''
  # The shape of the real process: matches the maestro pattern, never exits,
  # burns no meaningful CPU between requests.
  perl -e 'sleep 600' "$MC_MOBILE_TOOLING_ARGV_PATTERN" & tooling=$!
  perl -e 'sleep 600' "ms-playwright-fixture" & sim=$!
  wait_spawned "$tooling" "$sim"
  AGENTPIDS=""
  SIMPIDS="$sim"
  MC_DRY_RUN=1
  # The sim is well past its own grace, so whatever blocks the reap here is the
  # TOOLING veto and not the sim's clock.
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$sim")"
  mkdir -p "$(mc_mobile_tooling_idle_dir)"
  now=$(date +%s)

  # Last burst 200s ago -- more than three enforcement passes back, and still
  # inside the 300s default. A CPU baseline above anything the fixture could
  # have burned means the clock is never seen to advance, so the only thing
  # deciding this is the window.
  printf '%s 999999\n' "$((now - 200))" > "$(mc_mobile_tooling_idle_stamp "$tooling")"
  run mc_reap_sims
  inside="$output"

  # Same process, same flat CPU, 400s since its last burst: past the default,
  # so it releases.
  printf '%s 999999\n' "$((now - 400))" > "$(mc_mobile_tooling_idle_stamp "$tooling")"
  run mc_reap_sims
  outside="$output"

  # Every assertion happens AFTER this, deliberately. A `sleep 600` fixture that
  # outlives its test holds bats's captured output open, so a test that returns
  # early on a failed assertion hangs the run for ten minutes instead of
  # reporting the failure -- observed while writing this test's negative
  # control, and not fixed by redirecting the fixture's own stdout/stderr. A
  # guard that hangs rather than going red is the "check that cannot fail" trap
  # in a different costume, so nothing here can fail before the kill.
  kill "$tooling" "$sim" 2>/dev/null

  assert_not_contains "$inside" "would kill"
  grep -q "declining -- active mobile tooling detected" "$(mc_state_dir)/actions.log"
  assert_contains "$outside" "would kill"
  assert_contains "$outside" "$sim"
}

@test "hands-on mobile work blocks the reap and preserves its stamp" {
  unset MC_HANDS_ON_MOBILE
  perl -e 'sleep 600' "ms-playwright-fixture" & sim=$!
  perl -e '$0=shift; sleep 600' "/Applications/Xcode.app/Contents/Developer/Applications/Simulator.app/Contents/MacOS/Simulator sleep 600" & hands_on=$!
  wait_spawned "$sim" "$hands_on"
  AGENTPIDS=""
  SIMPIDS="$sim"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$sim")"

  run mc_reap_sims

  kill "$sim" "$hands_on" 2>/dev/null

  assert_not_contains "$output" "would kill"
  [ -f "$(mc_sims_idle_stamp "$sim")" ]
}

# --- v0.3.0: a real reclaim logs enough detail to audit after the fact ------
@test "a reclaim logs the pid, how long it was idle, and its flat CPU baseline" {
  perl -e 'sleep 600' "ms-playwright-fixture" & sim=$!
  wait_spawned "$sim"
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$sim"
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 42\n' > "$(mc_sims_idle_stamp "$sim")"

  run mc_reap_sims

  kill "$sim" 2>/dev/null

  log="$MEMCAP_STATE_HOME/memcap/actions.log"
  grep -q "tier3: reclaiming pid $sim" "$log"
  grep -q "CPU flat at ~42s accumulated" "$log"
}

# --- mc_pid_cwd must be resolved once per orphan, never per (orphan x root) ---
# mc_pid_cwd spawns lsof, measured at 35.8ms on the author's machine. The cwd
# fallback that catches symlinked project roots was first written inside the
# per-root loop, where it runs orphans x roots times: 388 orphans (the leak that
# motivated this tool) against 12 recorded roots is 4,656 spawns and a 167-second
# pass, against a 60-second service interval. Neither the cwd nor the argv line
# depends on $root, so both are hoisted. This test pins that: it counts calls and
# fails if either is moved back inside the loop.
@test "mc_pid_cwd is called at most once per orphan, not once per root" {
  local i
  for i in 1 2 3 4 5; do
    mkdir -p "$HOME/.mc-cost-$$/r$i"
    mc_record_root "$HOME/.mc-cost-$$/r$i"
  done

  # Victim lives under NONE of the roots, so every root misses and the cwd
  # fallback is reached on all five -- the worst case for call count.
  mkdir -p "$HOME/.mc-cost-other-$$/proj"
  ( cd "$HOME/.mc-cost-other-$$/proj" && exec perl -e 'sleep 600' ) &
  victim=$!
  wait_spawned "$victim"

  # Counting stub, defined AFTER enforce.sh was sourced in setup() so sourcing
  # cannot clobber it. Delegates to nothing -- the return value is irrelevant
  # here, only how many times it is asked.
  MC_CWD_CALLS="$BATS_TEST_TMPDIR/cwd-calls"
  : > "$MC_CWD_CALLS"
  mc_pid_cwd() { echo x >> "$MC_CWD_CALLS"; printf '%s' ""; }

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  # Tier 1 now has its own age gate (TIER1_MIN_AGE_SEC, default 300s), so a
  # freshly-spawned fixture would be spared for a reason that has nothing to do
  # with what this test is checking. Zeroed here; the gate itself is tested
  # directly further down.
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  TIER1_MIN_AGE_SEC=0
  MC_DRY_RUN=1
  run mc_reap_orphans

  calls=$(wc -l < "$MC_CWD_CALLS" | tr -d ' ')

  kill "$victim" 2>/dev/null
  rm -rf "$HOME/.mc-cost-$$" "$HOME/.mc-cost-other-$$"

  # One orphan, five roots. Hoisted: at most 1. Inside the loop: 5.
  [ "$calls" -le 1 ] || {
    echo "mc_pid_cwd called $calls times for 1 orphan across 5 roots" >&2
    echo "it must be resolved once per orphan, not once per (orphan x root)" >&2
    return 1
  }
}

# =============================================================================
# THE HEADLINE: tier 3 has never fired, in the tool's entire life.
#
# Eleven days of production logs on this machine: 1,973 tier-3 declines, zero
# reclaims. The cause was `kill -0` used as a LIVENESS test. It conflates EPERM
# ("alive, but not yours to signal") with ESRCH ("dead"), and simdiskimaged --
# root-owned, listed in MC_SIM_EXE, present on any Mac with Xcode installed --
# entered SIMPIDS on every single pass. Every pass the prune declared it dead and
# deleted its idle stamp; the tracking loop then re-saw it as never-tracked and
# reset the global readiness flag; and the function returned through the one
# unlogged return it had. It was blocked by a process that is not even in
# MC_SIM_KILL_PATTERN and could never have been killed if it had been selected.
# =============================================================================

@test "HEADLINE: kill -0 and mc_pid_alive disagree about a root-owned process -- that disagreement is the bug" {
  # pid 1 (launchd) is the reproduction that is guaranteed present on any macOS
  # machine: alive, root-owned, unsignalable by this user. simdiskimaged behaves
  # identically and is what actually did it here.
  run mc_pid_alive 1
  [ "$status" -eq 0 ]
  run kill -0 1
  [ "$status" -ne 0 ]
}

@test "HEADLINE: the prune keeps the idle stamp of an alive-but-unsignalable sim pid" {
  # Under `kill -0` this stamp was deleted on every pass, so the pid was re-seen
  # as never-tracked on every pass, and its clock could never advance past zero.
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="1"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp 1)"

  run mc_reap_sims

  [ -f "$(mc_sims_idle_stamp 1)" ]
}

@test "HEADLINE: an unsignalable pid is tracked but is never a kill target" {
  # MC_SIM_KILL_PATTERN is overridden to match launchd so the pattern test PASSES
  # and the permission test is the thing being exercised -- otherwise this would
  # be rejected one line earlier for a reason that is not the one under test.
  # shellcheck disable=SC2034  # consumed by mc_sim_is_target, sourced from enforce.sh
  MC_SIM_KILL_PATTERN='launchd'
  run mc_sim_is_target 1 "/sbin/launchd"
  [ "$status" -ne 0 ]
  grep -q "memcap cannot signal it" "$(mc_state_dir)/actions.log"
}

@test "HEADLINE: a non-target sim pid no longer holds back a genuine reclaim" {
  # Both pids are ready (ancient stamps). Only one matches the reclaim pattern.
  # Pre-fix, readiness was a global conjunction over every tracked pid and any
  # disqualified pid could gate the whole tier; now a pid that cannot be a target
  # simply is not one.
  perl -e 'sleep 600' "ms-playwright-fixture" & target=$!
  sleep 600 & bystander=$!
  wait_spawned "$target" "$bystander"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$bystander $target"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$target")"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$bystander")"

  run mc_reap_sims

  kill "$target" "$bystander" 2>/dev/null

  assert_contains "$output" "would kill"
  assert_contains "$output" "$target"
  assert_not_contains "$output" "$bystander"
}

@test "HEADLINE: a pass that reclaims nothing names the pid blocking it and how long it has been idle" {
  # The return this replaces was the ONLY unlogged return in mc_reap_sims, and it
  # is the one that fired 1,973 times. Eleven days of silence, in a file whose
  # stated purpose is recording enforcement decisions.
  perl -e 'sleep 600' "ms-playwright-fixture" & sim=$!
  wait_spawned "$sim"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$sim"
  MC_DRY_RUN=1

  run mc_reap_sims

  kill "$sim" 2>/dev/null

  assert_not_contains "$output" "would kill"
  grep -q "tier3: reclaimed nothing" "$(mc_state_dir)/actions.log"
  grep -q "longest-idle blocker is pid $sim" "$(mc_state_dir)/actions.log"
}

# --- The invariant: evidence of work is never reclaimable garbage -------------
# The one-line kill -0 fix is correct and, alone, unsafe to ship. With tier 3
# unblocked it would have reclaimed the author's own maestro MCP servers:
# MC_SIM_KILL_PATTERN matches `\.maestro/lib`, and mc_active_mobile_tooling
# matches the same string as PROOF that a simulator is being driven. memcap would
# have been vetoing on the very pids it was about to kill.
#
# The rule is stated generally, not as an exclusion bolted onto one pattern: a
# process that any veto counts as evidence of active work is never a target, and
# it is enforced once, at mc_filter_protected, over whatever set the matchers
# currently return. Any future veto whose evidence overlaps a kill pattern
# inherits the protection automatically.
@test "INVARIANT: a process the mobile-tooling veto counts as evidence is never reclaimed" {
  # This fixture matches BOTH MC_SIM_KILL_PATTERN and the maestro arm of
  # MC_MOBILE_TOOLING_ARGV_PATTERN -- exactly the real maestro MCP server's shape.
  perl -e 'sleep 600' ".maestro/lib/fake-mcp-server.jar" & tooling=$!
  wait_spawned "$tooling"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$tooling"
  MC_DRY_RUN=1
  # The veto's ANSWER is forced to "not active" (setup_common's suite-wide
  # default) so this cannot pass merely because tier 3 declined. The veto says
  # "go ahead"; the invariant still refuses to touch the pid the veto is built on.
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$tooling")"

  run mc_reap_sims

  kill "$tooling" 2>/dev/null

  assert_not_contains "$output" "would kill"
  grep -q "counts it as active work" "$(mc_state_dir)/actions.log"
}

@test "INVARIANT: the choke point drops a tooling pid even when a tier hands it over directly" {
  # Stated at mc_kill_pids rather than inside one tier, so no tier can opt out of
  # it by construction. MC_VETO_EVIDENCE_PIDS is the deterministic form of the
  # same set (the matchers themselves depend on what is running on the host).
  sleep 600 & victim=$!
  wait_spawned "$victim"
  # shellcheck disable=SC2034  # consumed by mc_veto_evidence_pids
  MC_VETO_EVIDENCE_PIDS="$victim"
  MC_DRY_RUN=1
  run mc_kill_pids "$victim" "test"

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  [ -z "$output" ]
}

# --- Per-device simulator shutdown -------------------------------------------
# Two bugs live in this section's history, and the second is the reason it was
# rewritten.
#
# The first: all_ready was initialised to 1 at its `local` declaration while every
# grace and CPU check lived inside `if [ -n "${SIMPIDS// /}" ]`. An EMPTY SIMPIDS
# skipped the whole block with all_ready still 1, and every booted device on the
# machine was shut down.
#
# The second, found in production between 2026-08-27 and 08-29: `simctl shutdown
# all` ran 38 times in one-minute bursts against the device a live Maestro run was
# driving. `ios_ready` was a single machine-wide flag set by ANY ready pid
# matching launchd_sim|SimulatorTrampoline -- and SimulatorTrampoline is a
# CoreSimulator helper with no device affinity that stays alive, and CPU-flat, for
# days across device boots and shutdowns. So "a device is idle" was permanently
# true, and any device that appeared Booted was taken on the next pass. Maestro
# re-booted it; memcap shut it down again; the user ran `memcap off`.
#
# The fix is per-device: the ONLY evidence about a device is the launchd_sim whose
# argv names that device's own UDID.

# Writes both representations of `simctl list devices booted` -- the `-j` JSON the
# main path parses and the plain text the no-jq fallback parses -- from
# "<UDID>=<name>" pairs, so a test's booted set is the same fact whichever parse
# the machine running bats happens to take.
set_booted() {
  local pair udid name json=""
  : > "$FAKE_XCRUN_BOOTED"
  for pair in "$@"; do
    udid="${pair%%=*}"
    name="${pair#*=}"
    printf '    %s (%s) (Booted)\n' "$name" "$udid" >> "$FAKE_XCRUN_BOOTED"
    [ -n "$json" ] && json="$json,"
    json="$json{\"udid\":\"$udid\",\"name\":\"$name\",\"state\":\"Booted\"}"
  done
  printf '{"devices":{"com.apple.CoreSimulator.SimRuntime.iOS-26-0":[%s]}}\n' "$json" \
    > "$FAKE_XCRUN_BOOTED_JSON"
}

# A launchd_sim fixture for exactly one device. The real argv, measured:
#
#   launchd_sim /Users/u/Library/Developer/CoreSimulator/Devices/<UDID>/data/var/run/launchd_bootstrap.plist
#
# and that UDID is the only thing tying an idle pid to a device. A fixture without
# it is a fixture that cannot express the bug -- which is exactly why the previous
# version of these tests, whose launchd_sim carried no device path at all, passed
# against code that shut down every device on the machine.
spawn_launchd_sim() {
  perl -e 'sleep 600' "launchd_sim" \
    "/Users/tester/Library/Developer/CoreSimulator/Devices/$1/data/var/run/launchd_bootstrap.plist" &
  launchd_sim_pid=$!
  wait_spawned "$launchd_sim_pid"
}

MC_UDID_X="F096B0A2-20F5-4637-BC0E-19098780FA83"
MC_UDID_Y="0A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F9"

@test "XCRUN: an empty SIMPIDS does not shut down a single booted device" {
  set_booted "$MC_UDID_X=iPhone 17"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS=""
  # Deliberately NOT a dry run: the pre-fix behavior was safe in tests only by the
  # accident of MC_DRY_RUN=1, which is precisely the accident this asserts against.
  # Nothing real is reachable -- MC_XCRUN_BIN is setup's logging fake, and there
  # are no kill targets.
  MC_DRY_RUN=0

  run mc_reap_sims

  # Not merely "no shutdown": with nothing sim-classified at all there is nothing
  # memcap could act on and nothing it could explain, so it does not even ask the
  # device list. A booted device always has a launchd_sim, so an empty SIMPIDS
  # means either nothing is booted or the snapshot predates the boot.
  [ ! -s "$FAKE_XCRUN_LOG" ]
}

@test "XCRUN: SimulatorTrampoline is never evidence that a booted device is idle" {
  # THE NEGATIVE CONTROL for the production bug. SimulatorTrampoline was in
  # MC_SIM_IOS_EXE, so this exact arrangement -- one long-idle trampoline, one
  # booted device it has nothing to do with -- was what ran `simctl shutdown all`
  # 38 times against a live Maestro run.
  set_booted "$MC_UDID_X=iPhone 17"
  perl -e 'sleep 600' "SimulatorTrampoline" & sim=$!
  wait_spawned "$sim"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$sim"
  MC_DRY_RUN=0
  mkdir -p "$(mc_sims_idle_dir)"
  # Idle since 1970 and CPU-flat: past every grace this tier has.
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$sim")"

  run mc_reap_sims

  kill "$sim" 2>/dev/null

  # No shutdown of any kind -- not `all`, not this device, not any device. The
  # whole call log is the haystack so a failure prints the call that was made.
  assert_not_contains "$(cat "$FAKE_XCRUN_LOG")" "shutdown"
  # And the device is reported as untouchable rather than silently skipped: a
  # booted device with no launchd_sim in the snapshot is the fail-closed case.
  assert_contains "$(cat "$(mc_state_dir)/actions.log")" "device $MC_UDID_X (iPhone 17) is booted but no launchd_sim"
}

@test "XCRUN: only the device whose own launchd_sim is idle is shut down" {
  # Two booted devices. X's launchd_sim has cleared its grace; Y's was stamped
  # this second, which is what a device Maestro just booted looks like. Under the
  # old machine-wide flag either pid licensed `shutdown all` and took both.
  set_booted "$MC_UDID_X=iPhone 17" "$MC_UDID_Y=iPad Pro 13-inch"
  spawn_launchd_sim "$MC_UDID_X"; sim_x="$launchd_sim_pid"
  spawn_launchd_sim "$MC_UDID_Y"; sim_y="$launchd_sim_pid"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$sim_x $sim_y"
  MC_DRY_RUN=0
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$sim_x")"
  printf '%s 999999\n' "$(date +%s)" > "$(mc_sims_idle_stamp "$sim_y")"

  run mc_reap_sims

  kill "$sim_x" "$sim_y" 2>/dev/null

  calls="$(cat "$FAKE_XCRUN_LOG")"
  assert_contains "$calls" "simctl shutdown $MC_UDID_X"
  assert_not_contains "$calls" "simctl shutdown $MC_UDID_Y"
  assert_not_contains "$calls" "shutdown all"
  # The audit line names the device, the pid that spoke for it, and how long that
  # pid had been flat -- "shutdown all" could say none of those.
  assert_matches "$(cat "$(mc_state_dir)/actions.log")" \
    "tier3: xcrun simctl shutdown $MC_UDID_X \\(iPhone 17\\) -- launchd_sim pid $sim_x CPU-flat for [0-9]+s"
}

@test "XCRUN: a booted device whose launchd_sim is still inside its grace is held, not shut down" {
  # The other half of failing closed: the device IS mapped, so it is not the
  # unmapped case, but its pid has not earned anything yet. The existing
  # tier3-holding line is what reports it, and the launchd_sim has to be counted
  # among the blockers for that line to exist at all.
  set_booted "$MC_UDID_X=iPhone 17"
  spawn_launchd_sim "$MC_UDID_X"; sim="$launchd_sim_pid"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$sim"
  MC_DRY_RUN=0
  mkdir -p "$(mc_sims_idle_dir)"
  printf '%s 999999\n' "$(date +%s)" > "$(mc_sims_idle_stamp "$sim")"

  run mc_reap_sims

  kill "$sim" 2>/dev/null

  assert_not_contains "$(cat "$FAKE_XCRUN_LOG")" "shutdown"
  log="$(cat "$(mc_state_dir)/actions.log")"
  assert_contains "$log" "still inside their idle grace"
  assert_contains "$log" "blocker is pid $sim"
  # Mapped, so the unmapped line would be a lie.
  assert_not_contains "$log" "no launchd_sim"
}

@test "XCRUN: a booted device with no launchd_sim in the snapshot is reported once, not shut down" {
  # A sim-classified process exists (so memcap does look at the device list) but
  # nothing in the snapshot belongs to this device. Fail closed and say so.
  set_booted "$MC_UDID_X=iPhone 17"
  perl -e 'sleep 600' "ms-playwright-fixture" & other=$!
  wait_spawned "$other"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$other"
  MC_DRY_RUN=0
  mkdir -p "$(mc_sims_idle_dir)"
  printf '%s 999999\n' "$(date +%s)" > "$(mc_sims_idle_stamp "$other")"

  run mc_reap_sims
  run mc_reap_sims

  kill "$other" 2>/dev/null

  assert_not_contains "$(cat "$FAKE_XCRUN_LOG")" "shutdown"
  # Throttled per UDID: two passes, one line. Unthrottled, a permanently booted
  # device nobody is measuring would write this every 60 seconds forever, which is
  # how 94% of a day's actions.log became two repeated lines once before.
  run grep -c "no launchd_sim for it" "$(mc_state_dir)/actions.log"
  [ "$output" = "1" ]
}

@test "XCRUN: a shutdown that fails is logged as a failure, with its rc and reason" {
  # `simctl shutdown` on a device that is already down exits 149 with
  # "Unable to shutdown device in current state: Shutdown". The old line was
  # written BEFORE the command ran and said only "shutdown all", so actions.log
  # could not distinguish a device memcap took down from one it never touched --
  # the difference between "memcap did this to me" and "something else did".
  set_booted "$MC_UDID_X=iPhone 17"
  spawn_launchd_sim "$MC_UDID_X"; sim="$launchd_sim_pid"
  printf '149\n' > "$FAKE_XCRUN_SHUTDOWN_RC"
  printf 'Unable to shutdown device in current state: Shutdown\n' > "$FAKE_XCRUN_SHUTDOWN_ERR"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$sim"
  MC_DRY_RUN=0
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$sim")"

  run mc_reap_sims

  kill "$sim" 2>/dev/null

  log="$(cat "$(mc_state_dir)/actions.log")"
  assert_contains "$log" "tier3: simctl shutdown $MC_UDID_X (iPhone 17) failed (rc 149): Unable to shutdown device in current state: Shutdown"
  assert_not_contains "$log" "CPU-flat for"
}

@test "XCRUN: a dry run names the device it would shut down, and shuts nothing down" {
  set_booted "$MC_UDID_X=iPhone 17"
  spawn_launchd_sim "$MC_UDID_X"; sim="$launchd_sim_pid"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$sim"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$sim")"

  run mc_reap_sims

  kill "$sim" 2>/dev/null

  assert_contains "$output" "would shut down device $MC_UDID_X (iPhone 17)"
  assert_not_contains "$(cat "$FAKE_XCRUN_LOG")" "shutdown"
}

@test "XCRUN: simdiskimaged alone is not evidence that any device is booted" {
  # simdiskimaged is a root-owned daemon that runs whether or not a device is
  # booted, which is why it is excluded from MC_SIM_IOS_EXE. Treating it as
  # evidence is what made a shutdown look justified on a machine with nothing
  # booted at all. It is sim-classified, so the device list is still consulted --
  # what must not happen is a shutdown.
  set_booted "$MC_UDID_X=iPhone 17"
  perl -e 'sleep 600' "/Library/Developer/CoreSimulator/simdiskimaged" & daemon=$!
  wait_spawned "$daemon"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$daemon"
  MC_DRY_RUN=0
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$daemon")"

  run mc_reap_sims

  kill "$daemon" 2>/dev/null

  assert_not_contains "$(cat "$FAKE_XCRUN_LOG")" "shutdown"
}

@test "XCRUN: the booted-device list parses without jq too" {
  # jq is a formula dependency, so the plain-text parse is a fallback -- but a
  # tier that cannot see a booted device on a machine missing jq is a tier that
  # silently stops working, and a fallback that misreads a UDID would shut down
  # the wrong device. Same PATH-stripping method as docker.bats's no-jq test.
  set_booted "$MC_UDID_X=iPhone 17" "$MC_UDID_Y=iPad Pro 13-inch"
  fakebin="$BATS_TEST_TMPDIR/nojq"
  mkdir -p "$fakebin"
  for c in sed cat head grep ps date; do ln -sf "$(command -v $c)" "$fakebin/$c"; done
  run env PATH="$fakebin" MC_XCRUN_BIN="$MC_XCRUN_BIN" /bin/bash -c \
    "source '$MEMCAP_ROOT/libexec/common.sh'; source '$MEMCAP_ROOT/libexec/enforce.sh'; mc_booted_devices"

  assert_contains "$output" "$MC_UDID_X iPhone 17"
  assert_contains "$output" "$MC_UDID_Y iPad Pro 13-inch"
  # Both parses have to agree, or the suite's meaning depends on the host.
  run mc_booted_devices
  assert_contains "$output" "$MC_UDID_X iPhone 17"
  assert_contains "$output" "$MC_UDID_Y iPad Pro 13-inch"
}

@test "XCRUN: nothing booted means no devices, on either parse" {
  set_booted
  run mc_booted_devices
  [ -z "$output" ]
}


# --- Corrupt idle stamps ------------------------------------------------------
# Both of these were reproduced, and both are worse than they look.
@test "STAMP: a truncated stamp restarts the clock instead of bypassing the grace entirely" {
  # `12345` -- second field lost. The CPU delta was computed against an empty
  # baseline so no reset ever fired, and `now - 12345` is decades: the grace was
  # bypassed ENTIRELY, the pid was killed on the first pass that saw it, and it
  # was reported as a successful, correctly-graced reclaim.
  perl -e 'sleep 600' "ms-playwright-fixture" & sim=$!
  wait_spawned "$sim"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$sim"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '12345\n' > "$(mc_sims_idle_stamp "$sim")"

  run mc_reap_sims

  assert_not_contains "$output" "would kill"
  # Rewritten as a well-formed two-field stamp with a fresh clock, so the pid gets
  # its full grace from here rather than from a number nobody could parse.
  read -r first cpu < "$(mc_sims_idle_stamp "$sim")"
  kill "$sim" 2>/dev/null
  [ "$first" != "12345" ]
  [ -n "$cpu" ]
}

@test "STAMP: a non-numeric stamp does not take down this pass, or every pass after it" {
  # `notanumber 0` reached `$(( ))` under `set -u` and killed the whole pass. The
  # stamp is only pruned once its pid dies, so EVERY subsequent pass died at the
  # same line for as long as that simulator lived -- a single bad write was a
  # permanent outage, and the heartbeat would still have been stamped by the
  # caller that never got to run.
  perl -e 'sleep 600' "ms-playwright-fixture" & sim=$!
  wait_spawned "$sim"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$sim"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf 'notanumber 0\n' > "$(mc_sims_idle_stamp "$sim")"

  run mc_reap_sims
  [ "$status" -eq 0 ]

  # And again, with the stamp deliberately re-corrupted, to prove the recovery is
  # not a one-off: the real failure was that it repeated forever.
  printf 'notanumber 0\n' > "$(mc_sims_idle_stamp "$sim")"
  run mc_reap_sims

  kill "$sim" 2>/dev/null
  [ "$status" -eq 0 ]
}

# =============================================================================
# The kill path: pid reuse between the snapshot and the SIGKILL.
#
# `kill -TERM $pids; sleep 2; kill -0 recheck; kill -KILL`. The recheck answers
# "is A process alive at this number", not "is it the one I TERMed" -- and the
# survivors went to SIGKILL without passing back through mc_filter_protected.
# Measured on this machine: 135 pids allocated per 2-second idle window against a
# pid space of ~99999, so the 388-orphan sweep this tool was built for carries
# roughly half an expected SIGKILL delivered to an innocent bystander. It is the
# widest snapshot-to-action window in the codebase and was revalidated by nothing.
# =============================================================================

@test "REUSE: a pid's identity is its start time plus its command line, and it distinguishes two processes" {
  perl -e 'sleep 600' "mc-ident-a" & a=$!
  perl -e 'sleep 600' "mc-ident-b" & b=$!
  wait_spawned "$a" "$b"
  ident_a=$(mc_pid_identity "$a")
  ident_b=$(mc_pid_identity "$b")
  kill "$a" "$b" 2>/dev/null
  [ -n "$ident_a" ]
  assert_contains "$ident_a" "mc-ident-a"
  assert_contains "$ident_a" "$(date +%Y)"
  [ "$ident_a" != "$ident_b" ] || {
    echo "two distinct fixtures produced identical identities:" >&2
    echo "  $ident_a" >&2
    return 1
  }
}

@test "REUSE: what the identity check does NOT close, stated honestly" {
  # `lstart` has one-second resolution, so two processes started in the same
  # second with the same argv are genuinely indistinguishable by this test. That
  # is the residual window, and it is a far narrower one than the pre-fix
  # behavior: the pid number alone matched ANY process that happened to inherit
  # the number, whereas this requires an unrelated process to have taken the
  # number AND to be running the same command line AND to have started in the
  # same second the original did. Pinned as a test so the limitation is a
  # recorded decision rather than something a later reader has to rediscover.
  sleep 600 & a=$!
  sleep 600 & b=$!
  wait_spawned "$a" "$b"
  ident_a=$(mc_pid_identity "$a")
  ident_b=$(mc_pid_identity "$b")
  kill "$a" "$b" 2>/dev/null
  [ "$ident_a" = "$ident_b" ]
}

@test "REUSE: an identity recorded before SIGTERM is looked up again by pid" {
  recorded="4242|Mon Jan  1 00:00:00 2024 /usr/bin/node server.js
4243|Mon Jan  1 00:00:01 2024 /usr/bin/node other.js"
  run mc_identity_lookup 4243 "$recorded"
  [ "$status" -eq 0 ]
  assert_contains "$output" "other.js"
  run mc_identity_lookup 9999 "$recorded"
  [ "$status" -ne 0 ]
}

@test "REUSE: a real TERM-then-KILL still kills the process it was aimed at" {
  # The identity check must close the reuse window without breaking the ordinary
  # path. A perl fixture that ignores SIGTERM forces the escalation to SIGKILL,
  # which is the branch the re-filter and the identity comparison live on.
  perl -e '$SIG{TERM}="IGNORE"; sleep 600' "mc-stubborn-fixture" & victim=$!
  wait_spawned "$victim"
  MC_DRY_RUN=0
  run mc_kill_pids "$victim" "test escalation"
  [ "$status" -eq 0 ]

  # SIGKILL is synchronous enough that a short poll is decisive, and polling
  # rather than sleeping a constant keeps this from racing under suite load.
  gone=0
  for _ in $(seq 1 250); do
    mc_pid_alive "$victim" || { gone=1; break; }
    sleep 0.02
  done
  kill -9 "$victim" 2>/dev/null || true
  [ "$gone" -eq 1 ]
}

# --- The protection filter itself must not fail open -------------------------
@test "PROTECT: an unset PROTECTEDPIDS refuses the kill instead of protecting nothing" {
  sleep 600 & victim=$!
  wait_spawned "$victim"
  unset PROTECTEDPIDS
  MC_DRY_RUN=1
  run mc_kill_pids "$victim" "test"

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  [ "$status" -ne 0 ]
  [ -z "$output" ]
  grep -q "the protected pid set is unknown" "$(mc_state_dir)/actions.log"
}

@test "PROTECT: a pid in PROTECTEDPIDS but not AGENTPIDS is still spared" {
  # Contract C3: PROTECTEDPIDS is the propagated agent TREE. This is the shape of
  # the six real tier-2 kills that were chrome-devtools-mcp watchdogs running as
  # grandchildren of a live claude session -- in the tree, absent from AGENTPIDS.
  sleep 600 & victim=$!
  wait_spawned "$victim"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_filter_protected
  PROTECTEDPIDS="$victim"
  MC_DRY_RUN=1
  run mc_kill_pids "$victim" "test"

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  [ -z "$output" ]
}

@test "PROTECT: a ps that cannot resolve memcap's own ancestry refuses the kill" {
  # The old walk broke out of the loop on an empty ps answer and returned just
  # `$$`, silently dropping memcap's parents out of the protected set -- at
  # exactly the moment the machine is under enough pressure for a tier to fire.
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  cat > "$fakebin/ps" <<'SCRIPT'
#!/usr/bin/env bash
exit 1
SCRIPT
  chmod +x "$fakebin/ps"

  sleep 600 & victim=$!
  wait_spawned "$victim"
  MC_DRY_RUN=1
  PATH="$fakebin:$PATH" run mc_self_ancestry
  [ "$status" -ne 0 ]

  PATH="$fakebin:$PATH" run mc_kill_pids "$victim" "test"

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  [ -z "$output" ]
}

# --- Kill records were truncated mid-token -----------------------------------
@test "RECORD: a long command line keeps its informative tail instead of being cut at 160" {
  # 376 real kill records collapsed to 4 distinct strings under `cut -c1-160`:
  # the node binary path plus the `--require` shim consumed the whole budget, so
  # the argument naming the script actually being killed was never recorded once.
  filler=$(printf 'A%.0s' $(seq 1 500))
  perl -e 'sleep 600' "$filler" "mc-tail-marker-9f2" & victim=$!
  wait_spawned "$victim"
  MC_DRY_RUN=0
  run mc_kill_pids "$victim" "tail test"

  kill -9 "$victim" 2>/dev/null || true

  log="$(mc_state_dir)/actions.log"
  grep -q "mc-tail-marker-9f2" "$log"
  grep -q '\.\.\.' "$log"
}

@test "RECORD: mc_abbrev leaves a short record exactly as it found it" {
  run mc_abbrev "1234  5000  /usr/bin/node server.js"
  [ "$output" = "1234  5000  /usr/bin/node server.js" ]
}

# =============================================================================
# Tier 1: it had no age gate at all, while its own comment claimed one.
#
# MC_DEV_PATTERN matches /esbuild, /webpack, /rollup and /tsx, so `npm run build
# &` reparented to init is an instant kill target. One real production kill was
# `npm exec next start -p 3100` -- a PRODUCTION server on an ad-hoc port. ppid==1
# cannot distinguish "abandoned by a dead session" from "deliberately daemonized
# with nohup", and nothing else in the process table can either; the age gate
# does not fix that case, but the asymmetry decides the one it does fix. A leak
# is persistent (388 orphans accumulate over hours), so waiting five minutes to
# reap one costs nothing; killing a three-second-old build destroys work.
# =============================================================================

@test "TIER1-AGE: a freshly-spawned orphan under a valid root is spared" {
  mkdir -p "$HOME/.mc-age-$$/proj"
  real_root=$(mc_canonicalize "$HOME/.mc-age-$$/proj")
  mc_record_root "$HOME/.mc-age-$$/proj"

  perl -e 'sleep 600' "$real_root/node_modules/.bin/esbuild" &
  victim=$!
  wait_spawned "$victim"

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  MC_DRY_RUN=1
  # No TIER1_MIN_AGE_SEC override: the DEFAULT must already spare this.
  run mc_reap_orphans

  kill "$victim" 2>/dev/null
  rm -rf "$HOME/.mc-age-$$"

  assert_not_contains "$output" "would kill"
}

@test "TIER1-AGE: the gate reads through mc_num, so a non-numeric value does not disable it" {
  # `[ 0 -lt "5m" ]` returns status 2 and the gate sits left of an `&&`, so
  # pre-C1 a hand-typed "5m" did not lengthen the minimum age -- it removed it.
  mkdir -p "$HOME/.mc-agebad-$$/proj"
  real_root=$(mc_canonicalize "$HOME/.mc-agebad-$$/proj")
  mc_record_root "$HOME/.mc-agebad-$$/proj"

  perl -e 'sleep 600' "$real_root/node_modules/.bin/esbuild" &
  victim=$!
  wait_spawned "$victim"

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans's age gate
  TIER1_MIN_AGE_SEC="5m"
  MC_DRY_RUN=1
  run mc_reap_orphans

  kill "$victim" 2>/dev/null
  rm -rf "$HOME/.mc-agebad-$$"

  assert_not_contains "$output" "would kill"
  grep -q "TIER1_MIN_AGE_SEC is not a whole number" "$(mc_state_dir)/actions.log"
}

@test "TIER1-PERF: sweep roots are validated once per pass, not once per (orphan x root)" {
  # mc_canonicalize was called from inside the per-root loop, which itself sits
  # inside the per-orphan loop. Measured at 5,121us per (orphan, root) pair, this
  # machine's 40 recorded roots against 388 orphans is 79 seconds of work against
  # a 60-second service interval -- memcap at its slowest in exactly the leak it
  # exists to clean up. Validation does not depend on which orphan is being
  # tested, so it happens once. This test counts the calls and fails if it is
  # ever moved back inside.
  local i
  for i in 1 2 3 4 5; do
    mkdir -p "$HOME/.mc-rootcost-$$/r$i"
    mc_record_root "$HOME/.mc-rootcost-$$/r$i"
  done

  mkdir -p "$HOME/.mc-rootcost-other-$$/proj"
  perl -e 'sleep 600' "$HOME/.mc-rootcost-other-$$/proj/a.js" & v1=$!
  perl -e 'sleep 600' "$HOME/.mc-rootcost-other-$$/proj/b.js" & v2=$!
  wait_spawned "$v1" "$v2"

  # Counting stub, defined AFTER enforce.sh was sourced in setup() so sourcing
  # cannot clobber it. Identity-canonicalizing is correct for this fixture:
  # mc_record_root already stored the resolved form, so `real == root` holds and
  # the validation path behaves exactly as it does for real.
  MC_CANON_CALLS="$BATS_TEST_TMPDIR/canon-calls"
  : > "$MC_CANON_CALLS"
  mc_canonicalize() { echo x >> "$MC_CANON_CALLS"; printf '%s\n' "$1"; }
  # Kept out of it entirely so the count is purely about root validation.
  mc_pid_cwd() { printf '%s' ""; }

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$v1 $v2"
  # shellcheck disable=SC2034  # consumed by mc_reap_orphans's age gate
  TIER1_MIN_AGE_SEC=0
  MC_DRY_RUN=1
  run mc_reap_orphans

  calls=$(wc -l < "$MC_CANON_CALLS" | tr -d ' ')

  kill "$v1" "$v2" 2>/dev/null
  rm -rf "$HOME/.mc-rootcost-$$" "$HOME/.mc-rootcost-other-$$"

  # Five roots, two orphans. Hoisted: 5. Inside the loop: 10.
  [ "$calls" -eq 5 ] || {
    echo "mc_canonicalize called $calls times for 5 roots across 2 orphans" >&2
    echo "roots must be validated once per pass, not once per (orphan x root)" >&2
    return 1
  }
}

# =============================================================================
# Tier 2 killed live work, reclaimed nothing, and misreported it.
#
# Ten real kills on this machine, 126 MB reclaimed in total, against overages of
# 0.5-6 GB. Six were agent grandchildren; four were the Metro transformer feeding
# a simulator the developer was actively driving -- the production log has tier 3
# declining at 13:03:47 "active mobile tooling", and tier 2 killing that
# simulator's bundler at 13:03:48. And every one of them was announced as
# "killed a leaked dev server".
# =============================================================================

@test "TIER2-VETO: active mobile tooling declines tier 2, because the dev server IS the bundler" {
  sleep 600 & victim=$!
  wait_spawned "$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # shellcheck disable=SC2034  # consumed by mc_active_mobile_tooling
  MC_ACTIVE_MOBILE_TOOLING=1
  MC_DRY_RUN=1
  run mc_kill_over_budget

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  assert_not_contains "$output" "would kill"
  grep -q "tier2: declining -- active mobile tooling" "$(mc_state_dir)/actions.log"
}

@test "TIER2-VETO: hands-on mobile work declines tier 2 as well" {
  sleep 600 & victim=$!
  wait_spawned "$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # shellcheck disable=SC2034  # consumed by mc_hands_on_mobile
  MC_HANDS_ON_MOBILE=1
  MC_DRY_RUN=1
  run mc_kill_over_budget

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  assert_not_contains "$output" "would kill"
  grep -q "tier2: declining -- hands-on mobile work" "$(mc_state_dir)/actions.log"
}

@test "TIER2-OFF: TIER2_ENABLED=0 switches the tier off outright" {
  sleep 600 & victim=$!
  wait_spawned "$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget
  TIER2_ENABLED=0
  MC_DRY_RUN=1
  run mc_kill_over_budget

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  assert_not_contains "$output" "would kill"
  grep -q "TIER2_ENABLED=0" "$(mc_state_dir)/actions.log"
}

@test "TIER2-PROTECT: a dev server's agent grandchild is spared via PROTECTEDPIDS" {
  # The exact shape of six of the ten real kills: a telemetry watchdog running as
  # a GRANDCHILD of a live agent session. It never appeared in AGENTPIDS, which
  # only ever held direct CLI matches, so the old filter did not see it.
  bash -c 'sleep 600 & wait' >/dev/null 2>&1 & server=$!
  agent_child=""
  for _ in $(seq 1 250); do
    agent_child=$(pgrep -P "$server" | head -1)
    [ -n "$agent_child" ] && break
    sleep 0.02
  done
  [ -n "$agent_child" ]

  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$server"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # Deliberately empty: this must be caught by PROTECTEDPIDS alone.
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_filter_protected
  PROTECTEDPIDS="$agent_child"
  MC_DRY_RUN=1
  run mc_kill_over_budget

  kill "$agent_child" "$server" 2>/dev/null

  assert_contains "$output" "would kill"
  assert_contains "$output" "$server"
  assert_not_contains "$output" "$agent_child"
}

@test "TIER2-RANK: candidates are ranked by subtree total, not by their own footprint" {
  # Ranking by the victim's own footprint while killing its subtree ranks by a
  # number that is not what the kill reclaims: a fat worker outranks the server
  # that owns the worker pool, memcap kills the worker, the supervisor respawns
  # it, and the next pass kills a different pid for the same reason. Two such
  # kills 70 seconds apart, on consecutive passes, are in the production log.
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  cat > "$fakebin/ps" <<'SCRIPT'
#!/usr/bin/env bash
pid="" mode=""
for a in "$@"; do
  case "$a" in
    etime=) mode=etime ;;
    ppid=)  mode=ppid ;;
    comm=)  mode=comm ;;
    [0-9]*) pid="$a" ;;
  esac
done
case "$mode" in
  ppid) echo " 1"; exit 0 ;;
  comm) echo " node"; exit 0 ;;
esac
case "$pid:$mode" in
  7010:etime) echo "  10:00" ;;
  7020:etime) echo "  10:00" ;;
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
  7010) echo "7010  100M" ;;    # the supervisor: small on its own
  7011) echo "7011  8000M" ;;   # its worker: where the memory actually is
  7020) echo "7020  5000M" ;;   # a fat leaf with no children
esac
exit 0
SCRIPT
  chmod +x "$fakebin/top"
  cat > "$fakebin/pgrep" <<'SCRIPT'
#!/usr/bin/env bash
[ "$1" = "-P" ] && [ "$2" = "7010" ] && { echo 7011; exit 0; }
exit 1
SCRIPT
  chmod +x "$fakebin/pgrep"

  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="7010 7020"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  MC_DRY_RUN=1
  PATH="$fakebin:$PATH" run mc_kill_over_budget

  # Own footprint: 7020 (5000M) beats 7010 (100M). Subtree: 7010 (8100M) beats
  # 7020 (5000M). The subtree is what the kill actually reclaims.
  assert_contains "$output" "would kill"
  assert_contains "$output" "7010"
  assert_contains "$output" "7011"
  assert_not_contains "$output" "7020"
}

@test "TIER2-C1: TIER2_MIN_AGE_SEC=\"5m\" lengthens the gate instead of deleting it" {
  # `[ 0 -lt "5m" ]` returns status 2, and the gate sits left of an `&&`, so
  # pre-C1 this did not mean "five minutes" and it did not mean "reject the
  # value" -- it meant no age gate at all, and a one-second-old process died.
  sleep 600 & victim=$!
  wait_spawned "$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC="5m"
  MC_DRY_RUN=1
  run mc_kill_over_budget

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  assert_not_contains "$output" "would kill"
  grep -q "TIER2_MIN_AGE_SEC is not a whole number" "$(mc_state_dir)/actions.log"
}

# --- Tier 2 must not reclaim what tier 3 is still judging ---------------------
# 2026-08-26 21:10:30, production: a Playwright driver plus a headed Chrome and
# its six helpers, killed in one tier-2 event. The Chrome carried
# `--user-data-dir=...playwright_chromiumdev_profile-...`, so it was
# sim-classified -- tier 3's population, which tier 3 only reclaims after
# measuring CPU flatness across the whole grace. Tier 2 applied none of that: it
# ranked the subtree first BECAUSE a browser is the biggest thing on the machine.

# A parent with a child, so mc_descendants has a real subtree to walk. Returns
# "parent child" and leaves both running for the caller to kill.
spawn_pair() {
  local parent child i
  # stdout and stderr detached: this function is called through a command
  # substitution, which waits for the write end of the pipe to close -- a
  # background process inheriting that fd holds it open for its whole 600s life
  # and hangs the test that spawned it.
  # `& wait`, not two sleeps: the wrapper then has exactly ONE child, so killing
  # the pair at the end of a test leaves nothing behind. The first version left a
  # second sleep orphaned in every test, and five of them still holding bats'
  # descriptors is a suite that finishes its last test and then hangs.
  bash -c 'sleep 600 & wait' >/dev/null 2>&1 &
  parent=$!
  i=0
  while [ "$i" -lt 250 ]; do
    child=$(pgrep -P "$parent" 2>/dev/null | head -1)
    [ -n "$child" ] && break
    sleep 0.02
    i=$((i + 1))
  done
  printf '%s %s' "$parent" "$child"
}

@test "TIER2-SIM: a candidate whose subtree holds a sim inside its idle grace is skipped" {
  read -r parent child <<<"$(spawn_pair)"
  [ -n "$child" ]
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$parent"
  # shellcheck disable=SC2034  # consumed by mc_tier2_sim_blocker
  SIMPIDS=" $child "
  # shellcheck disable=SC2034  # read by mc_kill_over_budget, sourced from enforce.sh
  TIER2_MIN_AGE_SEC=0
  # shellcheck disable=SC2034  # read by mc_tier2_sim_blocker
  SIM_IDLE_GRACE_SEC=600
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  # Tracked 60 seconds ago: nowhere near the 600s grace, so tier 3 would not
  # touch it and tier 2 must not either.
  printf '%s %s\n' "$(( $(date +%s) - 60 ))" 0 > "$(mc_sims_idle_stamp "$child")"

  run mc_kill_over_budget
  kill "$parent" "$child" 2>/dev/null

  assert_not_contains "$output" "would kill"
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "its subtree holds sim pid $child"
  assert_contains "$output" "tier 3's to judge"
}

@test "TIER2-SIM: once the sim has cleared tier 3's grace, tier 2 may take it" {
  read -r parent child <<<"$(spawn_pair)"
  [ -n "$child" ]
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget
  DEVPIDS="$parent"
  # shellcheck disable=SC2034  # consumed by mc_tier2_sim_blocker
  SIMPIDS=" $child "
  # shellcheck disable=SC2034  # read by mc_kill_over_budget, sourced from enforce.sh
  TIER2_MIN_AGE_SEC=0
  # shellcheck disable=SC2034  # read by mc_tier2_sim_blocker
  SIM_IDLE_GRACE_SEC=600
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '%s %s\n' "$(( $(date +%s) - 5000 ))" 0 > "$(mc_sims_idle_stamp "$child")"

  run mc_kill_over_budget
  kill "$parent" "$child" 2>/dev/null

  assert_contains "$output" "would kill"
}

@test "TIER2-SIM: an unstamped sim blocks -- unseen is not proven idle" {
  # Tier 3 stamps every sim pid on every pass, so a sim with no stamp is one
  # memcap has not observed yet, not one it has watched sit still. The wrong
  # guess here kills a browser mid-test.
  read -r parent child <<<"$(spawn_pair)"
  [ -n "$child" ]
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget
  DEVPIDS="$parent"
  # shellcheck disable=SC2034  # consumed by mc_tier2_sim_blocker
  SIMPIDS=" $child "
  # shellcheck disable=SC2034  # read by mc_kill_over_budget, sourced from enforce.sh
  TIER2_MIN_AGE_SEC=0
  MC_DRY_RUN=1
  rm -f "$(mc_sims_idle_stamp "$child")"

  run mc_kill_over_budget
  kill "$parent" "$child" 2>/dev/null

  assert_not_contains "$output" "would kill"
}

@test "TIER2-SIM: a blocked candidate does not hide a legitimate one below it" {
  # The guard walks DOWN the ranking. Stopping at the first blocked candidate
  # would turn one protected browser into tier 2 never acting at all.
  read -r parent child <<<"$(spawn_pair)"
  [ -n "$child" ]
  sleep 600 & plain=$!
  wait_spawned "$plain"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget
  DEVPIDS="$parent $plain"
  # shellcheck disable=SC2034  # consumed by mc_tier2_sim_blocker
  SIMPIDS=" $child "
  # shellcheck disable=SC2034  # read by mc_kill_over_budget, sourced from enforce.sh
  TIER2_MIN_AGE_SEC=0
  # shellcheck disable=SC2034  # read by mc_tier2_sim_blocker
  SIM_IDLE_GRACE_SEC=600
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '%s %s\n' "$(date +%s)" 0 > "$(mc_sims_idle_stamp "$child")"

  run mc_kill_over_budget
  kill "$parent" "$child" "$plain" 2>/dev/null

  # The pair ranks higher (two processes of subtree), so the guard must have
  # skipped past it to reach the lone sleeper.
  assert_contains "$output" "would kill"
  assert_not_contains "$output" "would kill $parent"
}

@test "TIER2-SIM: when every candidate is blocked, the pass says so" {
  read -r parent child <<<"$(spawn_pair)"
  [ -n "$child" ]
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget
  DEVPIDS="$parent"
  # shellcheck disable=SC2034  # consumed by mc_tier2_sim_blocker
  SIMPIDS=" $child "
  # shellcheck disable=SC2034  # read by mc_kill_over_budget, sourced from enforce.sh
  TIER2_MIN_AGE_SEC=0
  # shellcheck disable=SC2034  # read by mc_tier2_sim_blocker
  SIM_IDLE_GRACE_SEC=600
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '%s %s\n' "$(date +%s)" 0 > "$(mc_sims_idle_stamp "$child")"

  mc_kill_over_budget >/dev/null
  kill "$parent" "$child" 2>/dev/null

  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "every candidate's subtree holds a simulator or browser"
}

# 79 identical copies of this line in 42 hours of production, the most frequent
# line in actions.log -- a machine that is chronically over budget with only
# young processes reaches this branch on every pass. v0.4.0 throttled every other
# repeating decline for exactly this reason and missed this one.
@test "TIER2-THROTTLE: the 'no candidate' decline logs once per window, not once per pass" {
  sleep 600 & victim=$!
  wait_spawned "$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  # shellcheck disable=SC2034  # nothing is old enough, so every pass declines
  TIER2_MIN_AGE_SEC=99999
  MC_DRY_RUN=1
  mc_kill_over_budget
  mc_kill_over_budget
  mc_kill_over_budget

  kill "$victim" 2>/dev/null

  run grep -c "no candidate is both older" "$(mc_state_dir)/actions.log"
  [ "$output" = "1" ]
}

@test "TIER2-THROTTLE: a state change gets its own line rather than waiting out the window" {
  # The throttle key is cleared the moment tier 2 finds a candidate again, so a
  # machine that goes quiet, gets busy, and goes quiet again logs both quiet
  # spells instead of one.
  sleep 600 & victim=$!
  wait_spawned "$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget
  DEVPIDS="$victim"
  MC_DRY_RUN=1

  TIER2_MIN_AGE_SEC=99999
  mc_kill_over_budget          # declines, logs
  TIER2_MIN_AGE_SEC=0
  mc_kill_over_budget          # finds a candidate, clears the key
  TIER2_MIN_AGE_SEC=99999
  mc_kill_over_budget          # declines again -- must log again

  kill "$victim" 2>/dev/null

  run grep -c "no candidate is both older" "$(mc_state_dir)/actions.log"
  [ "$output" = "2" ]
}

@test "TIER2-NOTIFY: the 'only fresh builds' notification is dry-run gated too" {
  # enforce.sh's other notification was gated on mc_kill_pids actually killing
  # something; this one was gated on nothing at all, so a dry run fired a real
  # desktop notification and burned the 5-minute rate limit doing it.
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  capture="$BATS_TEST_TMPDIR/osascript-arg"
  cat > "$fakebin/osascript" <<SCRIPT
#!/usr/bin/env bash
printf '%s' "\$2" >> "$capture"
SCRIPT
  chmod +x "$fakebin/osascript"

  sleep 600 & victim=$!
  wait_spawned "$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  MC_DRY_RUN=1
  PATH="$fakebin:$PATH" run mc_kill_over_budget

  kill "$victim" 2>/dev/null

  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "not touching active work"
  [ ! -f "$capture" ]
}

@test "TIER2-NOTIFY: a real kill names what was killed rather than calling it a leaked dev server" {
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  capture="$BATS_TEST_TMPDIR/osascript-arg"
  cat > "$fakebin/osascript" <<SCRIPT
#!/usr/bin/env bash
printf '%s' "\$2" >> "$capture"
SCRIPT
  chmod +x "$fakebin/osascript"

  perl -e 'sleep 600' "mc-notify-fixture" & victim=$!
  wait_spawned "$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # A real kill of a fixture this test spawned itself -- the notification wording
  # only exists on the real path.
  MC_DRY_RUN=0
  PATH="$fakebin:$PATH" run mc_kill_over_budget

  kill -9 "$victim" 2>/dev/null || true

  run cat "$capture"
  assert_contains "$output" "$victim"
  assert_not_contains "$output" "leaked dev server"
}

# =============================================================================
# Contract C5: last-outcome, written next to the heartbeat on every path.
#
# The heartbeat answers "is the daemon ticking". It does not answer "is it
# enforcing", and those came apart repeatedly in this audit: a refusing pass, a
# paused pass and a healthy pass all stamped the same file and all read as
# healthy in `status`. One word next to it closes that gap, and `status` renders
# anything other than `enforced` as a remedy line.
# =============================================================================

@test "C5: a normal dry-run pass records dry-run" {
  run "$MEMCAP_ROOT/bin/memcap" watch
  [ "$status" -eq 0 ]
  run cat "$MEMCAP_STATE_HOME/memcap/last-outcome"
  [ "$output" = "dry-run" ]
}

@test "C5: a paused pass records paused, and still stamps the heartbeat" {
  "$MEMCAP_ROOT/bin/memcap" off
  run "$MEMCAP_ROOT/bin/memcap" watch
  "$MEMCAP_ROOT/bin/memcap" on
  [ -f "$MEMCAP_STATE_HOME/memcap/last-pass" ]
  run cat "$MEMCAP_STATE_HOME/memcap/last-outcome"
  [ "$output" = "paused" ]
}

@test "C5: an unsatisfiable budget records refused-misconfig" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  cat > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" <<-'EOF'
	TOTAL_BUDGET_GB=8
	DOCKER_BUDGET_GB=10
	EOF
  run "$MEMCAP_ROOT/bin/memcap" watch
  [ "$status" -ne 0 ]
  run cat "$MEMCAP_STATE_HOME/memcap/last-outcome"
  [ "$output" = "refused-misconfig" ]
}

@test "C5: a config that does not parse records refused-badconfig and enforces nothing" {
  # config.sh deliberately does NOT guard `watch` with mc_refuse_if_broken, so
  # that mc_watch reaches its own refusal and still stamps both files: a daemon
  # that is running but refusing must not be indistinguishable from a dead one.
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  cat > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" <<-'EOF'
	TOTAL_BUDGET_GB="10
	EOF
  run "$MEMCAP_ROOT/bin/memcap" watch
  [ "$status" -ne 0 ]
  [ -f "$MEMCAP_STATE_HOME/memcap/last-pass" ]
  run cat "$MEMCAP_STATE_HOME/memcap/last-outcome"
  [ "$output" = "refused-badconfig" ]
}

@test "C5: a real, healthy pass records enforced" {
  # MC_DRY_RUN=0 with a fixed fixture: nothing here can reach a real kill --
  # mc_ps_snapshot is a two-line fixture of fabricated pids, tier 2 is stubbed,
  # and tier 1 has no recorded roots in this sandboxed state directory.
  run env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=0 bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/config.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    mc_ps_snapshot() { printf '9001 1 2000000 /usr/local/bin/claude\n'; }
    mc_kill_over_budget() { :; }
    mc_record_roots() { :; }
    mc_watch
  "
  run cat "$MEMCAP_STATE_HOME/memcap/last-outcome"
  [ "$output" = "enforced" ]
}

@test "C5: a deliberate MC_NO_TOP=1 is NOT a fault -- the escape hatch keeps enforcing" {
  # C4 as amended publishes TWO signals, and C5 keys on the second:
  #   MC_MEASURE_DEGRADED -- totals are ps RSS, for any reason INCLUDING a
  #                          deliberate MC_NO_TOP=1
  #   MC_MEASURE_FAULT    -- that fallback was not asked for
  # Keying the outcome on DEGRADED would make a documented escape hatch refuse to
  # enforce and make `status` shout a remedy line at a setting the user typed
  # themselves. This is the test that pins which signal is which.
  run env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=0 MC_NO_TOP=1 bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/config.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    ps() { printf '9001 1 2000000 /usr/local/bin/claude\\n'; }
    mc_free_pct() { echo 50; }
    mc_kill_over_budget() { :; }
    mc_record_roots() { :; }
    mc_watch
  "
  run cat "$MEMCAP_STATE_HOME/memcap/last-outcome"
  [ "$output" = "enforced" ]
}

@test "C5: a measurement fault nobody asked for is reported as degraded-measurement" {
  # The contrast case: `top` is stubbed to fail outright, so the ps-RSS fallback
  # happens WITHOUT anyone requesting it. mc_measure_status_load is the only way
  # to see this -- mc_ps_snapshot runs inside `eval "$(... | mc_classify)"`, a
  # pipeline in a command substitution, so a global set in there dies two
  # subshells down. That is what made the original C4 unimplementable.
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  cat > "$fakebin/top" <<'SCRIPT'
#!/usr/bin/env bash
exit 1
SCRIPT
  chmod +x "$fakebin/top"

  run env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=0 PATH="$fakebin:$PATH" bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/config.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    ps() { printf '9001 1 2000000 /usr/local/bin/claude\\n'; }
    mc_free_pct() { echo 50; }
    mc_kill_over_budget() { :; }
    mc_record_roots() { :; }
    mc_watch
  "
  run cat "$MEMCAP_STATE_HOME/memcap/last-outcome"
  [ "$output" = "degraded-measurement" ]
}

@test "C5: a classifier that produces nothing refuses rather than running on unset variables" {
  # `eval "$(mc_ps_snapshot | mc_classify)"` on an empty string leaves every
  # variable below unset, and `set -u` would then take the pass down somewhere
  # much less obvious than here.
  run env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/config.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    mc_classify() { :; }
    mc_kill_over_budget() { echo TIER2_FIRED; }
    mc_record_roots() { :; }
    mc_watch
  "
  [ "$status" -ne 0 ]
  assert_not_contains "$output" "TIER2_FIRED"
  run cat "$MEMCAP_STATE_HOME/memcap/actions.log"
  assert_contains "$output" "the classifier produced no assignments"
}

# --- The liveness line -------------------------------------------------------
# The 28-hour outage that motivated the heartbeat was only detectable because a
# since-removed log line happened to fire every 30 minutes. Since v0.3.0 --
# correctly, since those lines were 94% of the file -- everything routine is
# throttled or conditional, so the same outage would now look identical to a
# quiet week. The pass count carries the information -- but only alongside the
# window it was counted over: the comment here used to call 60 passes "a healthy
# hour", and on a real Mac a healthy hour is 46-58, because launchd coalesces a
# StartInterval timer (two consecutive intervals measured in production were 73
# seconds apart, not 60). So the line states the elapsed window and the derived
# interval, and a stall is visible without knowing what the number should be.
@test "LIVENESS: the very first pass starts the clock rather than reporting a bogus window" {
  # No mark exists yet, so `now - mark` is the whole epoch. Deriving an elapsed
  # time or an interval from that prints nonsense.
  run "$MEMCAP_ROOT/bin/memcap" watch
  grep -q "watch: alive (memcap $MEMCAP_VERSION, liveness clock started)" "$MEMCAP_STATE_HOME/memcap/actions.log"
  run grep -c "passes in" "$MEMCAP_STATE_HOME/memcap/actions.log"
  [ "$output" = "0" ]
}

@test "LIVENESS: the line states the interval, not just the count" {
  d="$MEMCAP_STATE_HOME/memcap"
  mkdir -p "$d"
  # An hour ago, 47 passes already counted: this pass makes 48, i.e. one every
  # 75 seconds -- the real cadence on the author's machine, which the old line
  # would have rendered as the bare "48 passes" that reads like a fault.
  echo "$(( $(date +%s) - 3600 ))" > "$d/liveness-mark"
  echo 47 > "$d/pass-count"
  run "$MEMCAP_ROOT/bin/memcap" watch
  grep -qE "watch: alive \(memcap [0-9]+\.[0-9]+\.[0-9]+, 48 passes in 3[0-9]{3}s -- one every 7[0-9]s\)" "$d/actions.log"
  # ... and specifically THIS build's version, so a log excerpt identifies it.
  grep -q "watch: alive (memcap $MEMCAP_VERSION, 48 passes" "$d/actions.log"
}

@test "LIVENESS: the line is hourly, not per pass -- three passes in a row log once" {
  "$MEMCAP_ROOT/bin/memcap" watch >/dev/null
  "$MEMCAP_ROOT/bin/memcap" watch >/dev/null
  "$MEMCAP_ROOT/bin/memcap" watch >/dev/null
  run grep -c "watch: alive" "$MEMCAP_STATE_HOME/memcap/actions.log"
  [ "$output" = "1" ]
}

@test "LIVENESS: a paused daemon still proves it is ticking" {
  # A paused memcap is not a stopped one, and this is the line that says so.
  "$MEMCAP_ROOT/bin/memcap" off
  run "$MEMCAP_ROOT/bin/memcap" watch
  "$MEMCAP_ROOT/bin/memcap" on
  grep -q "watch: alive" "$MEMCAP_STATE_HOME/memcap/actions.log"
}

@test "LIVENESS: the next window reports how many passes went by" {
  "$MEMCAP_ROOT/bin/memcap" watch >/dev/null
  "$MEMCAP_ROOT/bin/memcap" watch >/dev/null
  "$MEMCAP_ROOT/bin/memcap" watch >/dev/null
  # LIVENESS_SEC=0 opens the window immediately rather than waiting an hour. The
  # counter resets when a mark is written, so the three passes counted here are
  # the two silent ones above plus this one -- the first pass wrote the mark.
  run env LIVENESS_SEC=0 "$MEMCAP_ROOT/bin/memcap" watch
  grep -q "watch: alive (memcap $MEMCAP_VERSION, 3 passes in " "$MEMCAP_STATE_HOME/memcap/actions.log"
}

# --- C1's fractional sibling: SOFT_TRIGGER -----------------------------------
@test "C1-FRAC: a non-numeric SOFT_TRIGGER falls back instead of firing tier 1 every pass" {
  # SOFT_TRIGGER reaches awk rather than `[`, so it fails open in the opposite
  # direction from the integer knobs: awk coerces garbage to 0, `agents >
  # budget*0` is true for any live agent, and the soft trigger fires on EVERY
  # pass. Same class of defect, opposite sign -- hence mc_frac.
  #
  # MIN_FREE_PCT=0 so the low-memory arm of the same `if` cannot fire and make
  # this pass for the wrong reason on a busy machine. 1 GB against a 10 GB agent
  # budget is nowhere near the real 0.80 trigger.
  run env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MIN_FREE_PCT=0 SOFT_TRIGGER=eighty MC_DRY_RUN=1 bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/config.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    mc_ps_snapshot() { printf '9001 1 1000000 /usr/local/bin/claude\n'; }
    mc_reap_orphans() { echo TIER1_FIRED; }
    mc_kill_over_budget() { :; }
    mc_record_roots() { :; }
    mc_watch
  "
  assert_not_contains "$output" "TIER1_FIRED"
  run cat "$MEMCAP_STATE_HOME/memcap/actions.log"
  assert_contains "$output" "SOFT_TRIGGER is not a number"
}

# --- Protection scope: tier 3 protects the agent, not the agent's whole tree --
# Applying the full propagated tree to tier 3 leaves it exactly as dead as the
# kill -0 bug did. Measured against this machine's real process table: of the 18
# sim-classified pids, 14 are descendants of a live agent session (seven Chrome
# processes and five Playwright headless shells, plus the two maestro MCP
# servers), and the remaining four are CoreSimulator daemons that match no kill
# pattern. Full-tree protection would have shipped a tier-3 fix that reclaims
# nothing, for a new reason.
@test "SCOPE: tier 3 eventually reclaims an idle browser that belongs to a live agent session" {
  perl -e 'sleep 600' "ms-playwright-fixture" & browser=$!
  wait_spawned "$browser"
  AGENTPIDS=""
  # The browser is inside the agent tree -- the ordinary shape, since that is how
  # a Playwright or devtools-MCP browser gets launched in the first place.
  # shellcheck disable=SC2034  # consumed by mc_filter_protected
  PROTECTEDPIDS="$browser"
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$browser"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  # An epoch of 1 is decades of idleness -- past both graces.
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$browser")"

  run mc_reap_sims

  kill "$browser" 2>/dev/null

  assert_contains "$output" "would kill"
  assert_contains "$output" "$browser"
}

@test "TREE-GRACE: an agent's own browser is held past the ordinary grace, and the hold is logged" {
  # The whole point of the longer clock: a browser an agent launched and has not
  # touched for eleven minutes is idle by the ordinary measure, but it still
  # belongs to somebody. Tier 3 waits.
  perl -e 'sleep 600' "ms-playwright-fixture" & browser=$!
  wait_spawned "$browser"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_filter_protected and mc_reap_sims
  PROTECTEDPIDS="$browser"
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$browser"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  # 660 seconds ago: past the 600s ordinary grace, inside the 1800s tree grace.
  printf '%s 999999\n' "$(( $(date +%s) - 660 ))" > "$(mc_sims_idle_stamp "$browser")"

  run mc_reap_sims

  kill "$browser" 2>/dev/null

  assert_not_contains "$output" "would kill"
  grep -q "it is a live agent session's own process" "$(mc_state_dir)/actions.log"
}

@test "TREE-GRACE: an unowned browser at the same idle age is reclaimed immediately" {
  # The contrast that shows the longer clock applies to tree membership and not
  # to everything: same fixture, same idle age, no session owns it.
  perl -e 'sleep 600' "ms-playwright-fixture" & browser=$!
  wait_spawned "$browser"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_filter_protected, sourced from enforce.sh
  PROTECTEDPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$browser"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '%s 999999\n' "$(( $(date +%s) - 660 ))" > "$(mc_sims_idle_stamp "$browser")"

  run mc_reap_sims

  kill "$browser" 2>/dev/null

  assert_contains "$output" "would kill"
  assert_contains "$output" "$browser"
}

@test "TREE-GRACE: a config that lowers it below the ordinary grace is clamped, not honoured" {
  # Left unclamped, TIER3_AGENT_TREE_GRACE_SEC=0 would silently invert the rule
  # and make a live session's own browsers the FIRST thing tier 3 reaps.
  perl -e 'sleep 600' "ms-playwright-fixture" & browser=$!
  wait_spawned "$browser"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_filter_protected and mc_reap_sims
  PROTECTEDPIDS="$browser"
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$browser"
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  TIER3_AGENT_TREE_GRACE_SEC=0
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  # Only 60 seconds idle -- inside the ordinary 600s grace, so the clamp must
  # keep it protected even though the knob says zero.
  printf '%s 999999\n' "$(( $(date +%s) - 60 ))" > "$(mc_sims_idle_stamp "$browser")"

  run mc_reap_sims

  kill "$browser" 2>/dev/null

  assert_not_contains "$output" "would kill"
}

@test "SCOPE: tier 3 still refuses to touch an agent CLI process itself" {
  # AGENTPIDS -- the direct CLI matches -- applies in both scopes. The narrower
  # scope drops the propagated TREE, not the agent.
  perl -e 'sleep 600' "ms-playwright-fixture" & victim=$!
  wait_spawned "$victim"
  # shellcheck disable=SC2034  # consumed by mc_filter_protected
  AGENTPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$victim"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$victim")"

  run mc_reap_sims

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  assert_not_contains "$output" "would kill"
}

@test "SCOPE: tiers 1 and 2 keep the full-tree protection" {
  # The contrast case. Tier 2 picks its victim by inference, and the inference is
  # what went wrong in six of ten real kills -- so under the default scope a pid
  # anywhere in the agent tree is off limits, exactly as before.
  sleep 600 & victim=$!
  wait_spawned "$victim"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_filter_protected
  PROTECTEDPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  MC_DRY_RUN=1
  run mc_kill_over_budget

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  assert_not_contains "$output" "would kill"
}

@test "XCRUN: a dry run does not announce a shutdown on a machine with nothing booted" {
  # SimulatorTrampoline can outlive every booted device, and does on this machine
  # -- so "a simulator process is idle" is not the same claim as "a device is
  # booted". The read-only `list devices booted` query settles it on the dry-run
  # path too, rather than the dry run reporting an action that would not happen.
  : > "$FAKE_XCRUN_BOOTED"
  perl -e 'sleep 600' "SimulatorTrampoline" & sim=$!
  wait_spawned "$sim"
  AGENTPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$sim"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$sim")"

  run mc_reap_sims

  kill "$sim" 2>/dev/null

  assert_not_contains "$output" "would shut down"
}

# =============================================================================
# Evidence, second form: a resource HELD OPEN by a live server.
#
# The flat evidence set answers "is this pid itself a tool memcap vetoes on", and
# that is too narrow. Walked on the real machine:
#
#   41308 Google Chrome                      <- sim-classified, CPU-flat, would be reaped
#    41307 node .../playwright-mcp
#     41263 npm exec @playwright/mcp@latest   <- MCP server
#      93098 claude                           <- live agent session
#
# The seven "idle Chrome processes" are ONE browser an MCP server holds across
# requests. Same category as the maestro MCP server: a long-lived server holding
# a resource that is idle BY DESIGN between calls. Ten minutes of CPU-flatness
# while the agent reads code is ordinary, and the reap surfaces as the next
# browser_navigate failing.
#
# These build the real three-level tree rather than asserting on the matcher in
# isolation -- an isolated assertion is what let the first version of the tooling
# veto ship with its bug still live.
# =============================================================================

# Builds: agent -> server (argv carries the token) -> browser. Sets $srv_pid,
# $browser_pid and $agent_pid for the caller.
mc_build_held_tree() {
  local token="$1" script="$BATS_TEST_TMPDIR/srv-$$.sh"
  cat > "$script" <<'EOS'
#!/bin/bash
perl -e 'sleep 600' "ms-playwright-fixture" &
wait
EOS
  chmod +x "$script"
  bash "$script" "$token" & srv_pid=$!
  browser_pid=""
  for _ in $(seq 1 250); do
    browser_pid=$(pgrep -P "$srv_pid" | head -1)
    [ -n "$browser_pid" ] && break
    sleep 0.02
  done
  [ -n "$browser_pid" ] || return 1
  wait_spawned "$browser_pid"
  # The real parent of the server, whatever shell bats is running this in.
  agent_pid=$(ps -o ppid= -p "$srv_pid" | tr -d ' ')
  [ -n "$agent_pid" ]
}

@test "HELD: a browser an MCP server holds open under a live session is never reaped" {
  mc_build_held_tree "@playwright/mcp@latest"
  # shellcheck disable=SC2034  # consumed by mc_held_by_agent_server
  AGENTPIDS="$agent_pid"
  PROTECTEDPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$browser_pid"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  # Decades idle and CPU-flat -- past every grace. Only the held-resource rule
  # can save it, which is the point.
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$browser_pid")"

  run mc_reap_sims

  kill "$browser_pid" "$srv_pid" 2>/dev/null

  assert_not_contains "$output" "would kill"
  grep -q "held open by live server pid" "$(mc_state_dir)/actions.log"
}

@test "HELD: the same browser IS reclaimable once its owning session is gone" {
  # The guarantee that keeps tier 3 from going inert for a third reason. A walk
  # that reaches ppid 1 without finding a live agent session is a resource whose
  # owner is dead -- the leaked browser, which is the population this tier exists
  # for. Here the session is simply not live, so the walk finds no agent.
  mc_build_held_tree "@playwright/mcp@latest"
  # No live agent session anywhere in the ancestry.
  AGENTPIDS=""
  PROTECTEDPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$browser_pid"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$browser_pid")"

  run mc_reap_sims

  kill "$browser_pid" "$srv_pid" 2>/dev/null

  assert_contains "$output" "would kill"
  assert_contains "$output" "$browser_pid"
}

@test "HELD: the rule matches the protocol, not a vendor -- chrome-devtools-mcp too" {
  # `@playwright/mcp` and `chrome-devtools-mcp` share no vendor, only the bounded
  # `mcp` token. Enumerating vendors here would be the MC_SIM_KILL_PATTERN edit
  # this file already refused to make, one layer up.
  mc_build_held_tree "chrome-devtools-mcp"
  # shellcheck disable=SC2034  # consumed by mc_held_by_agent_server
  AGENTPIDS="$agent_pid"
  PROTECTEDPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$browser_pid"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$browser_pid")"

  run mc_reap_sims

  kill "$browser_pid" "$srv_pid" 2>/dev/null

  assert_not_contains "$output" "would kill"
}

@test "HELD: an ordinary intermediary is not a server, so it does not hold anything" {
  # The contrast that keeps the rule from collapsing into "anything under an
  # agent session". A plain shell between the agent and the browser carries no
  # protocol token, so the browser falls through to the ordinary rules.
  mc_build_held_tree "some-ordinary-wrapper"
  # shellcheck disable=SC2034  # consumed by mc_held_by_agent_server
  AGENTPIDS="$agent_pid"
  PROTECTEDPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$browser_pid"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$browser_pid")"

  run mc_reap_sims

  kill "$browser_pid" "$srv_pid" 2>/dev/null

  assert_contains "$output" "would kill"
}

@test "HELD: memcap's own name does not match the protocol token" {
  # `mcp` must be bounded by non-alphanumerics on both sides, or the tool would
  # classify itself -- and every process with `memcap` in its argv -- as an MCP
  # server holding something.
  run bash -c "printf '%s' 'memcap watch' | grep -Eq '$MC_HELD_SERVER_PATTERN'"
  [ "$status" -ne 0 ]
  run bash -c "printf '%s' 'npm exec @playwright/mcp@latest' | grep -Eq '$MC_HELD_SERVER_PATTERN'"
  [ "$status" -eq 0 ]
  run bash -c "printf '%s' 'node /x/@modelcontextprotocol/server-filesystem/index.js' | grep -Eq '$MC_HELD_SERVER_PATTERN'"
  [ "$status" -eq 0 ]
}

@test "HELD: an unset AGENTPIDS fails closed -- everything reads as held" {
  # With no classification there is no way to tell a held resource from a leaked
  # one, and the wrong guess here kills a live browser.
  perl -e 'sleep 600' "ms-playwright-fixture" & browser=$!
  wait_spawned "$browser"
  unset AGENTPIDS
  run mc_held_by_agent_server "$browser"
  kill "$browser" 2>/dev/null
  [ "$status" -eq 0 ]
}

# --- The three bands, and the line that says which one a pid is in ------------
# 1. server-held   -- exempt while the holder is alive
# 2. agent tree, not server-held -- TIER3_AGENT_TREE_GRACE_SEC
# 3. unowned       -- ordinary SIM_IDLE_GRACE_SEC
#
# The band a pid is in, and what is holding it back, is the difference between
# diagnosing the next instance of this in minutes and in eleven days.
@test "BANDS: a server-held exclusion names the holder, not just the fact" {
  mc_build_held_tree "@playwright/mcp@latest"
  # shellcheck disable=SC2034  # consumed by mc_held_by_agent_server
  AGENTPIDS="$agent_pid"
  PROTECTEDPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$browser_pid"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$browser_pid")"

  run mc_reap_sims

  kill "$browser_pid" "$srv_pid" 2>/dev/null

  assert_not_contains "$output" "would kill"
  log="$(mc_state_dir)/actions.log"
  # The holding process, by pid and by name -- "held by something" is not enough
  # to act on.
  grep -q "held open by live server pid $srv_pid" "$log"
  grep -q "@playwright/mcp" "$log"
}

@test "BANDS: the innermost holder is named, not the outermost" {
  # A real chain is `npm exec @playwright/mcp` -> `node .../playwright-mcp` ->
  # browser. Both match the token; the one that actually owns the resource is the
  # inner one, and naming the outer would send someone looking in the wrong place.
  local outer="$BATS_TEST_TMPDIR/outer.sh" inner="$BATS_TEST_TMPDIR/inner.sh"
  cat > "$inner" <<'EOS'
#!/bin/bash
perl -e 'sleep 600' "ms-playwright-fixture" &
wait
EOS
  cat > "$outer" <<EOS
#!/bin/bash
bash "$inner" "node-playwright-mcp-inner" &
wait
EOS
  chmod +x "$inner" "$outer"
  bash "$outer" "npm-exec-mcp-outer" & outer_pid=$!
  inner_pid=""
  for _ in $(seq 1 250); do
    inner_pid=$(pgrep -P "$outer_pid" | head -1)
    [ -n "$inner_pid" ] && break
    sleep 0.02
  done
  [ -n "$inner_pid" ]
  browser=""
  for _ in $(seq 1 250); do
    browser=$(pgrep -P "$inner_pid" | head -1)
    [ -n "$browser" ] && break
    sleep 0.02
  done
  [ -n "$browser" ]
  wait_spawned "$browser"

  # shellcheck disable=SC2034  # consumed by mc_held_by_agent_server
  AGENTPIDS="$(ps -o ppid= -p "$outer_pid" | tr -d ' ')"
  # Called directly, not through `run`: `run` executes in a subshell, so the
  # MC_HELD_BY_* globals it sets would not survive back to the test body.
  mc_held_by_agent_server "$browser" || true
  held_pid="$MC_HELD_BY_PID"

  kill "$browser" "$inner_pid" "$outer_pid" 2>/dev/null

  [ "$held_pid" = "$inner_pid" ] || {
    echo "named holder $held_pid; innermost is $inner_pid, outermost is $outer_pid" >&2
    return 1
  }
}

@test "BANDS: a simulator held by a running tooling process is held by it too" {
  # The two forms of evidence compose: an ancestor already in the flat tooling set
  # holds its children just as a server-shaped one does. A simulator launched by a
  # live xcodebuild or maestro flow is not garbage because the flow paused.
  local script="$BATS_TEST_TMPDIR/tooling.sh"
  cat > "$script" <<'EOS'
#!/bin/bash
perl -e 'sleep 600' "ms-playwright-fixture" &
wait
EOS
  chmod +x "$script"
  bash "$script" "some-plain-wrapper" & tool_pid=$!
  browser=""
  for _ in $(seq 1 250); do
    browser=$(pgrep -P "$tool_pid" | head -1)
    [ -n "$browser" ] && break
    sleep 0.02
  done
  [ -n "$browser" ]
  wait_spawned "$browser"

  # The wrapper carries NO protocol token -- it is a holder purely because the
  # flat evidence set names it, which is what this test is about.
  # shellcheck disable=SC2034  # consumed by mc_veto_evidence_warm
  MC_VETO_EVIDENCE_PIDS="$tool_pid"
  # shellcheck disable=SC2034  # consumed by mc_held_by_agent_server
  AGENTPIDS="$(ps -o ppid= -p "$tool_pid" | tr -d ' ')"
  PROTECTEDPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$browser"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$browser")"

  run mc_reap_sims

  kill "$browser" "$tool_pid" 2>/dev/null

  assert_not_contains "$output" "would kill"
}

@test "BANDS: when the holder dies, its resource becomes an ordinary candidate" {
  # The correct transition, and it needs no special case: the resource reparents,
  # the ancestry walk no longer finds a holder or a session, and it falls to band
  # 3. This is what makes "exempt while the holder is alive" safe to say.
  mc_build_held_tree "@playwright/mcp@latest"
  # shellcheck disable=SC2034  # consumed by mc_held_by_agent_server
  AGENTPIDS="$agent_pid"
  # shellcheck disable=SC2034  # consumed by mc_filter_protected, sourced from enforce.sh
  PROTECTEDPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS="$browser_pid"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp "$browser_pid")"

  run mc_reap_sims
  assert_not_contains "$output" "would kill"

  # The holder exits; the browser reparents to init.
  kill "$srv_pid" 2>/dev/null
  for _ in $(seq 1 250); do
    [ "$(ps -o ppid= -p "$browser_pid" | tr -d ' ')" = "1" ] && break
    sleep 0.02
  done
  # The process table was cached for the pass; a new pass re-reads it.
  unset MC_PROC_TABLE

  run mc_reap_sims

  kill "$browser_pid" 2>/dev/null

  assert_contains "$output" "would kill"
  assert_contains "$output" "$browser_pid"
}

@test "BANDS: a sim-classified ancestor is never named as the holder" {
  # On this machine Chrome's own profile directory is `mcp-chrome-7ac0193`, so
  # Chrome matches the protocol token itself and the honest walk reported "pid
  # 41317 is held by pid 41308" -- Chrome holding Chrome. True, useless, and it
  # points whoever reads the log at the wrong process. The walk skips
  # sim-classified ancestors and names the server that actually owns the browser.
  local script="$BATS_TEST_TMPDIR/mcp-holder.sh"
  cat > "$script" <<'EOS'
#!/bin/bash
# Stands in for Chrome: sim-classified AND carrying a protocol token, exactly
# like a browser whose profile dir is named after the MCP tool that launched it.
perl -e 'sleep 600' "ms-playwright-fixture --user-data-dir=/tmp/mcp-chrome-abc" &
wait
EOS
  chmod +x "$script"
  bash "$script" "node-playwright-mcp" & server=$!
  browser=""
  for _ in $(seq 1 250); do
    browser=$(pgrep -P "$server" | head -1)
    [ -n "$browser" ] && break
    sleep 0.02
  done
  [ -n "$browser" ]
  wait_spawned "$browser"

  # Both the browser AND the intermediate are sim-classified; only the outer
  # `bash ... node-playwright-mcp` is a real server.
  # shellcheck disable=SC2034  # consumed by mc_held_by_agent_server
  SIMPIDS="$browser"
  # shellcheck disable=SC2034  # consumed by mc_held_by_agent_server
  AGENTPIDS="$(ps -o ppid= -p "$server" | tr -d ' ')"
  # Called directly, not via `run`: the MC_HELD_BY_* globals must survive.
  mc_held_by_agent_server "$browser" || true
  held="$MC_HELD_BY_PID"

  kill "$browser" "$server" 2>/dev/null

  [ "$held" = "$server" ] || {
    echo "named holder $held; expected the server $server" >&2
    return 1
  }
}

@test "ADAPTIVE: green and yellow footprint excess never trigger tier 2; red retains gates" {
  for pressure in 1 2 4 0; do
    run env QUEUE_POLICY=adaptive BUDGET_MODE=shared TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 fixture_pressure="$pressure" bash -c "
      for module in common config budget detect measure classify roots status enforce; do
        source '$MEMCAP_ROOT/libexec/'\"\$module\"'.sh'
      done
      sysctl() {
        case \"\$*\" in
          '-n kern.memorystatus_vm_pressure_level') echo \"\$fixture_pressure\" ;;
          '-n hw.memsize') echo 25769803776 ;;
          *) command sysctl \"\$@\" ;;
        esac
      }
      mc_snapshot_capture() {
        MC_CAPTURE_SNAPSHOT='9001 1 12000000 /usr/local/bin/claude'
        MC_MEASURE_FAULT=0
        MC_MEASURE_DEGRADED=0
      }
      mc_free_pct() { echo 30; }
      mc_kill_over_budget() { echo TIER2_SELECTED; }
      mc_record_roots() { :; }
      mc_reap_orphans() { :; }
      mc_watch
    "
    [ "$status" = 0 ] || return 1
    if [ "$pressure" = 4 ]; then
      assert_contains "$output" TIER2_SELECTED
    else
      assert_not_contains "$output" TIER2_SELECTED
    fi
  done
}
