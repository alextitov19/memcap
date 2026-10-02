load helper

setup() {
  setup_common
  export MC_DRY_RUN=1
}

@test "ANALYTICS: immutable release evidence and fixed-request learning" {
  run python3 "$MEMCAP_ROOT/tests/test_release_evidence.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "ANALYTICS: private local recorder and truthful performance reports" {
  run python3 "$MEMCAP_ROOT/tests/test_analytics.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "ANALYTICS: CLI remains independent of owner enforcement pause" {
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  touch "$MEMCAP_STATE_HOME/memcap/paused"
  run "$MEMCAP_ROOT/bin/memcap" analytics enable
  [ "$status" = 0 ]
  assert_contains "$output" '"enabled": true'
  run "$MEMCAP_ROOT/bin/memcap" analytics status
  [ "$status" = 0 ]
  assert_contains "$output" '"enforcement_paused": true'
  run "$MEMCAP_ROOT/bin/memcap" analytics disable
  [ "$status" = 0 ]
  [ -f "$MEMCAP_STATE_HOME/memcap/paused" ]
}
