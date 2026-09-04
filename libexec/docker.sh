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

# --- The ceiling Docker is ACTUALLY enforcing ---------------------------------
#
# In whole GB, read back out of the same key mc_docker_apply writes. Written
# since v0.1.0, read by nobody until v0.5.1, and the gap was not theoretical: on
# the author's machine DOCKER_BUDGET_GB was 4 while Docker's own MemoryMiB was
# 6144, because `memcap docker apply` had never been run there. Every budget
# memcap computed on that machine subtracted a ceiling nothing was enforcing,
# and `status` printed "6.39 GB / 4 GB ceiling" -- which reads as Docker
# overrunning a limit, rather than as there being no limit at all.
#
# THREE outcomes, not two, and collapsing the last two into one was v0.5.1's bug:
#
#   store ABSENT     -> return 1, silently. No Docker Desktop, nothing to say.
#   store READABLE   -> print whole GB, return 0, and seed the cache below.
#   store UNREADABLE -> the cached value if there is one (return 0), otherwise
#                       return 2 with MC_DOCKER_CEILING_ERR set.
#
# v0.5.1 treated "unreadable" exactly like "absent" and returned 1. That reads
# as conservative and was not: macOS denies launchd agents access to
# ~/Library/Group Containers, so the background service got EPERM on this file
# on every single pass -- reproduced with a transient launchd job under the
# daemon's own PATH: `test -f` OK, `read` FAIL, `jq` "Operation not permitted"
# -- while `memcap status` from a terminal read the same file fine. The drift
# line v0.5.1 shipped precisely to warn about an unapplied ceiling therefore
# logged ZERO times in 8 days and ~10,000 passes, while `status` showed the
# drift every time it was run. The one place the warning mattered -- the
# service, computing every agent budget by subtracting a ceiling Docker was not
# honoring -- was the one place structurally unable to see it. A silent return 1
# on a permission error is indistinguishable from "this machine has no Docker".
#
# The outputs below exist so a caller can act on WHICH of the three it got, and
# they are globals rather than stdout for a specific reason: both callers used to
# capture this through a command substitution, and a subshell discards exactly
# the diagnosis that separates "no Docker here" from "the file is there and
# launchd cannot open it".
# shellcheck disable=SC2034  # read by enforce.sh (mc_watch), status.sh and the tests
MC_DOCKER_CEILING_GB=""
# shellcheck disable=SC2034
MC_DOCKER_CEILING_SOURCE=""
# shellcheck disable=SC2034
MC_DOCKER_CEILING_ERR=""
# shellcheck disable=SC2034
MC_DOCKER_CEILING_AGE=""
# shellcheck disable=SC2034
MC_DOCKER_CEILING_UNREADABLE=0
# shellcheck disable=SC2034
MC_DOCKER_CEILING_DRIFT=""

# Where the last value a terminal managed to read is kept, so `watch` is not
# blind merely because launchd denied it a file. Written by whichever command
# CAN read the store -- `status` and `docker apply` both run from a terminal --
# and read back by the background service, which cannot.
#
# This caches a fact about the host, not state memcap owns, so it is never
# presented as a live reading: a ceiling read three weeks ago is still worth
# acting on, but the user is told which number they are looking at and how old
# it is. In the state directory deliberately, so `memcap uninstall` removes it
# and a sandboxed MEMCAP_STATE_HOME keeps every test out of the real one.
mc_docker_ceiling_cache_file() { printf '%s/docker-ceiling\n' "$(mc_state_dir)"; }

# `<mib> <epoch>`, written whole and then moved into place -- the same
# discipline as notify-message, for the same reason: the daemon can read this
# file while a terminal's `status` is writing it, and a half-written line parses
# as two non-numeric fields, which the strict reader below correctly discards.
# That would return `watch` to being blind for no reason at all. Best-effort
# throughout: a cache that cannot be written must never fail the read it was a
# side effect of.
mc_docker_ceiling_cache_write() {
  local mib="$1" file tmp
  case "$mib" in ''|*[!0-9]*) return 1 ;; esac
  file="$(mc_docker_ceiling_cache_file)"
  mkdir -p "$(mc_state_dir)" 2>/dev/null || return 1
  tmp="$file.$$"
  if printf '%s %s\n' "$mib" "$(date +%s)" > "$tmp" 2>/dev/null && mv -f "$tmp" "$file" 2>/dev/null; then
    return 0
  fi
  rm -f "$tmp" 2>/dev/null
  return 1
}

# Sets MC_DOCKER_CEILING_CACHE_MIB and MC_DOCKER_CEILING_CACHE_AT, or fails with
# neither set to anything usable. Validated exactly as strictly as
# mc_read_idle_stamp validates its own two-integer stamps, and for a stronger
# reason: a half-parsed value here is reported to the user as the ceiling Docker
# is enforcing, and would produce a confident, wrong warning telling them to
# restart Docker.
mc_docker_ceiling_cache_read() {
  local file a="" b=""
  MC_DOCKER_CEILING_CACHE_MIB=0
  MC_DOCKER_CEILING_CACHE_AT=0
  file="$(mc_docker_ceiling_cache_file)"
  [ -f "$file" ] || return 1
  # shellcheck disable=SC2162
  read -r a b < "$file" 2>/dev/null || return 1
  case "$a" in ''|*[!0-9]*) return 1 ;; esac
  case "$b" in ''|*[!0-9]*) return 1 ;; esac
  [ ${#a} -le 18 ] && [ ${#b} -le 18 ] || return 1
  [ $((10#$a)) -gt 0 ] || return 1
  MC_DOCKER_CEILING_CACHE_MIB=$((10#$a))
  MC_DOCKER_CEILING_CACHE_AT=$((10#$b))
  return 0
}

# How long ago that read happened, in mc_format_age's units. A clock that has
# gone backwards (a stamp in the future) reads as 0s rather than as a negative
# age.
mc_docker_ceiling_cache_age() {
  local now age
  now=$(date +%s)
  age=$((now - MC_DOCKER_CEILING_CACHE_AT))
  [ "$age" -lt 0 ] && age=0
  mc_format_age "$age"
}

# Prints the ceiling in whole GB and also leaves it in MC_DOCKER_CEILING_GB, so
# a caller needing the diagnosis alongside the number does not have to choose
# between them. Prints nothing at all on 1 or 2.
mc_docker_ceiling_gb() {
  local mib="" src=""
  MC_DOCKER_CEILING_GB=""
  MC_DOCKER_CEILING_SOURCE=""
  MC_DOCKER_CEILING_ERR=""
  MC_DOCKER_CEILING_AGE=""
  MC_DOCKER_CEILING_UNREADABLE=0
  if [ -n "${MC_DOCKER_CEILING_MIB:-}" ]; then
    # Escape hatch, the MC_DOCKER_RUNTIME pattern again: what Docker Desktop has
    # in its settings file is a property of the host, not something a test can
    # arrange. Deliberately does NOT seed the cache -- it is not a reading of
    # anything, and a test's fixture must never become the number the daemon
    # acts on later.
    mib="$MC_DOCKER_CEILING_MIB"
    src=override
  else
    [ -f "$MC_DOCKER_STORE" ] || return 1
    if cat "$MC_DOCKER_STORE" >/dev/null 2>&1; then
      if command -v jq >/dev/null 2>&1; then
        mib=$(jq -r '.MemoryMiB // empty' "$MC_DOCKER_STORE" 2>/dev/null)
      else
        # Deliberately not a JSON parser: this is a fallback for a machine without
        # the formula's own dependency, and it either finds a plain integer or
        # gives up. A wrong number here would produce a false mismatch warning,
        # which is worse than no warning.
        mib=$(sed -n 's/.*"MemoryMiB"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$MC_DOCKER_STORE" 2>/dev/null | head -1)
      fi
      src=live
    else
      # `cat`, not the shell's own `read`: an EMPTY but readable store must land
      # in the readable branch above (there is simply no ceiling recorded in it),
      # and `read` fails at end-of-file, which would misreport that file as a
      # permission problem and put a "launchd cannot read this" line in
      # actions.log about a file that is perfectly readable.
      # shellcheck disable=SC2034  # read by mc_watch and the tests, not here
      MC_DOCKER_CEILING_ERR="cannot read $MC_DOCKER_STORE -- macOS denies launchd agents access to ~/Library/Group Containers"
      if mc_docker_ceiling_cache_read; then
        mib="$MC_DOCKER_CEILING_CACHE_MIB"
        src=cached
      else
        # shellcheck disable=SC2034  # read by mc_watch, which keys its "blind" log line on it
        MC_DOCKER_CEILING_UNREADABLE=1
        return 2
      fi
    fi
  fi
  case "$mib" in ''|*[!0-9]*) return 1 ;; esac
  [ "$mib" -gt 0 ] || return 1
  case "$src" in
    live)
      mc_docker_ceiling_cache_write "$mib" || :
      MC_DOCKER_CEILING_SOURCE="live"
      ;;
    cached)
      MC_DOCKER_CEILING_AGE=$(mc_docker_ceiling_cache_age)
      MC_DOCKER_CEILING_SOURCE="cached; last read $MC_DOCKER_CEILING_AGE ago from a terminal"
      ;;
    *)
      MC_DOCKER_CEILING_SOURCE="MC_DOCKER_CEILING_MIB"
      ;;
  esac
  MC_DOCKER_CEILING_GB=$((mib / 1024))
  printf '%s' "$MC_DOCKER_CEILING_GB"
  return 0
}

# The one-line explanation of a drift, or nothing at all when the two agree or
# the enforced ceiling cannot be read. Shared so `status` and `watch` cannot
# describe the same condition two different ways. Also left in
# MC_DOCKER_CEILING_DRIFT so a caller can have both the message and the read's
# diagnosis: capturing this through a command substitution discards the latter,
# which is how the unreadable-store case stayed invisible for 8 days.
mc_docker_ceiling_drift() {
  local configured="${1:-0}" rc=0
  MC_DOCKER_CEILING_DRIFT=""
  # The read happens FIRST and unconditionally, before the configured value is
  # even validated: whether Docker's settings file is readable is a fact about
  # the host regardless of what memcap.conf asks for, `watch` logs about that
  # fact separately from any drift, and every command that CAN reach the file
  # should seed the cache while it is here. The message rules below are
  # unchanged -- an unmanaged Docker (0 GB) is still not a drift.
  mc_docker_ceiling_gb >/dev/null || rc=$?
  case "$configured" in ''|*[!0-9]*) return 1 ;; esac
  [ "$configured" -gt 0 ] || return 1
  [ "$rc" -eq 0 ] || return 1
  [ "$MC_DOCKER_CEILING_GB" = "$configured" ] && return 1
  MC_DOCKER_CEILING_DRIFT=$(printf "Docker is enforcing a %s GB VM ceiling, not the %s GB in your config -- the agent budget is computed from a number Docker is not honoring. Fix with: memcap docker apply" \
    "$MC_DOCKER_CEILING_GB" "$configured")
  # Says so when the number is remembered rather than read. Without this the
  # daemon would state a ceiling in the present tense on the strength of a file
  # it cannot open, and a user who had just fixed the drift would keep being told
  # about it until something ran from a terminal again.
  case "$MC_DOCKER_CEILING_SOURCE" in
    cached*)
      MC_DOCKER_CEILING_DRIFT="$MC_DOCKER_CEILING_DRIFT (Docker's settings file is unreadable from the background service; this is the value memcap last read from a terminal, $MC_DOCKER_CEILING_AGE ago -- run 'memcap status' to refresh it)"
      ;;
  esac
  printf '%s' "$MC_DOCKER_CEILING_DRIFT"
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
  # Refresh the cache from the value just written, rather than leaving the
  # background service to keep reporting the PREVIOUS ceiling until someone runs
  # `status`. This is the one moment memcap knows the enforced number for
  # certain, and `docker apply` always runs from a terminal, so it is also one of
  # only two places that can write this file at all. Placed after the write is
  # confirmed: caching a ceiling that was not actually stored would be worse than
  # caching nothing.
  mc_docker_ceiling_cache_write "$mem_mib" || :

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
