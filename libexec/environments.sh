#!/usr/bin/env bash
# Container termination shares the central enforcement choke point. No Docker
# Desktop settings, restarts, volume deletion or process-name guessing.
mc_environment_stop() {
  local token="$1" caller="${2:-}" ids args="" endpoint
  mc_is_paused && return 1
  [ "${MC_CONFIG_BROKEN:-0}" != 1 ] || return 1
  [ -z "$caller" ] || args="--caller $caller"
  endpoint=$(python3 "$LIB/environments.py" _endpoint "$token") || return 1
  # shellcheck disable=SC2086 # caller is a numeric parent PID, never user text
  ids=$(python3 "$LIB/environments.py" _authorize "$token" $args) || return 1
  if [ -z "$ids" ]; then
    [ "${MC_DRY_RUN:-0}" != 1 ] || return 1
    python3 "$LIB/environments.py" _finish "$token" || return 1
    return 1 # No container was signalled; retain the choke point's contract.
  fi
  if [ "${MC_DRY_RUN:-0}" = 1 ]; then
    printf 'would stop disposable environment %s: %s\n' "$token" "$ids"
    return 1
  fi
  # Authorization immediately precedes the Docker request. IDs are full immutable
  # IDs, never names; stop preserves containers and volumes for inspection.
  # shellcheck disable=SC2086
  docker --host "$endpoint" stop --timeout 30 $ids || return 1
  # Docker chooses the image's stop signal; zero explicitly means unknown here.
  # shellcheck source=/dev/null
  . "$LIB/analytics.sh"
  mc_analytics_signal environment 0 1 "$ids" "$token" || :
  python3 "$LIB/environments.py" _finish "$token" || return 1
  mc_log "stopped disposable environment $token"
}

mc_environment_reap() {
  local tokens token
  mc_is_paused && return 0
  [ -f "$(mc_state_dir)/environments/resources.json" ] || return 0
  tokens=$(python3 "$LIB/environments.py" _scan) || return 0
  while IFS= read -r token; do
    [ -n "$token" ] || continue
    mc_kill_pids "$token" 'finished disposable environment' environment || :
  done <<EOF_TOKENS
$tokens
EOF_TOKENS
}
