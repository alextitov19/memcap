#!/usr/bin/env bash
set -uo pipefail

MC_DOCKER_STORE="$HOME/Library/Group Containers/group.com.docker/settings-store.json"
MC_DRY_RUN="${MC_DRY_RUN:-0}"

mc_docker_runtime() {
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
  # Validate the argument BEFORE anything else. The dispatcher routes every `memcap
  # docker <anything>` here, so without this a typo -- `memcap docker aply` -- silently
  # performs the single riskiest action in the codebase: quitting and restarting Docker.
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
    echo "would set Docker VM to ${DOCKER_BUDGET_GB:-6} GB / ${DOCKER_CPUS:-8} cores"
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
      echo " up. VM ceiling ${DOCKER_BUDGET_GB:-6} GB / ${DOCKER_CPUS:-8} cores."
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
