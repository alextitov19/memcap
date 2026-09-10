#!/usr/bin/env bash
set -uo pipefail

# Prompt with a default the user accepts by pressing Enter. The prompt goes to stderr
# and the answer is read from stdin, so the flow works interactively AND when answers
# are piped in (`yes '' | memcap init`), which is what makes init testable.
mc_ask() {
  local prompt="$1" default="$2" answer
  printf '  %-44s [%s] ' "$prompt" "$default" >&2
  read -r answer || answer=""
  echo "${answer:-$default}"
}

# Re-prompts until the answer is a plain non-negative integer of at least `min`
# (default 0). Both cap and the Docker ceiling feed `-ge`/`-eq` comparisons and,
# downstream in watch/status, bash arithmetic under `set -u` -- a non-numeric
# answer (a typo, or someone typing "sixteen") dies there with a raw "integer
# expression expected" error instead of a message a user could act on. `min=1`
# is what rejects a degenerate 0 total cap; the Docker prompt explicitly allows 0
# ("0 to skip Docker"), so it passes min=0.
mc_ask_int() {
  local prompt="$1" default="$2" min="${3:-0}" answer
  while :; do
    answer=$(mc_ask "$prompt" "$default")
    case "$answer" in
      ''|*[!0-9]*) echo "  Please enter a whole number." >&2; continue ;;
    esac
    if [ "$answer" -lt "$min" ]; then
      echo "  Please enter a number of at least $min." >&2
      continue
    fi
    echo "$answer"
    return 0
  done
}

# Every yes/no prompt here compared against the literal string "yes" or "no".
# Someone answering "n" to "Enforce by killing leaked processes? (yes/no)" was
# therefore not answering no, and got enforcement. On a consent question the
# unrecognised answer must land on the side that does not kill anything, so
# only an explicit affirmative counts as yes.
mc_is_yes() {
  case "${1-}" in
    [Yy]|[Yy][Ee][Ss]|[Tt][Rr][Uu][Ee]|1) return 0 ;;
  esac
  return 1
}

mc_run_init() {
  local no_service=0 no_docker=0 total cores cap docker_gb agents enforce start_svc safe_docker
  local proto state_dir
  while [ $# -gt 0 ]; do
    case "$1" in
      --no-service) no_service=1 ;;
      --no-docker)  no_docker=1 ;;
    esac
    shift
  done

  # sysctl can be absent or fail in a container or under a restricted shell.
  # `total` reaches `-lt` below and `cores` reaches arithmetic, where an empty
  # answer would silently become 0 and write DOCKER_CPUS=0. A detected 0 is worse
  # than non-numeric: mc_num passes it (it IS a whole number) and mc_cap_gb then
  # returns a cap of 0, which mc_ask_int's min=1 rejects on every re-prompt --
  # including the default it falls back to at EOF, i.e. an init that never ends.
  total=$(mc_num "$(mc_total_ram_gb)" 16 "detected RAM")
  cores=$(mc_num "$(mc_cpu_count)" 8 "detected cores")
  if [ "$total" -lt 1 ] || [ "$cores" -lt 1 ]; then
    echo "  Could not read this machine's RAM/CPU from sysctl -- assuming 16 GB, 8 cores." >&2
    [ "$total" -lt 1 ] && total=16
    [ "$cores" -lt 1 ] && cores=8
  fi
  agents=$(mc_installed_agents)
  cap=$(mc_cap_gb "$total"); docker_gb=$(mc_docker_gb "$cap")

  echo "  Detected: ${total} GB RAM · ${cores} cores · agents: ${agents:-none found}"
  if [ "$total" -lt 16 ]; then
    echo
    echo "  Note: ${total} GB is below the practical floor for running coding agents"
    echo "  alongside Docker. memcap will still reap leaked processes, which is where"
    echo "  most of the benefit is on a machine this size, but the Docker/agent split"
    echo "  will be tight."
  fi
  echo

  # Import the prototype's config if present, so the author's machine does not regress.
  # Checked and validated like any other config: this file is hand-written, its
  # values become the DEFAULTS the user accepts by pressing Enter, and a bad one
  # would be written straight into memcap.conf with the user's apparent blessing.
  proto="$HOME/.claude/agent-budget.conf"
  if [ -f "$proto" ]; then
    if mc_source_checked "$proto"; then
      cap=$(mc_num "${TOTAL_BUDGET_GB:-$cap}" "$cap" TOTAL_BUDGET_GB)
      docker_gb=$(mc_num "${DOCKER_BUDGET_GB:-$docker_gb}" "$docker_gb" DOCKER_BUDGET_GB)
      echo "  Imported existing settings from ~/.claude/agent-budget.conf"
    else
      echo "  Ignoring ~/.claude/agent-budget.conf -- it does not parse (bash -n $proto)" >&2
    fi
  fi

  cap=$(mc_ask_int "Total cap for agents + Docker + sims (GB)" "$cap" 1)
  if [ "$no_docker" = "0" ]; then
    docker_gb=$(mc_ask_int "Docker VM ceiling (GB, 0 to skip Docker)" "$docker_gb" 0)
  else
    docker_gb=0
  fi

  # A Docker ceiling that meets or exceeds the total cap leaves agents with zero or
  # negative budget. `watch` would then believe agents are permanently over budget and
  # kill a dev server on every pass. Refuse to write that config -- fall back to the
  # same safe split mc_docker_gb would have suggested instead of trusting the answer.
  if [ "$no_docker" = "0" ] && [ "$docker_gb" -ge "$cap" ]; then
    safe_docker=$(mc_docker_gb "$cap")
    echo "  Refusing DOCKER_BUDGET_GB=$docker_gb with TOTAL_BUDGET_GB=$cap -- that would leave no budget for agents." >&2
    echo "  Using ${safe_docker} GB for Docker instead." >&2
    docker_gb="$safe_docker"
  fi

  enforce=$(mc_ask "Enforce by killing leaked processes? (yes/no)" "yes")

  mkdir -p "$(mc_config_dir)"
  cat > "$(mc_config_file)" <<EOF
# memcap configuration -- edit freely. Not touched by brew upgrade.
# Machine at init: ${total} GB RAM, ${cores} cores.
TOTAL_BUDGET_GB=$cap
DOCKER_BUDGET_GB=$docker_gb
DOCKER_CPUS=$((cores * 55 / 100))
SOFT_TRIGGER=0.80
MIN_FREE_PCT=15
TIER2_MIN_AGE_SEC=300
SIM_IDLE_GRACE_SEC=600
SIM_ACTIVE_CPU_SEC=2
MOBILE_TOOLING_IDLE_SEC=300
TIER3_REQUIRE_NO_SESSION=0
STALE_PASS_SEC=300
LOG_THROTTLE_SEC=1800
TIER1_MIN_AGE_SEC=300
TIER1_MAX_CWD_LOOKUPS=64
TIER2_ENABLED=1
LIVENESS_SEC=3600
TIER3_AGENT_TREE_GRACE_SEC=1800
ROOT_TTL_DAYS=14
ROOT_MAX=64
MEASURE_MISSING_PCT_MAX=10
HOST_MIN_DISK_GB=10
HOST_MAX_SWAP_GB=8
PRESSURE_SNAPSHOT_SEC=300
EXTRA_AGENTS=""
# The emoji on memcap's notifications. Change it, then run: memcap notify
# NOTIFY_ICON=none goes back to plain Script Editor notifications.
NOTIFY_ICON="🧠"
EOF

  echo
  echo "  Wrote $(mc_config_file)"

  # The notification bundle memcap posts through (notify.sh). Failing to build
  # one costs the custom icon and nothing else, so it never fails init -- and it
  # is deliberately built AFTER the config is written, since the icon it renders
  # is the one that file now names.
  mc_ensure_notifier || :

  # The paused marker lives under the STATE directory, which -- unlike the config
  # directory mkdir -p'd above -- does not exist on a fresh install. This `touch`
  # used to fail silently, so a user who answered "no" to killing their processes
  # got enforcement anyway, with no marker, no message, and no way to tell. The
  # population that hit it is exactly the population that could not detect it.
  # Failing loudly here matters more than finishing init: a wrong answer to this
  # question is measured in killed dev servers.
  if ! mc_is_yes "$enforce"; then
    state_dir="$(mc_state_dir)"
    if mkdir -p "$state_dir" 2>/dev/null && touch "$state_dir/paused" 2>/dev/null; then
      echo "  Enforcement is OFF -- memcap will report only. Turn it on with: memcap on"
    else
      echo >&2
      echo "  ERROR: could not write $state_dir/paused" >&2
      echo "  You asked memcap NOT to kill anything, and it cannot record that." >&2
      echo "  memcap WOULD ENFORCE. Fix the directory's permissions and run: memcap off" >&2
      return 1
    fi
  elif [ -f "$(mc_state_dir)/paused" ]; then
    # Do NOT silently clear a pause the user set with `memcap off`. The default
    # answer here is "yes", so someone re-running init only to change the Docker
    # ceiling would otherwise resume enforcement by pressing Enter past a
    # question they were not really answering.
    echo "  memcap is currently paused and stays paused -- resume it with: memcap on"
  else
    echo "  Enforcement is ON -- memcap will kill leaked processes. Turn it off with: memcap off"
  fi

  if [ "$no_service" = "0" ]; then
    start_svc=$(mc_ask "Install and start the background service now? (yes/no)" "yes")
    # memcap owns and installs its own LaunchAgent (see service.sh) rather than
    # going through `brew services start` -- Homebrew's own copy of this job is
    # what `brew upgrade` was found to silently remove. mc_service_install also
    # migrates away from an existing Homebrew-owned plist if one is found.
    mc_is_yes "$start_svc" && mc_service_install
  fi
  return 0
}
