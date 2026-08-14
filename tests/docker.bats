load helper
setup() {
  setup_common
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/docker.sh"
}

@test "runtime detection returns a known value" {
  run mc_docker_runtime
  [[ "$output" =~ ^(desktop|orbstack|colima|podman|none)$ ]]
}

@test "apply refuses without Docker Desktop" {
  run bash -c "mc_docker_runtime() { echo none; }; source '$MEMCAP_ROOT/libexec/docker.sh'; mc_docker_apply"
  [ "$status" -ne 0 ]
}

@test "apply is a no-op in dry run" {
  run env MC_DRY_RUN=1 bash -c "source '$MEMCAP_ROOT/libexec/common.sh'; source '$MEMCAP_ROOT/libexec/docker.sh'; mc_docker_apply"
  [[ "$output" == *would* ]]
}
