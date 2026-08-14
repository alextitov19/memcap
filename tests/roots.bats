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

@test "a path escaping HOME with .. is rejected" {
  run mc_root_is_safe "$HOME/../etc"
  [ "$status" -ne 0 ]
}

@test "a path climbing to the filesystem root with .. is rejected" {
  run mc_root_is_safe "$HOME/x/y/../../../../"
  [ "$status" -ne 0 ]
}

@test "a symlink pointing outside HOME is rejected" {
  mkdir -p "$HOME/.memcap-test-$$"
  ln -sfn /etc "$HOME/.memcap-test-$$/link"
  run mc_root_is_safe "$HOME/.memcap-test-$$/link"
  rm -rf "$HOME/.memcap-test-$$"
  [ "$status" -ne 0 ]
}

@test "an intermediate symlink with a missing leaf is rejected" {
  ln -sfn /tmp "$HOME/.memcap-test-link-$$"
  run mc_root_is_safe "$HOME/.memcap-test-link-$$/nonexistent"
  rm -f "$HOME/.memcap-test-link-$$"
  [ "$status" -ne 0 ]
}

@test "a trailing slash cannot pass one level off as two" {
  run mc_root_is_safe "$HOME/code/"
  [ "$status" -ne 0 ]
}

@test "a relative path is rejected without hanging" {
  run mc_root_is_safe "relative/path"
  [ "$status" -ne 0 ]
}

@test "a path containing a newline is rejected" {
  run mc_root_is_safe "$(printf '%s/a\nb/c' "$HOME")"
  [ "$status" -ne 0 ]
}

@test "a recorded root is stored resolved, not as given" {
  mkdir -p "$HOME/.memcap-real-$$/proj"
  ln -sfn "$HOME/.memcap-real-$$" "$HOME/.memcap-link-$$"
  mc_record_root "$HOME/.memcap-link-$$/proj"
  run mc_sweep_roots
  rm -rf "$HOME/.memcap-real-$$" "$HOME/.memcap-link-$$"
  [[ "$output" == *".memcap-real-$$/proj"* ]]
  [[ "$output" != *".memcap-link-$$"* ]]
}

@test "a path containing a carriage return is rejected" {
  run mc_root_is_safe "$HOME/code/proj"$'\r'"sneaky"
  [ "$status" -ne 0 ]
}

@test "a symlink to a directory whose name contains a newline is rejected" {
  real_dir="$HOME/.memcap-nl-$$/a"$'\n'"b"
  mkdir -p "$real_dir"
  ln -sfn "$real_dir" "$HOME/.memcap-nllink-$$"
  run mc_root_is_safe "$HOME/.memcap-nllink-$$/proj"
  rm -rf "$HOME/.memcap-nl-$$" "$HOME/.memcap-nllink-$$"
  [ "$status" -ne 0 ]
}

@test "an unsafe root is never recorded" {
  mc_record_root "$HOME"
  run mc_sweep_roots
  [ -z "$output" ]
}
