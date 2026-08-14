load helper
setup() { setup_common; }

@test "status runs and reports a budget line" {
  run "$MEMCAP_ROOT/bin/memcap" status
  [ "$status" -eq 0 ]
  [[ "$output" == *budget* ]]
}

@test "status names the config file it used" {
  run "$MEMCAP_ROOT/bin/memcap" status
  [[ "$output" == *memcap.conf* ]]
}

@test "status is read-only: it never logs an enforcement action" {
  run "$MEMCAP_ROOT/bin/memcap" status
  [ "$status" -eq 0 ]
  [ ! -s "$MEMCAP_STATE_HOME/memcap/actions.log" ]
}
