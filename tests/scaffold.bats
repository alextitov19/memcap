load helper

setup() { setup_common; source "$MEMCAP_ROOT/libexec/common.sh"; }

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
