load helper

setup() {
  setup_common
  export MC_DRY_RUN=1
}

@test "THROUGHPUT: compiler predictions retain evidence across bounded source edits" {
  run python3 "$MEMCAP_ROOT/tests/test_compiler_profiles.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "THROUGHPUT: owned process observations preserve identity and uncertainty" {
  run python3 "$MEMCAP_ROOT/tests/test_job_observation.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "THROUGHPUT: sample compatibility and freshness remain independent" {
  run python3 "$MEMCAP_ROOT/tests/test_sampling_context.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "THROUGHPUT: analytics distinguish observation coverage from useful learning" {
  run python3 "$MEMCAP_ROOT/tests/test_learning_analytics.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "THROUGHPUT: reusable estimates and owned observations integrate safely" {
  run python3 "$MEMCAP_ROOT/tests/test_learning_integration.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "THROUGHPUT: identical-headroom replay retains cold and red safety gates" {
  run python3 "$MEMCAP_ROOT/tests/test_throughput_replay.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "THROUGHPUT: learning and sampling negative controls actually fail" {
  run python3 "$MEMCAP_ROOT/tests/test_learning_negative.py"
  [ "$status" = 0 ]
  assert_contains "$output" '9/9 deliberate learning and sampling regressions detected'
}

@test "THROUGHPUT: pending work, lifecycle retention and exact admission evidence" {
  run python3 "$MEMCAP_ROOT/tests/test_throughput.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "ENVIRONMENTS: disposable ownership never reaches real Docker" {
  run python3 "$MEMCAP_ROOT/tests/test_environments.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "THROUGHPUT: negative controls detect disabled fixes" {
  run python3 "$MEMCAP_ROOT/tests/test_throughput_negative.py"
  [ "$status" = 0 ]
  assert_contains "$output" '6/6 deliberate regressions detected'
}
