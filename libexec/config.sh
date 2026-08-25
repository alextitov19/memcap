#!/usr/bin/env bash
# Reading the config file, and validating every number that comes out of it.
# Sourced immediately after common.sh (it needs mc_config_file and mc_log*), by
# bin/memcap and by any module or test that reads a config knob.
#
# Sourcing this file is cheap and side-effect-free: it defines functions and
# sets MC_CONFIG_BROKEN to a known-good default. It does not read the config,
# touch the state directory, or run a subprocess. mc_load_config does the work.
set -uo pipefail

# The knobs memcap itself writes and reads. Used to scrub half-applied values off
# a broken config (see mc_load_config); keep it in sync with init.sh's heredoc.
MC_CONFIG_KEYS="TOTAL_BUDGET_GB DOCKER_BUDGET_GB DOCKER_CPUS SOFT_TRIGGER \
MIN_FREE_PCT TIER2_MIN_AGE_SEC SIM_IDLE_GRACE_SEC SIM_ACTIVE_CPU_SEC \
MOBILE_TOOLING_IDLE_SEC TIER3_REQUIRE_NO_SESSION STALE_PASS_SEC \
LOG_THROTTLE_SEC EXTRA_AGENTS \
TIER1_MIN_AGE_SEC TIER1_MAX_CWD_LOOKUPS TIER2_ENABLED LIVENESS_SEC \
TIER3_AGENT_TREE_GRACE_SEC ROOT_TTL_DAYS ROOT_MAX MEASURE_MISSING_PCT_MAX \
NOTIFY_ICON"

# 1 once a config file has been found unusable. Defaulted here so that every
# consumer can read it under `set -u` even on a code path where mc_load_config
# was never called (a module sourced directly by a unit test, say).
: "${MC_CONFIG_BROKEN:=0}"
export MC_CONFIG_BROKEN

# Throttle keys become filenames under $(mc_state_dir)/log-throttle. Names are
# programmer-supplied constants, so anything unexpected is a bug rather than
# user input -- fold it onto one shared key rather than writing a path.
mc_throttle_key() {
  case "${1-}" in
    ''|*[!A-Za-z0-9_-]*) printf 'config-other\n' ;;
    *) printf 'config-%s\n' "$1" ;;
  esac
}

# mc_num VALUE DEFAULT NAME -- the only sanctioned way to read a numeric knob.
#
# `[` returns status 2, not 1, when an operand is not an integer, and every
# numeric gate in this tool sits to the LEFT of an `&&`. So a knob set to "5m"
# does not make its guard fail -- it makes the guard EVAPORATE:
#
#   $ [ 1000 -lt "5m" ] && echo GATE FIRED
#   bash: [: 5m: integer expression expected      # status 2, nothing echoed
#
# With TIER2_MIN_AGE_SEC="5m" that is tier 2's "do not kill young processes"
# check gone, and a process one second old gets killed. Every numeric knob
# failed open exactly this way, and the README invites users to edit them.
#
# Leading zeros are the other half: bash reads 016 as OCTAL 14 (a silently 20%
# tighter budget) and dies outright on 08 ("value too great for base"). An
# aligned column of values is a natural thing for a person to write, so this
# strips leading zeros rather than honouring a base nobody meant.
#
# Never fails the caller: it always prints something usable on stdout, so it is
# safe on the right-hand side of an assignment under `set -u`/`pipefail`.
mc_num() {
  local value="${1-}" default="${2-0}" name="${3-value}"
  case "$value" in
    ''|*[!0-9]*) ;;
    *)
      while [ ${#value} -gt 1 ]; do
        case "$value" in
          0*) value="${value#0}" ;;
          *) break ;;
        esac
      done
      # Past 18 digits bash's signed 64-bit arithmetic wraps, so a caller doing
      # `[ "$x" -lt ... ]` or `$((x * 1024))` would get a NEGATIVE number out of
      # a value the user wrote as enormous. Refuse it like any other non-number.
      if [ ${#value} -le 18 ]; then
        printf '%s\n' "$value"
        return 0
      fi
      ;;
  esac
  mc_log_throttled "$(mc_throttle_key "$name")" \
    "config: $name is not a whole number ('${1-}') -- using $default"
  printf '%s\n' "$default"
  return 0
}

# The fractional sibling of mc_num, for SOFT_TRIGGER -- the one knob that is a
# ratio rather than a count. It reaches awk rather than `[`, so it fails open in
# the opposite direction: awk coerces a non-number to 0, `agent > budget*0` is
# true for any live agent, and the soft trigger fires on EVERY pass instead of
# never. Same class of defect, opposite sign.
mc_frac() {
  local value="${1-}" default="${2-0}" name="${3-value}"
  case "$value" in
    ''|.|*[!0-9.]*|*.*.*) ;;
    *) printf '%s\n' "$value"; return 0 ;;
  esac
  mc_log_throttled "$(mc_throttle_key "$name")" \
    "config: $name is not a number ('${1-}') -- using $default"
  printf '%s\n' "$default"
  return 0
}

# Source a file only if it PARSES, and report which way it failed.
#   0 sourced cleanly
#   1 parsed, but something in it failed while running (partially applied)
#   2 syntax error -- NOT sourced, nothing applied
#   3 missing or unreadable
#
# The pre-flight parse is the point. `.` on a file with a syntax error aborts
# the sourcing but not the shell, so assignments before the error apply and
# assignments after it do not -- a half-applied config that looks fully applied.
# Checking first means a broken file changes nothing at all.
mc_source_checked() {
  local file="${1-}"
  [ -n "$file" ] && [ -f "$file" ] && [ -r "$file" ] || return 3
  bash -n "$file" >/dev/null 2>&1 || return 2
  # shellcheck source=/dev/null
  . "$file" || return 1
  return 0
}

# Loads memcap.conf, or refuses. bin/memcap calls this instead of a bare `.`.
#
# On a broken file memcap does NOT fall back to computed defaults and carry on.
# It sets MC_CONFIG_BROKEN=1 and lets the acting commands refuse. The reasoning:
# every knob in this file exists to STOP memcap killing something -- minimum
# process age, simulator grace, the tooling veto, the budget itself. Running on
# defaults would mean enforcing a policy the user never chose, against processes
# they thought they had protected, while `status` reported everything healthy.
# Not reaping a leaked process is recoverable; killing a dev server with live
# work in it is not. So: refuse, loudly, until one character gets fixed.
mc_load_config() {
  local file rc reason k
  MC_CONFIG_BROKEN=0
  export MC_CONFIG_BROKEN
  file="$(mc_config_file)"
  # No config at all is not an error -- a fresh install runs on computed
  # defaults until `memcap init`. Only a config that EXISTS and is unusable is.
  [ -e "$file" ] || return 0

  mc_source_checked "$file"
  rc=$?
  [ "$rc" -eq 0 ] && return 0

  MC_CONFIG_BROKEN=1
  case "$rc" in
    2) reason="has a syntax error" ;;
    3) reason="is not readable" ;;
    *) reason="failed while being read" ;;
  esac

  # Unthrottled: a config the user believes is active but is not is the single
  # worst state this tool can be in, and it persists until they act on it.
  mc_log "config: $file $reason -- REFUSING to enforce until it is fixed"
  echo "memcap: $file $reason -- not enforcing. Check it with: bash -n $file" >&2

  # Drop anything a runtime failure managed to apply before it died, so no
  # consumer downstream reads half a config and believes it is whole.
  for k in $MC_CONFIG_KEYS; do
    unset "$k"
  done
  return 1
}

# Guard for commands that ACT on config values -- sweeping, rewriting the
# Docker VM, rewriting the config itself. `watch` is deliberately NOT guarded
# here: mc_watch has to reach its own refusal path so it still stamps the
# heartbeat and the last-outcome file.
mc_refuse_if_broken() {
  local what="${1-act}" file
  [ "${MC_CONFIG_BROKEN:-0}" = "1" ] || return 0
  file="$(mc_config_file)"
  echo "memcap: refusing to $what -- $file is broken. Check it with: bash -n $file" >&2
  return 1
}
