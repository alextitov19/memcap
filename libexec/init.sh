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

mc_run_init() {
  local no_service=0 no_docker=0 total cores cap docker_gb agents enforce start_svc safe_docker
  while [ $# -gt 0 ]; do
    case "$1" in
      --no-service) no_service=1 ;;
      --no-docker)  no_docker=1 ;;
    esac
    shift
  done

  total=$(mc_total_ram_gb); cores=$(mc_cpu_count); agents=$(mc_installed_agents)
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
  if [ -f "$HOME/.claude/agent-budget.conf" ]; then
    # shellcheck source=/dev/null
    . "$HOME/.claude/agent-budget.conf"
    cap="${TOTAL_BUDGET_GB:-$cap}"; docker_gb="${DOCKER_BUDGET_GB:-$docker_gb}"
    echo "  Imported existing settings from ~/.claude/agent-budget.conf"
  fi

  cap=$(mc_ask "Total cap for agents + Docker + sims (GB)" "$cap")
  if [ "$no_docker" = "0" ]; then
    docker_gb=$(mc_ask "Docker VM ceiling (GB, 0 to skip Docker)" "$docker_gb")
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
EXTRA_AGENTS=""
EOF
  [ "$enforce" = "no" ] && touch "$(mc_state_dir)/paused"

  echo
  echo "  Wrote $(mc_config_file)"
  if [ "$no_service" = "0" ]; then
    start_svc=$(mc_ask "Start the background service now? (yes/no)" "yes")
    if [ "$start_svc" = "yes" ]; then
      brew services start memcap 2>/dev/null || \
        echo "  (not installed via brew yet — run 'brew services start memcap' after installing)"
    fi
  fi
  return 0
}
