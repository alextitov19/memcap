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

# For status lines that would otherwise repeat on every single pass -- "an agent
# session is alive", "combined exceeds the cap" -- rather than a discrete event
# like a kill. Unthrottled, these drowned the actual audit trail: on the author's
# machine, 94% of a day's actions.log was two such lines repeated every poll,
# burying the 210 kill records the file exists for. Stamps live in their own
# sub-directory, keyed by a caller-supplied string, so callers with unrelated
# reasons (two different tier-3 decline causes, say) throttle independently and
# none of this collides with sims-idle/, roots, paused, or .notified.
mc_log_throttle_dir() { printf '%s/log-throttle\n' "$(mc_state_dir)"; }

mc_log_throttled() {
  local key="$1" msg="$2" dir stamp now last
  dir="$(mc_log_throttle_dir)"
  stamp="$dir/$key"
  now=$(date +%s)
  last=$(cat "$stamp" 2>/dev/null || echo 0)
  [ $((now - last)) -lt "${LOG_THROTTLE_SEC:-1800}" ] && return 0
  mkdir -p "$dir"
  echo "$now" > "$stamp"
  mc_log "$msg"
}

# Clears one key's stamp, so the next mc_log_throttled call for it logs
# immediately rather than waiting out whatever window was already ticking.
# Callers use this the moment a key's condition stops holding, so a state change
# -- going back under the combined cap and later back over it, say -- always gets
# its own line even inside the window that would otherwise still be running from
# the PRIOR occurrence. Without this, throttling would swallow exactly the
# transitions actions.log exists to make diagnosable.
mc_log_throttle_clear() {
  rm -f "$(mc_log_throttle_dir)/$1"
}

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
