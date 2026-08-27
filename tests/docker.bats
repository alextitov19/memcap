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
  assert_matches "$output" '^(desktop|orbstack|colima|podman|none)$'
}

# --- Final review follow-up: CI has no Docker Desktop -------------------------
# GitHub's macOS runners have no Docker Desktop, no orbctl/colima/podman -- real
# mc_docker_runtime returns "none" there, and every test below that expects the
# apply path (dry-run "would set..." etc.) would fail on a clean checkout with
# nothing further installed. MC_DOCKER_RUNTIME is the fix: an escape-hatch env
# var, the same pattern as MC_NO_TOP, checked before mc_docker_runtime's real
# detection. A function-level stub (`mc_docker_runtime() { echo desktop; }`,
# defined after sourcing docker.sh) works for the tests that source docker.sh
# directly, but NOT for the ones below that invoke the real `bin/memcap`
# binary as a subprocess -- bin/memcap's own `. "$LIB/docker.sh"` redefines the
# function again, clobbering any override a parent shell set beforehand. A
# plain environment variable has no such problem: it survives into any
# subprocess normally, so it is used uniformly below regardless of which form
# the test invokes.
@test "apply refuses without Docker Desktop" {
  run env MC_DOCKER_RUNTIME=none MC_DRY_RUN=1 bash -c "source '$MEMCAP_ROOT/libexec/common.sh'; source '$MEMCAP_ROOT/libexec/docker.sh'; mc_docker_apply"
  [ "$status" -ne 0 ]
  assert_contains "$output" "cannot set a VM ceiling"
}

@test "apply is a no-op in dry run" {
  run env MC_DOCKER_RUNTIME=desktop MC_DRY_RUN=1 bash -c "source '$MEMCAP_ROOT/libexec/common.sh'; source '$MEMCAP_ROOT/libexec/docker.sh'; mc_docker_apply"
  assert_contains "$output" "would"
}

# --- Final review, I4: docker apply silently rewrites three undocumented settings
# SwapMiB, ResourceSaverEnabled, and AutoPauseTimeoutSeconds are set alongside
# memory and CPU, but only memory/CPU were ever mentioned to the user. Keep the
# behavior, but say so in the command's own output as well as the README.
@test "the dry-run message names all five settings it would change" {
  run env MC_DOCKER_RUNTIME=desktop MC_DRY_RUN=1 bash -c "source '$MEMCAP_ROOT/libexec/common.sh'; source '$MEMCAP_ROOT/libexec/docker.sh'; mc_docker_apply"
  assert_contains "$output" "GB"
  assert_contains "$output" "cores"
  assert_contains "$output" "swap"
  assert_contains "$output" "Resource Saver"
  assert_contains "$output" "auto-pause"
}

# A typo routed straight to mc_docker_apply must not fall through to the real runtime
# logic -- it is the single riskiest action in the codebase (quits and restarts Docker).
@test "mc_docker_apply rejects an unrecognized argument" {
  run mc_docker_apply bogus
  [ "$status" -eq 2 ]
  assert_contains "$output" "usage: memcap docker apply"
}

@test "mc_docker_apply still accepts --force" {
  # shellcheck disable=SC2034  # consumed by mc_docker_runtime, sourced from docker.sh
  MC_DOCKER_RUNTIME=desktop
  run mc_docker_apply --force
  assert_contains "$output" "would"
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
  assert_contains "$output" "usage: memcap docker apply"
}

@test "memcap docker aply (typo) is rejected with usage, before touching Docker" {
  run "$MEMCAP_ROOT/bin/memcap" docker aply
  [ "$status" -eq 2 ]
  assert_contains "$output" "usage: memcap docker apply"
}

@test "memcap docker apply still works" {
  run env MC_DOCKER_RUNTIME=desktop "$MEMCAP_ROOT/bin/memcap" docker apply
  [ "$status" -eq 0 ]
  assert_contains "$output" "would"
}

@test "bare memcap docker still works" {
  run env MC_DOCKER_RUNTIME=desktop "$MEMCAP_ROOT/bin/memcap" docker
  [ "$status" -eq 0 ]
  assert_contains "$output" "would"
}

@test "memcap docker apply --force still parses" {
  run env MC_DOCKER_RUNTIME=desktop "$MEMCAP_ROOT/bin/memcap" docker apply --force
  [ "$status" -eq 0 ]
  assert_contains "$output" "would"
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

  run env PATH="$fakebin:$PATH" MC_DOCKER_RUNTIME=desktop bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/docker.sh'
    pgrep() { return 1; }
    docker() { return 0; }
    open() { echo OPEN_CALLED; }
    MC_DOCKER_STORE='$store'
    MC_DRY_RUN=0
    mc_docker_apply
  "
  [ "$status" -eq 1 ]
  assert_contains "$output" "failed to write"
  assert_contains "$output" "jq"
  assert_not_contains "$output" "OPEN_CALLED"
  [ "$(cat "$store")" = '{"MemoryMiB":2048,"Cpus":4}' ]
}

@test "the deferral message does not claim a write that never happens" {
  # Fakes `docker ps -q` so the containers-running branch is reached without any real
  # Docker call; the branch returns before mc_docker_apply ever shells out to `docker`
  # for real, `osascript`, or `jq`, so this is safe despite MC_DRY_RUN=0.
  run env MC_DOCKER_RUNTIME=desktop bash -c "source '$MEMCAP_ROOT/libexec/common.sh'; source '$MEMCAP_ROOT/libexec/docker.sh'; docker() { echo fakecontainerid; }; MC_DRY_RUN=0 mc_docker_apply"
  [ "$status" -eq 1 ]
  assert_contains "$output" "not restarting Docker"
  # Final review: the old assertion checked for "Settings saved", a string that
  # exists nowhere in the codebase -- vacuous, it could never have failed. The
  # deferral branch returns before mc_docker_apply ever reaches "Waiting for the
  # Docker engine" (printed right after the real `open -a Docker` call), so that
  # absence is what actually proves the write/restart path was never reached.
  assert_not_contains "$output" "Waiting for the Docker engine"
}

# --- The ceiling memcap writes but never read back ---------------------------
# `memcap docker apply` has written MemoryMiB since v0.1.0 and nothing has ever
# read it. On the author's own machine the config said DOCKER_BUDGET_GB=4 while
# Docker was enforcing 6144 MiB, because apply had never actually been run there
# -- so every agent budget memcap computed subtracted a ceiling nothing honored,
# and status printed "6.39 GB / 4 GB ceiling", which reads as Docker overrunning
# a limit rather than as there being no limit at all.

@test "CEILING: the enforced ceiling is read out of Docker's own settings" {
  MC_DOCKER_STORE="$BATS_TEST_TMPDIR/settings.json"
  printf '{"MemoryMiB": 6144, "Cpus": 8}\n' > "$MC_DOCKER_STORE"
  run mc_docker_ceiling_gb
  [ "$status" -eq 0 ]
  [ "$output" = "6" ]
}

@test "CEILING: an unreadable or absent store is unknown, not a mismatch" {
  # Never warn on missing information: a machine with no Docker Desktop, or a
  # settings file memcap cannot parse, must produce silence rather than a claim
  # that the user's config is wrong.
  MC_DOCKER_STORE="$BATS_TEST_TMPDIR/does-not-exist.json"
  run mc_docker_ceiling_gb
  [ "$status" -ne 0 ]
  [ -z "$output" ]

  MC_DOCKER_STORE="$BATS_TEST_TMPDIR/junk.json"
  printf '{"Cpus": 8}\n' > "$MC_DOCKER_STORE"
  run mc_docker_ceiling_gb
  [ "$status" -ne 0 ]

  printf '{"MemoryMiB": "lots"}\n' > "$MC_DOCKER_STORE"
  run mc_docker_ceiling_gb
  [ "$status" -ne 0 ]
}

@test "CEILING: the store is read without jq too" {
  # jq is a formula dependency, so this is a fallback rather than the main path
  # -- but a status command that dies because jq is missing would be worse than
  # one that reports no ceiling.
  MC_DOCKER_STORE="$BATS_TEST_TMPDIR/settings.json"
  printf '{"MemoryMiB": 4096, "Cpus": 8}\n' > "$MC_DOCKER_STORE"
  fakebin="$BATS_TEST_TMPDIR/nojq"
  mkdir -p "$fakebin"
  for c in sed head cat; do ln -sf "$(command -v $c)" "$fakebin/$c"; done
  # /bin/bash by absolute path: PATH holds only the fake bin, so the shell itself
  # would not be findable by name.
  run env PATH="$fakebin" MC_DOCKER_STORE="$MC_DOCKER_STORE" /bin/bash -c \
    "source '$MEMCAP_ROOT/libexec/common.sh'; source '$MEMCAP_ROOT/libexec/docker.sh'; mc_docker_ceiling_gb"
  [ "$output" = "4" ]
}

@test "CEILING: a drift between config and Docker is reported with its remedy" {
  # shellcheck disable=SC2034  # read by mc_docker_ceiling_gb, sourced from docker.sh
  MC_DOCKER_CEILING_MIB=6144
  run mc_docker_ceiling_drift 4
  [ "$status" -eq 0 ]
  assert_contains "$output" "enforcing a 6 GB VM ceiling, not the 4 GB in your config"
  assert_contains "$output" "memcap docker apply"
}

@test "CEILING: agreement is silent" {
  # shellcheck disable=SC2034  # read by mc_docker_ceiling_gb, sourced from docker.sh
  MC_DOCKER_CEILING_MIB=6144
  run mc_docker_ceiling_drift 6
  [ "$status" -ne 0 ]
  [ -z "$output" ]
}

@test "CEILING: an unmanaged Docker (0 GB) is not a drift" {
  # shellcheck disable=SC2034  # read by mc_docker_ceiling_gb, sourced from docker.sh
  # DOCKER_BUDGET_GB=0 means "memcap is not managing Docker", so whatever Docker
  # is enforcing on its own is not a disagreement with anything.
  MC_DOCKER_CEILING_MIB=6144
  run mc_docker_ceiling_drift 0
  [ "$status" -ne 0 ]
  [ -z "$output" ]
}

