load helper
setup() {
  setup_common
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/roots.sh"
}

@test "recording a root makes it readable back" {
  mc_record_root "$HOME/code/projectA"
  run mc_sweep_roots
  [[ "$output" == *"$HOME/code/projectA"* ]]
}

@test "roots are deduplicated" {
  mc_record_root "$HOME/code/projectA"
  mc_record_root "$HOME/code/projectA"
  run bash -c "source '$MEMCAP_ROOT/libexec/common.sh'; source '$MEMCAP_ROOT/libexec/roots.sh'; mc_sweep_roots | wc -l"
  [ "$(echo "$output" | tr -d ' ')" = "1" ]
}

@test "HOME itself is rejected as too broad" {
  run mc_root_is_safe "$HOME"
  [ "$status" -ne 0 ]
}

@test "root directory is rejected" {
  run mc_root_is_safe "/"
  [ "$status" -ne 0 ]
}

@test "a project two levels below HOME is accepted" {
  run mc_root_is_safe "$HOME/code/projectA"
  [ "$status" -eq 0 ]
}

@test "an unsafe root is never recorded" {
  mc_record_root "$HOME"
  run mc_sweep_roots
  [ -z "$output" ]
}
