#!/usr/bin/env bash
set -uo pipefail

# `:-` like MC_DRY_RUN directly below, and unlike the unconditional assignment
# this used to be: bin/memcap re-sources this file for every real invocation, so
# a plain assignment silently discarded any MC_DOCKER_STORE set in the
# environment. That is the same override-clobbering AGENTS.md records for
# function stubs, and it meant the store path was reachable only by tests that
# set the variable AFTER sourcing -- never through `bin/memcap` at all.
MC_DOCKER_STORE="${MC_DOCKER_STORE:-$HOME/Library/Group Containers/group.com.docker/settings-store.json}"
MC_DRY_RUN="${MC_DRY_RUN:-0}"

# The ceiling Docker is ACTUALLY enforcing, in whole GB, read back out of the
# same key mc_docker_apply writes. Prints nothing and returns non-zero when the
# answer is unknowable (no Docker Desktop, unreadable store, no jq) -- an unknown
# ceiling must never be reported as a mismatched one.
#
# Written since v0.1.0, read by nobody until now, and the gap was not theoretical:
# on the author's machine DOCKER_BUDGET_GB was 4 while Docker's own MemoryMiB was
# 6144, because `memcap docker apply` had never been run there (no
# settings-store.json.memcap.bak existed). Every budget memcap computed on that
# machine subtracted a ceiling nothing was enforcing, and `status` printed
# "6.39 GB / 4 GB ceiling" -- which reads as Docker overrunning a limit, rather
# than as there being no limit at all.
mc_docker_ceiling_gb() {
  local mib
  if [ -n "${MC_DOCKER_CEILING_MIB:-}" ]; then
    # Escape hatch, the MC_DOCKER_RUNTIME pattern again: what Docker Desktop has
    # in its settings file is a property of the host, not something a test can
    # arrange.
    mib="$MC_DOCKER_CEILING_MIB"
  else
    [ -f "$MC_DOCKER_STORE" ] || return 1
    if command -v jq >/dev/null 2>&1; then
      mib=$(jq -r '.MemoryMiB // empty' "$MC_DOCKER_STORE" 2>/dev/null)
    else
      # Deliberately not a JSON parser: this is a fallback for a machine without
      # the formula's own dependency, and it either finds a plain integer or
      # gives up. A wrong number here would produce a false mismatch warning,
      # which is worse than no warning.
      mib=$(sed -n 's/.*"MemoryMiB"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$MC_DOCKER_STORE" 2>/dev/null | head -1)
    fi
  fi
  case "$mib" in ''|*[!0-9]*) return 1 ;; esac
  [ "$mib" -gt 0 ] || return 1
  printf '%s' $((mib / 1024))
  return 0
}

# The one-line explanation of a drift, or nothing at all when the two agree or
# the enforced ceiling cannot be read. Shared so `status` and `watch` cannot
# describe the same condition two different ways.
mc_docker_ceiling_drift() {
  local configured="${1:-0}" actual
  case "$configured" in ''|*[!0-9]*) return 1 ;; esac
  [ "$configured" -gt 0 ] || return 1
  actual=$(mc_docker_ceiling_gb) || return 1
  [ "$actual" = "$configured" ] && return 1
  printf "Docker is enforcing a %s GB VM ceiling, not the %s GB in your config -- the agent budget is computed from a number Docker is not honoring. Fix with: memcap docker apply" \
    "$actual" "$configured"
  return 0
}

mc_docker_runtime() {
  # Escape hatch, same pattern as MC_NO_TOP: this check depends entirely on what
  # is installed on the host, with no way to make it deterministic in an
  # environment (CI, a machine with no Docker runtime at all) that doesn't have
  # any of these actually present. A function-level test stub can't reach this
  # once bin/memcap's own `. "$LIB/docker.sh"` has run for a real invocation --
  # sourcing redefines the function again, clobbering an override set beforehand
  # -- but a plain environment variable survives into any subprocess normally.
  if [ -n "${MC_DOCKER_RUNTIME:-}" ]; then
    printf '%s' "$MC_DOCKER_RUNTIME"
    return 0
  fi
  if [ -f "$MC_DOCKER_STORE" ] && [ -d "/Applications/Docker.app" ]; then echo desktop; return; fi
  command -v orbctl  >/dev/null 2>&1 && { echo orbstack; return; }
  command -v colima  >/dev/null 2>&1 && { echo colima;   return; }
  command -v podman  >/dev/null 2>&1 && { echo podman;   return; }
  echo none
}

# Only Docker Desktop exposes a settable VM ceiling. Other runtimes are counted
# toward the budget but cannot be capped; say so once rather than nagging.
mc_docker_apply() {
  local force=0 rt mem_mib running
  # Validate the arguments BEFORE anything else. The dispatcher routes every `memcap
  # docker <anything>` here, so without this a typo -- `memcap docker aply` -- silently
  # performs the single riskiest action in the codebase: quitting and restarting Docker.
  # A trailing argument after --force must be rejected too, the same discipline
  # `memcap uninstall` already has: `memcap docker apply --force rm-everything` would
  # otherwise match "--force" on $1 alone and silently run the force path anyway.
  if [ $# -gt 1 ]; then
    echo "usage: memcap docker apply [--force]" >&2; return 2
  fi
  case "${1:-}" in
    "")        : ;;
    --force)   force=1 ;;
    *)         echo "usage: memcap docker apply [--force]" >&2; return 2 ;;
  esac
  rt=$(mc_docker_runtime)
  if [ "$rt" != "desktop" ]; then
    echo "Docker runtime is '$rt' — memcap can measure it but cannot set a VM ceiling." >&2
    return 1
  fi
  mem_mib=$(( ${DOCKER_BUDGET_GB:-6} * 1024 ))
  if [ "$MC_DRY_RUN" = "1" ]; then
    echo "would set Docker VM to ${DOCKER_BUDGET_GB:-6} GB / ${DOCKER_CPUS:-8} cores" \
         "(also: 2 GB swap, Resource Saver on, auto-pause after 30s idle)"
    return 0
  fi
  running=$(docker ps -q 2>/dev/null)
  if [ -n "$running" ] && [ "$force" = "0" ]; then
    # Deliberately does NOT write the setting here. Docker Desktop rewrites its own
    # settings file when it quits, so anything written while it is running is clobbered
    # on the very restart that would apply it. The ceiling is written at apply time.
    echo "Containers are running; not restarting Docker. Run 'memcap docker apply' when convenient." >&2
    return 1
  fi

  if pgrep -q "Docker Desktop" 2>/dev/null; then
    osascript -e 'quit app "Docker Desktop"' 2>/dev/null || true
    local i=0
    while pgrep -q "Docker Desktop" 2>/dev/null && [ $i -lt 30 ]; do sleep 1; i=$((i+1)); done
  fi

  # Both writes checked: an unchecked `cp` or a `jq ... && mv` with no else branch
  # reports success when nothing was written. By this point Docker has already been
  # quit, so failing silently here would mean a full restart for nothing -- restarted
  # anyway, with success printed, on the caller's original settings.
  if ! cp "$MC_DOCKER_STORE" "$MC_DOCKER_STORE.memcap.bak"; then
    echo "failed to back up $MC_DOCKER_STORE -- not touching it" >&2
    return 1
  fi
  local tmp; tmp=$(mktemp)
  if ! jq --argjson mem "$mem_mib" --argjson cpus "${DOCKER_CPUS:-8}" '
    .MemoryMiB = $mem | .Cpus = $cpus | .SwapMiB = 2048
    | .ResourceSaverEnabled = true | .AutoPauseTimeoutSeconds = 30' \
    "$MC_DOCKER_STORE" > "$tmp" || ! mv "$tmp" "$MC_DOCKER_STORE"; then
    rm -f "$tmp"
    echo "failed to write $MC_DOCKER_STORE (is jq installed?)" >&2
    return 1
  fi

  open -a Docker
  printf 'Waiting for the Docker engine'
  local n=0
  while [ $n -lt 60 ]; do
    if [ -S "$HOME/.docker/run/docker.sock" ] && docker info >/dev/null 2>&1; then
      echo " up. VM ceiling ${DOCKER_BUDGET_GB:-6} GB / ${DOCKER_CPUS:-8} cores" \
           "(also: 2 GB swap, Resource Saver on, auto-pause after 30s idle)."
      return 0
    fi
    printf '.'; sleep 5; n=$((n+1))
  done
  cat <<'MSG'

 the engine has not come up yet.
A large image store can take minutes to load, and while it does, `docker images` and
`docker ps -a` return EMPTY. That is NOT data loss -- the VM disk image is never
rewritten by memcap. Wait, or open Docker Desktop: after a restart it can sit on a
sign-in screen and will not start the engine until dismissed.
MSG
  return 1
}
