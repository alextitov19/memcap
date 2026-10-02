load helper

setup() {
  setup_common
  export MC_DRY_RUN=1
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
