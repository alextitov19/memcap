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

# Formats an elapsed-seconds count the way a person would say "how long ago" --
# seconds under a minute, then minutes, hours, days -- not a fixed-width
# duration. Matches the units mc_etime_secs (enforce.sh) parses, in reverse.
# Lives here rather than in status.sh because `memcap on` reports a pause
# duration too, and one formatting rule shared beats two that drift.
mc_format_age() {
  local s="$1"
  if [ "$s" -lt 60 ]; then printf '%ds' "$s"
  elif [ "$s" -lt 3600 ]; then printf '%dm' $((s / 60))
  elif [ "$s" -lt 86400 ]; then printf '%dh' $((s / 3600))
  else printf '%dd' $((s / 86400))
  fi
}

# Seconds of AWAKE time since boot -- kern.monotonicclock, which is derived from
# mach_absolute_time and so does NOT advance while the machine is asleep. That
# property is the whole point: wall-clock arithmetic cannot tell a daemon that
# stopped from a laptop whose lid was shut, and after a lid-close `status` used
# to shout "MEMCAP IS PROBABLY NOT RUNNING" for the ~60s until the next pass.
# A false alarm is how a real one gets ignored.
#
# MC_AWAKE_SECS overrides it for tests, the same escape-hatch pattern as
# MC_HANDS_ON_MOBILE: sleep cannot be simulated, and a machine that has never
# slept since boot (kern.sleeptime = 0 on this one) cannot exercise the path.
# Returns non-zero, printing nothing, when the reading is unavailable or not a
# number; every caller must keep working on wall-clock alone in that case.
mc_awake_secs() {
  local v
  if [ -n "${MC_AWAKE_SECS:-}" ]; then
    v="$MC_AWAKE_SECS"
  else
    v=$(sysctl -n kern.monotonicclock 2>/dev/null) || v=""
  fi
  case "$v" in
    ''|*[!0-9]*) return 1 ;;
  esac
  printf '%s' "$v"
}

# Epoch seconds of the most recent wake, falling back to the most recent boot.
# The coarse companion to mc_awake_secs, used only for a heartbeat stamped by a
# memcap old enough not to have written an awake stamp beside it. kern.waketime
# reads `{ sec = 0, usec = 0 }` on a machine that has not slept since boot, so a
# zero is "no wake recorded", not "the epoch".
mc_last_wake() {
  local key raw sec
  if [ -n "${MC_LAST_WAKE:-}" ]; then
    case "$MC_LAST_WAKE" in
      ''|*[!0-9]*) return 1 ;;
    esac
    printf '%s' "$MC_LAST_WAKE"
    return 0
  fi
  for key in kern.waketime kern.boottime; do
    raw=$(sysctl -n "$key" 2>/dev/null) || continue
    sec=$(printf '%s' "$raw" | sed -n 's/.*sec[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p') || sec=""
    case "$sec" in
      ''|0) continue ;;
    esac
    printf '%s' "$sec"
    return 0
  done
  return 1
}

# When the pause started, for `status` and for the resume log line. Reads the
# epoch `mc_pause` writes into the marker; falls back to the file's mtime so a
# marker left by an older memcap (or by a hand `touch`) still dates itself.
mc_paused_since() {
  local f ts
  f="$(mc_state_dir)/paused"
  [ -f "$f" ] || return 1
  ts=$(head -1 "$f" 2>/dev/null | tr -dc '0-9') || ts=""
  if [ -z "$ts" ]; then
    ts=$(stat -f %m "$f" 2>/dev/null) || ts=$(stat -c %Y "$f" 2>/dev/null) || ts=""
  fi
  case "$ts" in
    ''|*[!0-9]*) return 1 ;;
  esac
  printf '%s' "$ts"
}

# `memcap off` and `memcap on` wrote NOTHING to actions.log. The log is the only
# record of what memcap did with a day, so a week spent paused and a week spent
# dead were indistinguishable in it after the fact -- with the pause marker
# removed on resume, even the fact that a pause had happened was gone. Both
# transitions are now events in the log, and the pause carries its own start
# time so `status` and the resume line can say how long it lasted.
mc_pause() {
  mkdir -p "$(mc_state_dir)"
  date +%s > "$(mc_state_dir)/paused"
  mc_log "off: enforcement PAUSED by user -- no tier will act until 'memcap on'"
}

mc_resume() {
  local since now ago=""
  if since=$(mc_paused_since); then
    now=$(date +%s)
    [ "$now" -ge "$since" ] && ago=$(mc_format_age $((now - since)))
  fi
  rm -f "$(mc_state_dir)/paused"
  if [ -n "$ago" ]; then
    mc_log "on: enforcement RESUMED by user -- was paused for $ago"
    echo "memcap resumed (was paused for $ago)"
  else
    mc_log "on: enforcement RESUMED by user"
    echo "memcap resumed"
  fi
}

# A stopped service looks exactly like a quiet one: `status` still prints a full
# budget, nothing errors, no notification fires. Only the author noticing 28
# hours of silence caught it -- not paused, not rebooted, no evidence of a
# cause. This stamp is `status`'s only way to tell "not running" from "nothing
# to do". Written on every mc_watch invocation that completes, including the
# paused and misconfigured-budget early returns -- it answers "is the daemon
# ticking", a different question from "is it enforcing", which those paths
# already report on their own.
#
# Stamped in two clocks, not one. The wall clock is what a person reads ("3h
# ago"); the awake clock (mc_awake_secs, which freezes while the machine sleeps)
# is what `status` actually judges staleness against, so a closed lid no longer
# looks like a stopped daemon. The awake stamp is REMOVED rather than left
# behind when the reading is unavailable -- a stale one would be read as the
# current boot's and could hide a genuinely dead daemon, which is the failure
# this whole file exists to make visible.
mc_stamp_heartbeat() {
  local dir awake
  dir="$(mc_state_dir)"
  mkdir -p "$dir"
  date +%s > "$dir/last-pass"
  if awake=$(mc_awake_secs); then
    printf '%s\n' "$awake" > "$dir/last-pass-awake"
  else
    rm -f "$dir/last-pass-awake"
  fi
}

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

# Permission-independent liveness test. NEVER use `kill -0` for this: it returns
# failure for EPERM ("alive, but not yours to signal") exactly as it does for
# ESRCH ("dead"). That single confusion kept tier 3 from ever firing for the
# entire life of the tool -- root-owned `simdiskimaged` matches MC_SIM_EXE, enters
# SIMPIDS on every pass on any Mac with Xcode installed, failed `kill -0`, had its
# idle stamp deleted as though it had exited, and pinned `all_ready=0` forever.
# `ps -p` asks the question actually being asked: does this pid exist.
mc_pid_alive() { ps -p "$1" >/dev/null 2>&1; }
