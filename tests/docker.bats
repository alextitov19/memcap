load helper
setup() {
  setup_common
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/docker.sh"
  # Belt: no test in this file may ever quit Docker or rewrite its settings, whatever
  # else goes wrong. mc_docker_apply restarts Docker Desktop for real, and a developer
  # running `bats tests/` must never lose their containers to a test run.
  export MC_DRY_RUN=1
}

@test "runtime detection returns a known value" {
  run mc_docker_runtime
  [[ "$output" =~ ^(desktop|orbstack|colima|podman|none)$ ]]
}

# Braces: the stub must be defined AFTER sourcing, or `source docker.sh` overwrites it
# and this runs against the real runtime -- which on a Docker Desktop machine with no
# running containers would fall through to the actual quit-and-rewrite path.
@test "apply refuses without Docker Desktop" {
  run bash -c "source '$MEMCAP_ROOT/libexec/common.sh'; source '$MEMCAP_ROOT/libexec/docker.sh'; mc_docker_runtime() { echo none; }; MC_DRY_RUN=1 mc_docker_apply"
  [ "$status" -ne 0 ]
  [[ "$output" == *"cannot set a VM ceiling"* ]]
}

@test "apply is a no-op in dry run" {
  run env MC_DRY_RUN=1 bash -c "source '$MEMCAP_ROOT/libexec/common.sh'; source '$MEMCAP_ROOT/libexec/docker.sh'; mc_docker_apply"
  [[ "$output" == *would* ]]
}
