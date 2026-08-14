load helper

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
  [[ "$output" == *hello* ]]
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
