load helper
setup() {
  setup_common
  export MC_DRY_RUN=1
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/enforce.sh"
  # shellcheck disable=SC2034 # consumed by sourced enforcement
  AGENTPIDS=10
  device=F096B0A2-20F5-4637-BC0E-19098780FA83
  fixture_uid=$(id -u)
  fixture_table="10 1 $fixture_uid codex app-server
20 10 $fixture_uid java maestro.cli.AppKt mcp
30 20 $fixture_uid /Users/test/.maestro/deps/simulator-server ios --id $device"
  # shellcheck disable=SC2329 # Called indirectly by the sourced ownership check.
  ps() { printf '%s\n' "$fixture_table"; }
}

@test "a device held by a live agent MCP driver is protected across process trees" {
  run mc_sim_device_held "$device"
  [ "$status" -eq 0 ]
}

@test "another device and mentions outside an agent tree do not acquire driver protection" {
  fixture_table="$fixture_table
an unrelated command argument contains a newline"
  run mc_sim_device_held 0A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F9
  [ "$status" -eq 1 ]
  fixture_table="10 1 $fixture_uid codex app-server
40 1 $fixture_uid rg /Users/test/.maestro/deps/simulator-server ios --id $device"
  run mc_sim_device_held "$device"
  [ "$status" -eq 1 ]
  fixture_table="10 1 $fixture_uid sleep 600
20 10 $fixture_uid java maestro.cli.AppKt mcp
30 20 $fixture_uid /Users/test/.maestro/deps/simulator-server ios --id $device"
  run mc_sim_device_held "$device"
  [ "$status" -eq 1 ]
}

@test "departed agent and foreign driver do not retain a leaked device" {
  # shellcheck disable=SC2034 # consumed by sourced enforcement
  AGENTPIDS=""
  run mc_sim_device_held "$device"
  [ "$status" -eq 1 ]
  # shellcheck disable=SC2034 # consumed by sourced enforcement
  AGENTPIDS=10
  fixture_table="10 1 $fixture_uid codex app-server
20 10 $fixture_uid java maestro.cli.AppKt mcp
30 20 $((fixture_uid+1)) /Users/test/.maestro/deps/simulator-server ios --id $device"
  run mc_sim_device_held "$device"
  [ "$status" -eq 1 ]
}

@test "missing ancestry or failed process observation retains a held device" {
  fixture_table="30 20 $fixture_uid /Users/test/.maestro/deps/simulator-server ios --id $device"
  run mc_sim_device_held "$device"
  [ "$status" -eq 0 ]
  fixture_table="malformed process observation"
  run mc_sim_device_held "$device"
  [ "$status" -eq 0 ]
  fixture_table="10 1 $fixture_uid codex app-server
20 10 $fixture_uid
30 20 $fixture_uid /Users/test/.maestro/deps/simulator-server ios --id $device"
  run mc_sim_device_held "$device"
  [ "$status" -eq 0 ]
  # shellcheck disable=SC2329 # Called indirectly by the sourced ownership check.
  ps() { return 1; }
  run mc_sim_device_held "$device"
  [ "$status" -eq 0 ]
}

@test "a directly owned simulator driver stays protected while its agent waits" {
  fixture_table="10 1 $fixture_uid codex app-server
30 10 $fixture_uid /Users/test/.maestro/deps/simulator-server ios --id $device"
  run mc_sim_device_held "$device"
  [ "$status" -eq 0 ]
}

@test "a queued agent job naming the device holds it while the agent waits" {
  fixture_table="10 1 $fixture_uid claude
20 10 $fixture_uid /bin/zsh -c memcap run --wait 86400 -- xcodebuild test -destination id=$device
30 20 $fixture_uid python3 /opt/memcap/libexec/scheduler.py run -- xcodebuild test -destination id=$device"
  run mc_sim_device_held "$device"
  [ "$status" -eq 0 ]
  # The same job after its agent has gone no longer holds the device.
  # shellcheck disable=SC2034 # consumed by sourced enforcement
  AGENTPIDS=""
  run mc_sim_device_held "$device"
  [ "$status" -eq 1 ]
}

@test "a name destination holds only the exactly named device" {
  fixture_table="10 1 $fixture_uid claude
20 10 $fixture_uid python3 scheduler.py run -- xcodebuild test -destination platform=iOS Simulator,name=iPhone 17 Pro,OS=26.5"
  run mc_sim_device_held "$device" "iPhone 17 Pro"
  [ "$status" -eq 0 ]
  run mc_sim_device_held "$device" "iPhone 17"
  [ "$status" -eq 1 ]
  fixture_table="10 1 $fixture_uid claude
20 10 $fixture_uid python3 scheduler.py run -- xcodebuild test -destination 'platform=iOS Simulator,name=iPhone 17 Pro Max'"
  run mc_sim_device_held "$device" "iPhone 17 Pro"
  [ "$status" -eq 1 ]
  run mc_sim_device_held "$device" "iPhone 17 Pro Max"
  [ "$status" -eq 0 ]
  fixture_table="10 1 $fixture_uid claude
20 10 $fixture_uid xcodebuild test -destination platform=iOS Simulator,name=iPhone 17 Pro -quiet"
  run mc_sim_device_held "$device" "iPhone 17 Pro"
  [ "$status" -eq 0 ]
}

@test "the simulator's own processes never hold their device" {
  fixture_table="10 1 $fixture_uid claude
50 10 $fixture_uid /Library/Developer/CoreSimulator/Devices/$device/data/launchd_sim"
  run mc_sim_device_held "$device"
  [ "$status" -eq 1 ]
}
