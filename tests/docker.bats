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

# --- EPERM: the daemon cannot read Docker's settings store -------------------
# v0.5.1 shipped the drift line above and it logged ZERO times in 8 days and
# ~10,000 passes of the real service, while `memcap status` from a terminal
# showed the drift every single time. macOS denies launchd agents access to
# ~/Library/Group Containers: reproduced with a transient launchd job running
# /bin/sh under the LaunchAgent's own PATH, `test -f` said OK, `read` said FAIL,
# and jq said "Operation not permitted". mc_docker_ceiling_gb collapsed that into
# the same silent `return 1` it uses for "there is no Docker Desktop here", so
# the daemon ran blind and looked healthy doing it.
#
# chmod 000 is the reachable stand-in for EPERM: the suite runs as a non-root
# user, so `[ -f ]` is true and every read fails -- the same shape as the
# sandbox denial, which no test can arrange for real.
#
# The three outcomes are asserted separately below because collapsing any two of
# them is precisely the bug.

# Neither `run` NOR a command substitution: both put the call in a subshell, and
# every assertion below is about a global the function sets there. Written this
# way after the first draft used `MC_CEILING_OUT=$(mc_docker_ceiling_gb)` and
# three tests passed while asserting on docker.sh's top-level defaults rather
# than on anything the function had done -- the same subshell-swallows-the-
# diagnosis mistake that hid the EPERM case in production. stdout goes to a file
# so the printed value can still be checked.
ceiling_read() {
  MC_CEILING_RC=0
  mc_docker_ceiling_gb > "$BATS_TEST_TMPDIR/ceiling.out" || MC_CEILING_RC=$?
  MC_CEILING_OUT=$(cat "$BATS_TEST_TMPDIR/ceiling.out")
}

@test "EPERM: an unreadable store is 2 with a reason, not a silent 1" {
  if [ "$(id -u)" -eq 0 ]; then skip "chmod 000 is not a barrier to root"; fi
  MC_DOCKER_STORE="$BATS_TEST_TMPDIR/locked.json"
  printf '{"MemoryMiB": 6144}\n' > "$MC_DOCKER_STORE"
  chmod 000 "$MC_DOCKER_STORE"

  ceiling_read
  # 2, not 1: 1 means "no Docker Desktop on this machine, nothing to say", and
  # saying nothing is what left the service blind for 8 days.
  [ "$MC_CEILING_RC" -eq 2 ]
  # Prints nothing at all -- a caller must never receive a number it could
  # compare against the config.
  [ -z "$MC_CEILING_OUT" ]
  assert_contains "$MC_DOCKER_CEILING_ERR" "cannot read"
  assert_contains "$MC_DOCKER_CEILING_ERR" "Group Containers"
  [ "$MC_DOCKER_CEILING_UNREADABLE" = "1" ]
}

@test "EPERM: an absent store is still a silent 1, not the unreadable case" {
  # The negative control for the test above, and the CI case: GitHub's macOS
  # runners have no Docker Desktop at all. A machine that does not run Docker
  # must produce no diagnosis, no log line and no cache lookup.
  MC_DOCKER_STORE="$BATS_TEST_TMPDIR/does-not-exist.json"
  ceiling_read
  [ "$MC_CEILING_RC" -eq 1 ]
  [ -z "$MC_CEILING_OUT" ]
  [ "$MC_DOCKER_CEILING_UNREADABLE" = "0" ]
  [ -z "$MC_DOCKER_CEILING_ERR" ]
}

@test "EPERM: an empty but readable store is unknown, not unreadable" {
  # An empty file opens fine and simply records no ceiling. Distinguished here
  # because the obvious readability probe -- the shell's own `read` -- fails at
  # end-of-file, which would file a perfectly readable file as a permission
  # problem and put a launchd-denial line in actions.log about it.
  MC_DOCKER_STORE="$BATS_TEST_TMPDIR/empty.json"
  : > "$MC_DOCKER_STORE"
  ceiling_read
  [ "$MC_CEILING_RC" -eq 1 ]
  [ "$MC_DOCKER_CEILING_UNREADABLE" = "0" ]
}

@test "EPERM: a successful read caches the value, as two integers" {
  MC_DOCKER_STORE="$BATS_TEST_TMPDIR/settings.json"
  printf '{"MemoryMiB": 6144, "Cpus": 8}\n' > "$MC_DOCKER_STORE"
  ceiling_read
  [ "$MC_CEILING_RC" -eq 0 ]
  [ "$MC_CEILING_OUT" = "6" ]

  # This file is the entire mechanism by which the background service ever
  # learns the ceiling: it cannot read Docker's own settings, so it reads what a
  # terminal last saw. In the sandboxed state dir -- MEMCAP_STATE_HOME, set by
  # setup_common -- never the real one.
  cache="$MEMCAP_STATE_HOME/memcap/docker-ceiling"
  [ -f "$cache" ]
  run cat "$cache"
  assert_matches "$output" '^[0-9]+ [0-9]+$'
  assert_matches "$output" '^6144 '
}

@test "EPERM: the escape hatch does not seed the cache" {
  # MC_DOCKER_CEILING_MIB is a test fixture, not a reading of anything. If it
  # wrote the cache, a suite run would leave a number behind that a later pass
  # would report to the user as what Docker is enforcing.
  #
  # The store is pointed at a path that does not exist even though the override
  # means it is never opened: left at its default it is a path under $HOME, and
  # a test whose behaviour could turn on whether the developer runs Docker is
  # exactly what the no-Docker suite run exists to catch.
  MC_DOCKER_STORE="$BATS_TEST_TMPDIR/does-not-exist.json"
  # shellcheck disable=SC2034  # read by mc_docker_ceiling_gb, sourced from docker.sh
  MC_DOCKER_CEILING_MIB=6144
  ceiling_read
  [ "$MC_CEILING_RC" -eq 0 ]
  [ ! -f "$MEMCAP_STATE_HOME/memcap/docker-ceiling" ]
}

@test "EPERM: an unreadable store falls back to the cached reading" {
  if [ "$(id -u)" -eq 0 ]; then skip "chmod 000 is not a barrier to root"; fi
  MC_DOCKER_STORE="$BATS_TEST_TMPDIR/locked.json"
  printf '{"MemoryMiB": 6144}\n' > "$MC_DOCKER_STORE"
  chmod 000 "$MC_DOCKER_STORE"
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  printf '6144 %s\n' "$(date +%s)" > "$MEMCAP_STATE_HOME/memcap/docker-ceiling"

  ceiling_read
  [ "$MC_CEILING_RC" -eq 0 ]
  [ "$MC_CEILING_OUT" = "6" ]
  assert_contains "$MC_DOCKER_CEILING_SOURCE" "cached"
  assert_contains "$MC_DOCKER_CEILING_SOURCE" "from a terminal"
  # Still not a live read, so the daemon must not be told the check is fine --
  # but it is also not blind, so it must not log that it is.
  [ "$MC_DOCKER_CEILING_UNREADABLE" = "0" ]
}

@test "EPERM: the cached reading dates itself in the drift line" {
  if [ "$(id -u)" -eq 0 ]; then skip "chmod 000 is not a barrier to root"; fi
  MC_DOCKER_STORE="$BATS_TEST_TMPDIR/locked.json"
  printf '{"MemoryMiB": 6144}\n' > "$MC_DOCKER_STORE"
  chmod 000 "$MC_DOCKER_STORE"
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  # Two hours old, so the age is a real interval rather than "0s" -- a stale
  # cache reported as though it were current is the failure this note exists to
  # prevent.
  printf '6144 %s\n' "$(( $(date +%s) - 7200 ))" > "$MEMCAP_STATE_HOME/memcap/docker-ceiling"

  run mc_docker_ceiling_drift 4
  [ "$status" -eq 0 ]
  assert_contains "$output" "enforcing a 6 GB VM ceiling, not the 4 GB in your config"
  assert_contains "$output" "unreadable from the background service"
  assert_contains "$output" "last read from a terminal, 2h ago"
  assert_contains "$output" "memcap status"
}

@test "EPERM: a live read does not carry the cache note" {
  # The negative control for the test above: the note must appear only when the
  # number is remembered. A drift line that always claimed to be quoting a cache
  # would be as wrong as one that never did.
  MC_DOCKER_STORE="$BATS_TEST_TMPDIR/settings.json"
  printf '{"MemoryMiB": 6144}\n' > "$MC_DOCKER_STORE"
  run mc_docker_ceiling_drift 4
  [ "$status" -eq 0 ]
  assert_contains "$output" "enforcing a 6 GB VM ceiling"
  assert_not_contains "$output" "unreadable from the background service"
}

@test "EPERM: a malformed cache is ignored rather than half-read" {
  if [ "$(id -u)" -eq 0 ]; then skip "chmod 000 is not a barrier to root"; fi
  MC_DOCKER_STORE="$BATS_TEST_TMPDIR/locked.json"
  printf '{"MemoryMiB": 6144}\n' > "$MC_DOCKER_STORE"
  chmod 000 "$MC_DOCKER_STORE"
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  cache="$MEMCAP_STATE_HOME/memcap/docker-ceiling"

  # Every shape a torn or hand-edited file can take. Each must land back on
  # "unreadable, and nothing remembered" -- a garbage value here is reported to
  # the user as the ceiling Docker is enforcing, with a remedy attached.
  for bad in '6144' 'lots 1756900000' '6144 yesterday' '' '6144 1756900000 extra' '0 1756900000'; do
    printf '%s\n' "$bad" > "$cache"
    ceiling_read
    [ "$MC_CEILING_RC" -eq 2 ]
    [ -z "$MC_CEILING_OUT" ]
    [ "$MC_DOCKER_CEILING_UNREADABLE" = "1" ]
  done
}

@test "EPERM: a torn half-written cache line is never read" {
  # The write is temp-plus-mv so this cannot happen through memcap itself, which
  # is the point: the reader is strict enough that it would not matter if it did.
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  printf '61' > "$MEMCAP_STATE_HOME/memcap/docker-ceiling"
  run mc_docker_ceiling_cache_read
  # `-eq 1`, not `-ne 0`: with `-ne 0` this test passed against the version that
  # has no such function at all, on bats' 127 for "command not found". A test
  # that a missing implementation satisfies is the "passing test confirms the
  # bug" trap in AGENTS.md, found here by running the whole file against a
  # stashed working tree.
  [ "$status" -eq 1 ]
}

@test "EPERM: the cache write is atomic and leaves no temp file behind" {
  run mc_docker_ceiling_cache_write 4096
  [ "$status" -eq 0 ]
  run cat "$MEMCAP_STATE_HOME/memcap/docker-ceiling"
  assert_matches "$output" '^4096 [0-9]+$'
  run ls "$MEMCAP_STATE_HOME/memcap/"
  assert_not_contains "$output" "docker-ceiling."
}

@test "EPERM: docker apply refreshes the cache from the value it just wrote" {
  # `docker apply` runs from a terminal and is the only other place that knows
  # the enforced ceiling for certain. Without this, the service would keep
  # reporting the PREVIOUS ceiling -- and keep advising `memcap docker apply` --
  # until someone happened to run `status`.
  #
  # Nothing here reaches real Docker: pgrep says Docker Desktop is not running
  # (so no osascript quit), `docker` and `open` are stubbed, and `sleep` is
  # stubbed so the engine-wait loop costs nothing. The store is a temp file. The
  # function still returns 1 at the end, because the fake engine never comes up
  # -- the cache write is what is under test, and it must survive that.
  store="$BATS_TEST_TMPDIR/apply-store.json"
  printf '{"MemoryMiB":2048,"Cpus":4}\n' > "$store"

  run env MC_DOCKER_RUNTIME=desktop DOCKER_BUDGET_GB=5 DOCKER_CPUS=4 \
    MEMCAP_STATE_HOME="$MEMCAP_STATE_HOME" MC_DOCKER_STORE="$store" bash -c "
      source '$MEMCAP_ROOT/libexec/common.sh'
      source '$MEMCAP_ROOT/libexec/docker.sh'
      pgrep() { return 1; }
      docker() { return 1; }
      open() { echo OPEN_STUBBED; }
      sleep() { return 0; }
      MC_DRY_RUN=0
      mc_docker_apply
    "
  assert_contains "$output" "OPEN_STUBBED"
  run cat "$MEMCAP_STATE_HOME/memcap/docker-ceiling"
  # 5 GB, as MiB: the number written into Docker's settings, not the one that
  # was in them beforehand.
  assert_matches "$output" '^5120 [0-9]+$'
}

