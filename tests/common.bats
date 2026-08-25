load helper

bats_require_minimum_version 1.5.0

setup() {
  setup_common
  export MC_DRY_RUN=1
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
}

# --- mc_format_age -----------------------------------------------------------
# Moved here from status.sh so `memcap on` and `memcap status` describe a
# duration the same way. It is the units mc_etime_secs parses, in reverse.

@test "mc_format_age renders seconds, minutes, hours and days" {
  [ "$(mc_format_age 0)" = "0s" ]
  [ "$(mc_format_age 59)" = "59s" ]
  [ "$(mc_format_age 60)" = "1m" ]
  [ "$(mc_format_age 3599)" = "59m" ]
  [ "$(mc_format_age 3600)" = "1h" ]
  [ "$(mc_format_age 86399)" = "23h" ]
  [ "$(mc_format_age 86400)" = "1d" ]
}

# --- mc_pid_alive (contract C6) ----------------------------------------------
# `kill -0` returns failure for EPERM ("alive, but not yours to signal") exactly
# as it does for ESRCH ("dead"). That confusion kept tier 3 from firing for the
# tool's entire life: root-owned simdiskimaged is matched as a sim on any Mac
# with Xcode, and failed `kill -0` every pass.

@test "mc_pid_alive is true for a live pid and false for a dead one" {
  run mc_pid_alive "$$"
  [ "$status" -eq 0 ]
  # A pid that has certainly exited: spawn one and wait for it.
  sh -c 'exit 0' &
  local dead=$!
  wait "$dead" 2>/dev/null || true
  run mc_pid_alive "$dead"
  [ "$status" -ne 0 ]
}

@test "mc_pid_alive reports a pid it has no permission to signal as alive" {
  # pid 1 (launchd) is root-owned and never ours to signal. `kill -0 1` fails
  # for a live process; `ps -p 1` does not. This is the exact confusion C6 exists
  # to remove, so it is pinned against the real process table rather than a stub.
  run mc_pid_alive 1
  [ "$status" -eq 0 ]
}

# --- the awake clock ---------------------------------------------------------
# Wall-clock age cannot tell a stopped daemon from a closed lid. kern.monotonicclock
# does not advance while the machine sleeps, so the difference between two
# readings is elapsed AWAKE time -- which is what staleness should be judged on.

@test "mc_awake_secs honours the MC_AWAKE_SECS override" {
  MC_AWAKE_SECS=4242
  run mc_awake_secs
  [ "$status" -eq 0 ]
  [ "$output" = "4242" ]
}

@test "mc_awake_secs fails rather than printing a non-number" {
  MC_AWAKE_SECS="not-a-number"
  run mc_awake_secs
  [ "$status" -ne 0 ]
  [ -z "$output" ]
}

@test "mc_awake_secs reads a plain integer off the real machine" {
  # No override: whatever this machine reports must be a bare integer or an
  # honest failure -- never a string a caller would go on to do arithmetic with.
  unset MC_AWAKE_SECS
  if run mc_awake_secs && [ "$status" -eq 0 ]; then
    assert_matches "$output" '^[0-9]+$'
  fi
}

@test "mc_stamp_heartbeat writes both the wall clock and the awake clock" {
  MC_AWAKE_SECS=1000
  mc_stamp_heartbeat
  assert_matches "$(cat "$(mc_state_dir)/last-pass")" '^[0-9]+$'
  [ "$(cat "$(mc_state_dir)/last-pass-awake")" = "1000" ]
}

# A stale awake stamp is worse than none: it would be read as belonging to the
# current boot and could hide a genuinely dead daemon.
@test "mc_stamp_heartbeat removes a stale awake stamp when the clock is unreadable" {
  MC_AWAKE_SECS=1000
  mc_stamp_heartbeat
  [ -f "$(mc_state_dir)/last-pass-awake" ]
  export MC_AWAKE_SECS="unavailable"
  mc_stamp_heartbeat
  [ ! -f "$(mc_state_dir)/last-pass-awake" ]
  [ -f "$(mc_state_dir)/last-pass" ]
}

@test "mc_last_wake honours the MC_LAST_WAKE override and rejects junk" {
  MC_LAST_WAKE=1700000000
  run mc_last_wake
  [ "$status" -eq 0 ]
  [ "$output" = "1700000000" ]
  export MC_LAST_WAKE="yesterday"
  run mc_last_wake
  [ "$status" -ne 0 ]
}

# kern.waketime reads `{ sec = 0, usec = 0 }` on a machine that has not slept
# since boot -- "no wake recorded", not "the epoch". It must fall through to
# kern.boottime rather than report 1970.
@test "mc_last_wake never returns a zero epoch from the real sysctls" {
  unset MC_LAST_WAKE
  if run mc_last_wake && [ "$status" -eq 0 ]; then
    assert_matches "$output" '^[0-9]+$'
    [ "$output" -gt 0 ]
  fi
}

# --- pausing -----------------------------------------------------------------
# `memcap off`/`on` wrote NOTHING to actions.log, and `on` deletes the marker,
# so after the fact a week spent paused and a week spent dead were the same
# week. Both transitions are events now, and the pause dates itself.

@test "mc_pause logs the pause and dates the marker" {
  mc_pause
  [ -f "$(mc_state_dir)/paused" ]
  assert_matches "$(cat "$(mc_state_dir)/paused")" '^[0-9]+$'
  assert_contains "$(cat "$(mc_state_dir)/actions.log")" "PAUSED"
}

@test "mc_resume logs how long the pause lasted" {
  mkdir -p "$(mc_state_dir)"
  echo "$(( $(date +%s) - 7200 ))" > "$(mc_state_dir)/paused"
  run mc_resume
  [ "$status" -eq 0 ]
  assert_contains "$output" "was paused for 2h"
  [ ! -f "$(mc_state_dir)/paused" ]
  assert_contains "$(cat "$(mc_state_dir)/actions.log")" "RESUMED"
  assert_contains "$(cat "$(mc_state_dir)/actions.log")" "was paused for 2h"
}

# A marker left by an older memcap (or a hand `touch`) has no epoch inside it.
# Falling back to its mtime keeps it dateable rather than making the whole
# duration feature depend on who wrote the file.
@test "mc_paused_since falls back to the marker's mtime when it is empty" {
  mkdir -p "$(mc_state_dir)"
  touch "$(mc_state_dir)/paused"
  run mc_paused_since
  [ "$status" -eq 0 ]
  assert_matches "$output" '^[0-9]+$'
  [ "$output" -gt 0 ]
}

@test "mc_paused_since fails when nothing is paused" {
  run mc_paused_since
  [ "$status" -ne 0 ]
  [ -z "$output" ]
}

# --- the CLI verbs -----------------------------------------------------------

@test "memcap off records the pause in the audit trail" {
  run "$MEMCAP_ROOT/bin/memcap" off
  [ "$status" -eq 0 ]
  assert_contains "$output" "memcap paused"
  assert_contains "$(cat "$(mc_state_dir)/actions.log")" "off: enforcement PAUSED"
}

@test "memcap on records the resume, and how long the pause held" {
  mkdir -p "$(mc_state_dir)"
  echo "$(( $(date +%s) - 300 ))" > "$(mc_state_dir)/paused"
  run "$MEMCAP_ROOT/bin/memcap" on
  [ "$status" -eq 0 ]
  assert_contains "$output" "memcap resumed"
  assert_contains "$output" "was paused for 5m"
  assert_contains "$(cat "$(mc_state_dir)/actions.log")" "on: enforcement RESUMED"
}

@test "memcap on still works when nothing was paused" {
  run "$MEMCAP_ROOT/bin/memcap" on
  [ "$status" -eq 0 ]
  assert_contains "$output" "memcap resumed"
}
