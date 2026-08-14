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

# A typo routed straight to mc_docker_apply must not fall through to the real runtime
# logic -- it is the single riskiest action in the codebase (quits and restarts Docker).
@test "mc_docker_apply rejects an unrecognized argument" {
  run mc_docker_apply bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage: memcap docker apply"* ]]
}

@test "mc_docker_apply still accepts --force" {
  run mc_docker_apply --force
  [[ "$output" == *would* ]]
}

@test "memcap docker aply (typo) is rejected with usage, before touching Docker" {
  run "$MEMCAP_ROOT/bin/memcap" docker aply
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage: memcap docker apply"* ]]
}

@test "memcap docker apply still works" {
  run "$MEMCAP_ROOT/bin/memcap" docker apply
  [ "$status" -eq 0 ]
  [[ "$output" == *would* ]]
}

@test "bare memcap docker still works" {
  run "$MEMCAP_ROOT/bin/memcap" docker
  [ "$status" -eq 0 ]
  [[ "$output" == *would* ]]
}

@test "memcap docker apply --force still parses" {
  run "$MEMCAP_ROOT/bin/memcap" docker apply --force
  [ "$status" -eq 0 ]
  [[ "$output" == *would* ]]
}

@test "the deferral message does not claim a write that never happens" {
  # Fakes `docker ps -q` so the containers-running branch is reached without any real
  # Docker call; the branch returns before mc_docker_apply ever shells out to `docker`
  # for real, `osascript`, or `jq`, so this is safe despite MC_DRY_RUN=0.
  run bash -c "source '$MEMCAP_ROOT/libexec/common.sh'; source '$MEMCAP_ROOT/libexec/docker.sh'; docker() { echo fakecontainerid; }; MC_DRY_RUN=0 mc_docker_apply"
  [ "$status" -eq 1 ]
  [[ "$output" == *"not restarting Docker"* ]]
  [[ "$output" != *"Settings saved"* ]]
}
