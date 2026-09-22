#!/usr/bin/env bash
# Optional lifecycle collector. All signals still pass through mc_kill_pids.
# shellcheck disable=SC2034

mc_gc_config() {
  [ "${MC_CONFIG_BROKEN:-0}" != 1 ] || return 1
  case "${GC_MODE:-observe}" in on|observe|off) ;; *)
    mc_log_throttled gc-config 'garbage collector: invalid GC_MODE; refusing cleanup'; return 1 ;;
  esac
  export GC_IDLE_SEC="${GC_IDLE_SEC:-600}"
  MC_GC_PYTHON=$(command -v python3) || {
    mc_log_throttled gc-python 'garbage collector: Python 3 unavailable; idle helpers retained'
    return 1
  }
}

mc_gc_event() {
  local marker response
  mc_gc_config || return 0
  # A failed or timed-out hook must not leave a stale idle authorization behind.
  # Mark first, before Python starts; only a successfully recorded event clears
  # this invocation's marker. No prompt or tool arguments are persisted.
  marker="$(mc_state_dir)/gc-activity-pending/$$"
  mc_state_write "$marker" pending || return 1
  if response=$(printf '%s' "$1" | "$MC_GC_PYTHON" "$LIB/idle_gc.py" event); then
    rm -f "$marker"
    # Clear the activity marker BEFORE waiting; idle cleanup must keep working.
    if [ "${MC_FEEDBACK_WAIT:-0}" = 1 ] && [ -n "$response" ]; then
      printf '%s' "$1" | "$MC_GC_PYTHON" "$LIB/idle_gc.py" wait
    elif [ -n "$response" ]; then
      printf '%s\n' "$response"
    fi
  else
    mc_log_throttled gc-hook 'garbage collector: lifecycle hook failed; cleanup held pending inspection'
    return 1
  fi
}

mc_gc_prepare() {
  MC_GC_AUTHORIZED_PIDS=""
  mc_is_paused && return 1
  [ "${MC_CONFIG_BROKEN:-0}" != 1 ] && [ "${GC_MODE:-observe}" = on ] || return 1
  [ -n "${MC_GC_PYTHON:-}" ] || return 1
  # One fresh measurement for the batch, repeated at each choke-point pass.
  # shellcheck disable=SC2086
  MC_GC_AUTHORIZED_PIDS=$("$MC_GC_PYTHON" "$LIB/idle_gc.py" authorize $1) || return 1
}

mc_gc_allowed() {
  case " ${MC_GC_AUTHORIZED_PIDS:-} " in *" $1 "*) return 0 ;; esac
  return 1
}

mc_reap_idle_helpers() {
  local plan pid kind members count=0 p targets=""
  MC_GC_RECLAIMED=0
  mc_is_paused && return 0
  mc_gc_config || return 0
  [ "${GC_MODE:-observe}" != off ] || return 0
  mc_protection_ready || return 0
  plan=$("$MC_GC_PYTHON" "$LIB/idle_gc.py" scan) || {
    mc_log_throttled gc-scan 'garbage collector: observation failed; retaining helpers'
    return 0
  }
  while read -r pid kind members; do
    [ -n "$pid" ] || continue
    case "$pid:$members" in *[!0-9:,]*) continue ;; esac
    mc_log_throttled "gc-ready-$pid" "garbage collector: $kind pid $pid has completed its idle grace (${GC_IDLE_SEC}s); mode=${GC_MODE:-observe}"
    [ "${GC_MODE:-observe}" = on ] || continue
    # No resource other than this freshly verified candidate can use the
    # lifecycle exception; authorize is repeated before TERM and KILL.
    for p in ${members//,/ }; do
      targets="$targets $p"
      count=$((count + 1))
    done
    [ "$count" -lt 32 ] || break
  done <<EOF
$plan
EOF
  if [ -n "$targets" ] && mc_kill_pids "$targets" 'garbage collector idle helpers' idle-gc; then
    MC_GC_RECLAIMED=1
  fi
  return 0
}

mc_gc_status() {
  mc_gc_config || return 1
  printf 'Garbage collector: %s; idle grace: %ss\n' "${GC_MODE:-observe}" "$GC_IDLE_SEC"
  "$MC_GC_PYTHON" "$LIB/idle_gc.py" status
}
