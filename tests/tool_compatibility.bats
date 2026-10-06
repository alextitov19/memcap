load helper

setup() {
  setup_common
  export MC_DRY_RUN=1
}

@test "COMPAT: new tool demand, runtime selection and process identity" {
  run python3 "$MEMCAP_ROOT/tests/test_tool_compatibility.py"
  printf '%s\n' "$output"
  [ "$status" -eq 0 ]
  assert_contains "$output" "OK"
}
