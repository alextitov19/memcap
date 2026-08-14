#!/usr/bin/env bash
set -uo pipefail

mc_gb() { awk -v k="$1" 'BEGIN{printf "%.2f", k/1024/1024}'; }

mc_render_status() {
  local total cap docker_budget agents_budget agent_gb agent_net_gb docker_gb combined free docker_ceiling_label
  total=$(mc_total_ram_gb)
  cap="${TOTAL_BUDGET_GB:-$(mc_cap_gb "$total")}"
  docker_budget="${DOCKER_BUDGET_GB:-$(mc_docker_gb "$cap")}"
  agents_budget=$((cap - docker_budget))

  eval "$(mc_ps_snapshot | mc_classify)"
  agent_gb=$(mc_gb "$AGENT_KB"); docker_gb=$(mc_gb "$DOCKER_KB")
  agent_net_gb=$(mc_gb "$(mc_agent_net_kb "$AGENT_KB" "$SIM_KB")")
  combined=$(awk -v a="$agent_gb" -v d="$docker_gb" 'BEGIN{printf "%.2f", a+d}')
  free=$(mc_free_pct)

  # A 0 ceiling means Docker is unmanaged, not that it has no allowance; rendering it
  # as "6.41 GB / 0 GB ceiling" reads as catastrophically over budget.
  if [ "$docker_budget" -le 0 ]; then docker_ceiling_label="unmanaged"
  else docker_ceiling_label="${docker_budget} GB ceiling"; fi

  cat <<EOF
memcap — $(mc_config_file)

  agents + everything they spawn   ${agent_gb} GB / ${agents_budget} GB budget
    of which leaked/orphaned       $(mc_gb "$ORPHAN_KB") GB
    of which sims/playwright       $(mc_gb "$SIM_KB") GB
    net of sims (drives tier 2)    ${agent_net_gb} GB
  docker VM + helpers              ${docker_gb} GB / ${docker_ceiling_label}
  ---------------------------------------------------------
  combined                         ${combined} GB / ${cap} GB budget
  system memory available          ${free}%
EOF
  mc_is_paused && echo "  ENFORCEMENT PAUSED (memcap on to resume)"
  return 0
}
