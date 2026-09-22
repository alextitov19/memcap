load helper

setup() {
  setup_common
  export MC_DRY_RUN=1
}

@test "INTEGRATE: profile installation, preservation and doctor behavioral suite" {
  run python3 "$MEMCAP_ROOT/tests/test_integrate.py"
  [ "$status" = 0 ]
  assert_contains "$output" 'OK'
}
