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

@test "another device and command-line mentions do not acquire driver protection" {
  fixture_table="$fixture_table
an unrelated command argument contains a newline"
  run mc_sim_device_held 0A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F9
  [ "$status" -eq 1 ]
  fixture_table="10 1 $fixture_uid codex app-server
20 10 $fixture_uid java maestro.cli.AppKt mcp
30 20 $fixture_uid rg /Users/test/.maestro/deps/simulator-server ios --id $device"
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
