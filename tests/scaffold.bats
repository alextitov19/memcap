load helper

# `run !` (used below) needs bats >= 1.5.0; CI and the Development section's
# `brew install bats-core` both get a current release, well past this floor.
bats_require_minimum_version 1.5.0

setup() {
  setup_common
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
}

@test "config file lives under the config home" {
  run mc_config_file
  [ "$status" -eq 0 ]
  [ "$output" = "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" ]
}

@test "pause file absent means not paused" {
  run mc_is_paused
  [ "$status" -ne 0 ]
}

@test "pause file present means paused" {
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  touch "$MEMCAP_STATE_HOME/memcap/paused"
  run mc_is_paused
  [ "$status" -eq 0 ]
}

@test "mc_log appends to the action log" {
  mc_log "hello"
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "hello"
}

# Review round 1, Finding 2: mc_notify interpolates its argument into an AppleScript
# string unescaped. A quote or backslash in a message (callers build messages from
# process data) would otherwise break the script. Stub osascript to capture the exact
# argument it receives rather than firing a real notification.
stub_osascript() {
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  capture="$BATS_TEST_TMPDIR/osascript-arg"
  cat > "$fakebin/osascript" <<SCRIPT
#!/usr/bin/env bash
printf '%s' "\$2" > "$capture"
SCRIPT
  chmod +x "$fakebin/osascript"
}

@test "mc_notify escapes a double quote so it cannot break out of the AppleScript string" {
  stub_osascript
  PATH="$fakebin:$PATH" mc_notify 'killed "vite" server'
  run cat "$capture"
  [ "$output" = 'display notification "killed \"vite\" server" with title "memcap"' ]
}

@test "mc_notify escapes a backslash so it is not read as an escape sequence" {
  stub_osascript
  PATH="$fakebin:$PATH" mc_notify 'path C:\temp'
  run cat "$capture"
  [ "$output" = 'display notification "path C:\\temp" with title "memcap"' ]
}

# --- Final review, small fix: usage omits docker apply / bare docker ----------
@test "the usage line mentions docker apply --force and that bare docker works" {
  run "$MEMCAP_ROOT/bin/memcap" help
  assert_contains "$output" "docker [apply [--force]]"
}

# --- Final review follow-up: throttle the per-pass log lines ------------------
# 24 hours of real running showed two per-pass status lines drowning the actual
# kill records (94% of the file). mc_log_throttled must log on the first call,
# stay quiet for LOG_THROTTLE_SEC, then log again once the window elapses;
# mc_log_throttle_clear must reset a key immediately regardless of the window.
@test "mc_log_throttled logs on the first call for a key" {
  mc_log_throttled "k1" "first occurrence"
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "first occurrence"
}

@test "mc_log_throttled does not repeat a key within the window" {
  mc_log_throttled "k2" "occurrence one"
  mc_log_throttled "k2" "occurrence two"
  log="$(mc_state_dir)/actions.log"
  grep -q "occurrence one" "$log"
  # `run !`, not a bare `!`: in bats, `!` alone does not fail the test if it is
  # not the test's last command (the same class of pitfall as the bare `[[ ]]`
  # issue documented in enforce.bats -- confirmed here by shellcheck's own
  # SC2314, not just by hand).
  run ! grep -q "occurrence two" "$log"
}

@test "mc_log_throttled logs again once the window elapses" {
  # LOG_THROTTLE_SEC=0 rather than sleeping for real: the elapsed time since the
  # first call's stamp is always >= 0, so the gate opens on the very next call --
  # the same trick this file's SIM_IDLE_GRACE_SEC=0 tests use.
  # shellcheck disable=SC2034  # consumed by mc_log_throttled, sourced from common.sh
  LOG_THROTTLE_SEC=0
  mc_log_throttled "k3" "occurrence one"
  mc_log_throttled "k3" "occurrence two"
  log="$(mc_state_dir)/actions.log"
  grep -q "occurrence one" "$log"
  grep -q "occurrence two" "$log"
}

@test "mc_log_throttle_clear resets a key so the next call logs immediately" {
  mc_log_throttled "k4" "occurrence one"
  mc_log_throttle_clear "k4"
  mc_log_throttled "k4" "occurrence two"
  log="$(mc_state_dir)/actions.log"
  grep -q "occurrence one" "$log"
  grep -q "occurrence two" "$log"
}

@test "mc_log_throttled keys throttle independently of each other" {
  mc_log_throttled "keyA" "messageA-first"
  mc_log_throttled "keyB" "messageB-first"
  mc_log_throttled "keyA" "messageA-second"
  mc_log_throttled "keyB" "messageB-second"
  log="$(mc_state_dir)/actions.log"
  grep -q "messageA-first" "$log"
  grep -q "messageB-first" "$log"
  run ! grep -q "messageA-second" "$log"
  run ! grep -q "messageB-second" "$log"
}
