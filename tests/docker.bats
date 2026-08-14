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

# --- Final review, I4: docker apply silently rewrites three undocumented settings
# SwapMiB, ResourceSaverEnabled, and AutoPauseTimeoutSeconds are set alongside
# memory and CPU, but only memory/CPU were ever mentioned to the user. Keep the
# behavior, but say so in the command's own output as well as the README.
@test "the dry-run message names all five settings it would change" {
  run env MC_DRY_RUN=1 bash -c "source '$MEMCAP_ROOT/libexec/common.sh'; source '$MEMCAP_ROOT/libexec/docker.sh'; mc_docker_apply"
  # One combined [[ ]] rather than five separate ones: under bash 3.2 a non-final
  # `[[ ]]` that evaluates false does not abort the test (see classify.bats's
  # EXTRA_AGENTS test for the full explanation), so only the last of five separate
  # checks would actually be enforced.
  [[ "$output" == *"GB"* && "$output" == *"cores"* && "$output" == *"swap"* \
     && "$output" == *"Resource Saver"* && "$output" == *"auto-pause"* ]]
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

# --- Final review, small fix: reject trailing arguments -----------------------
# `memcap docker apply --force rm-everything` only ever looked at $1, so the case
# statement matched "--force" and silently discarded "rm-everything" -- running the
# force path (quits and restarts Docker) with the tail unexamined. uninstall already
# has this discipline (bin/memcap:60); apply is the riskier of the two commands and
# did not.
@test "mc_docker_apply rejects a trailing argument after --force" {
  run mc_docker_apply --force rm-everything
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage: memcap docker apply"* ]]
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

# --- Final review, I3: a failed settings write must not report success -------
# docker.sh never got the false-success pass T9 spent three rounds on in
# profile.sh: the `cp` was unchecked and `jq ... && mv` had no else branch. If jq is
# missing or errors, Docker has already been quit by the time this runs and would be
# restarted anyway with success printed -- a full Docker restart for nothing,
# reported as done, with a leaked temp file. `mc_docker_runtime`, `pgrep`, and
# `docker` are all stubbed so this never comes near a real Docker process or the
# real settings-store.json; `open` is stubbed too so a failure to notice the write
# error would show up as this test seeing "OPEN CALLED" it should never reach.
@test "a failed settings write reports failure and never opens Docker" {
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  # Standing in for "jq missing or erroring" -- the write path must fail closed
  # either way, not just when the command is entirely absent.
  cat > "$fakebin/jq" <<'SCRIPT'
#!/usr/bin/env bash
exit 1
SCRIPT
  chmod +x "$fakebin/jq"

  store="$BATS_TEST_TMPDIR/settings-store.json"
  printf '{"MemoryMiB":2048,"Cpus":4}' > "$store"

  run env PATH="$fakebin:$PATH" bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/docker.sh'
    mc_docker_runtime() { echo desktop; }
    pgrep() { return 1; }
    docker() { return 0; }
    open() { echo OPEN_CALLED; }
    MC_DOCKER_STORE='$store'
    MC_DRY_RUN=0
    mc_docker_apply
  "
  [ "$status" -eq 1 ]
  # Combined into one [[ ]] so bash 3.2's non-final-[[ ]] quirk (see classify.bats)
  # cannot let an earlier check silently pass without being enforced.
  [[ "$output" == *"failed to write"* && "$output" == *jq* && "$output" != *OPEN_CALLED* ]]
  [ "$(cat "$store")" = '{"MemoryMiB":2048,"Cpus":4}' ]
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
