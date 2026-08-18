#!/usr/bin/env bash
set -uo pipefail

mc_gb() { awk -v k="$1" 'BEGIN{printf "%.2f", k/1024/1024}'; }

# Formats an elapsed-seconds count the way a person would say "how long ago" --
# seconds under a minute, then minutes, hours, days -- not a fixed-width
# duration. Matches the units mc_etime_secs (enforce.sh) parses, in reverse.
mc_format_age() {
  local s="$1"
  if [ "$s" -lt 60 ]; then printf '%ds' "$s"
  elif [ "$s" -lt 3600 ]; then printf '%dm' $((s / 60))
  elif [ "$s" -lt 86400 ]; then printf '%dh' $((s / 3600))
  else printf '%dd' $((s / 86400))
  fi
}

mc_render_status() {
  local total cap docker_budget agents_budget agent_gb agent_net_gb docker_gb combined free docker_ceiling_label
  local heartbeat_ts now age stale_sec heartbeat_line remedy_line
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

  # A stopped service is otherwise indistinguishable from a quiet one: budget
  # renders fine, nothing errors, no notification fires. mc_watch stamps this
  # file on every pass, including the paused and misconfigured-budget early
  # returns, so a fresh stamp under `memcap off` correctly reads as paused, not
  # dead -- mc_is_paused below reports that distinction on its own.
  now=$(date +%s)
  stale_sec="${STALE_PASS_SEC:-300}"
  remedy_line=""
  heartbeat_ts=$(cat "$(mc_state_dir)/last-pass" 2>/dev/null)
  case "$heartbeat_ts" in
    ''|*[!0-9]*)
      heartbeat_line="  last enforcement pass            NEVER"
      remedy_line="  MEMCAP HAS NOT RUN SINCE INSTALL -- memcap service install"
      ;;
    *)
      age=$((now - heartbeat_ts))
      # A clock moved backward (NTP correction, manual adjustment) would print
      # a nonsensical negative duration; treat it as fresh rather than alarming
      # over something that isn't evidence of anything.
      [ "$age" -lt 0 ] && age=0
      if [ "$age" -le "$stale_sec" ]; then
        heartbeat_line="  last enforcement pass            $(mc_format_age "$age") ago"
      else
        heartbeat_line="  last enforcement pass            $(mc_format_age "$age") ago"
        remedy_line="  MEMCAP IS PROBABLY NOT RUNNING -- memcap service install"
      fi
      ;;
  esac

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
${heartbeat_line}
EOF
  [ -n "$remedy_line" ] && echo "$remedy_line"
  mc_is_paused && echo "  ENFORCEMENT PAUSED (memcap on to resume)"
  return 0
}
