load helper
setup() { setup_common;
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/detect.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/measure.sh"; }

@test "total ram is a plausible integer" {
  run mc_total_ram_gb
  [ "$output" -ge 4 ]
  [ "$output" -le 1024 ]
}

@test "cpu count is a plausible integer" {
  run mc_cpu_count
  [ "$output" -ge 1 ]
}

@test "snapshot emits pid ppid kb command lines" {
  run bash -c "source '$MEMCAP_ROOT/libexec/measure.sh'; mc_ps_snapshot | head -1"
  assert_matches "$output" '^[[:space:]]*[0-9]+[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+'
}

# Merging two data sources must not silently drop processes: a lost row is a process
# that escapes the budget entirely. Assert that against a process we KNOW is alive for
# the whole test, rather than comparing two `ps` counts taken moments apart -- the
# snapshot takes ~0.4s to build, and process churn during it makes a count comparison
# racy by construction.
@test "snapshot includes a process known to be alive throughout" {
  sleep 30 &
  marker=$!
  run bash -c "source '$MEMCAP_ROOT/libexec/measure.sh'; mc_ps_snapshot | awk -v p=$marker '\$1==p'"
  kill "$marker" 2>/dev/null || true
  [ -n "$output" ]
}

# Proportional, not absolute: churn scales with how busy the machine is, so a fixed
# slack of N processes is a flake waiting for a loaded CI runner.
@test "snapshot row count tracks ps within normal churn" {
  psn=$(ps -Ao pid= | wc -l | tr -d ' ')
  snapn=$(bash -c "source '$MEMCAP_ROOT/libexec/measure.sh'; mc_ps_snapshot | wc -l" | tr -d ' ')
  [ "$snapn" -ge $((psn * 90 / 100)) ]
}

@test "snapshot falls back to ps RSS when top is disabled" {
  run bash -c "source '$MEMCAP_ROOT/libexec/measure.sh'; MC_NO_TOP=1 mc_ps_snapshot | head -1"
  [ "$status" -eq 0 ]
  assert_matches "$output" '^[[:space:]]*[0-9]+[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+'
}

@test "free percentage is between 0 and 100" {
  run mc_free_pct
  [ "$output" -ge 0 ]
  [ "$output" -le 100 ]
}

# --- Final review, residual: EXTRA_AGENTS must not be exposed to globbing -----
# Same issue as classify.sh's mc_extra_agent_pattern: this loop also word-splits
# EXTRA_AGENTS unquoted, on purpose, but that exposes it to pathname expansion.
@test "EXTRA_AGENTS containing a glob character is not expanded against the cwd" {
  globdir="$BATS_TEST_TMPDIR/globtest"
  mkdir -p "$globdir"
  touch "$globdir/aa" "$globdir/ab"
  run bash -c "cd '$globdir' && source '$MEMCAP_ROOT/libexec/detect.sh' && EXTRA_AGENTS='a*' mc_installed_agents"
  [ "$status" -eq 0 ]
  assert_not_contains "$output" "aa"
  assert_not_contains "$output" "ab"
}

# --- Final review, residual: mc_ps_snapshot's temp file cleanup ---------------
# A `trap ... RETURN` was tried here and reverted: bash 3.2 does not scope a
# RETURN trap to the installing function, so it leaked into the CALLER's own
# return and crashed the shell on the now-gone $tmp under `set -u` (reproduced).
# Plain `rm -f "$tmp"` at the end of the normal path, guarded by this regression
# test, is the safer choice for a function whose only call sites today are
# already inside a command-substitution subshell that a real interrupt tears
# down entirely regardless of what's trapped inside it.
@test "mc_ps_snapshot leaves no temp file behind after a normal call" {
  sandbox="$BATS_TEST_TMPDIR/tmpdir-sandbox"
  mkdir -p "$sandbox"
  TMPDIR="$sandbox" bash -c "source '$MEMCAP_ROOT/libexec/measure.sh'; mc_ps_snapshot > /dev/null"
  run bash -c "ls -A '$sandbox'"
  [ -z "$output" ]
}

# Regression guard for the specific bash-3.2 bug above: calling mc_ps_snapshot
# from inside another function must not crash THAT function's own return. A
# `trap ... RETURN` inside mc_ps_snapshot would leak here and die on the
# now-unset $tmp under `set -u` before "caller-survived" ever prints.
@test "mc_ps_snapshot does not leak a trap into its caller's return" {
  run bash -c "
    source '$MEMCAP_ROOT/libexec/measure.sh'
    set -u
    caller_fn() { mc_ps_snapshot >/dev/null; echo caller-survived; }
    caller_fn
  "
  [ "$status" -eq 0 ]
  assert_contains "$output" "caller-survived"
}
