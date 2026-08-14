#!/usr/bin/env bash
# Shared paths, config access, logging. Sourced by every other module.
set -uo pipefail

mc_config_dir() { printf '%s/memcap\n' "${MEMCAP_CONFIG_HOME:-$HOME/.config}"; }
mc_config_file() { printf '%s/memcap.conf\n' "$(mc_config_dir)"; }
mc_state_dir() { printf '%s/memcap\n' "${MEMCAP_STATE_HOME:-$HOME/.local/state}"; }

mc_log() {
  local dir; dir="$(mc_state_dir)"
  mkdir -p "$dir"
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$dir/actions.log"
}

mc_is_paused() { [ -f "$(mc_state_dir)/paused" ]; }

# Rate-limited desktop notification: at most one per 5 minutes.
mc_notify() {
  local stamp now last
  stamp="$(mc_state_dir)/.notified"
  now=$(date +%s); last=$(cat "$stamp" 2>/dev/null || echo 0)
  [ $((now - last)) -lt 300 ] && return 0
  mkdir -p "$(mc_state_dir)"; echo "$now" > "$stamp"
  # Escape before interpolating into AppleScript: an unescaped quote or backslash in a
  # message breaks the script, and callers build messages from process data.
  local msg="$1"
  msg=${msg//\\/\\\\}
  msg=${msg//\"/\\\"}
  osascript -e "display notification \"$msg\" with title \"memcap\"" 2>/dev/null
  return 0
}
