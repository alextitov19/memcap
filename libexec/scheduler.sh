#!/usr/bin/env bash
# Bash bridge to the optional queue runner. Ordinary watchdog operation has no
# Python dependency. Config is loaded by the dispatcher, never parsed twice.

mc_scheduler_python() {
  command -v python3 >/dev/null 2>&1 || {
    echo 'memcap queue requires Python 3.9+ (brew install python)' >&2
    return 1
  }
  command -v python3
}

mc_scheduler_config() {
  mc_refuse_if_broken schedule || return 1
  export QUEUE_MAX_JOBS="${QUEUE_MAX_JOBS:-2}" QUEUE_WORKERS="${QUEUE_WORKERS:-2}"
  export QUEUE_JOB_GB="${QUEUE_JOB_GB:-2}" QUEUE_HEADROOM_GB="${QUEUE_HEADROOM_GB:-3}"
  export QUEUE_POLL_SEC="${QUEUE_POLL_SEC:-2}" QUEUE_WAIT_SEC="${QUEUE_WAIT_SEC:-1800}"
  export QUEUE_MAX_PRESSURE="${QUEUE_MAX_PRESSURE-green}"
}

mc_scheduler_run() {
  local python
  mc_scheduler_config || return 75
  python=$(mc_scheduler_python) || return 75
  exec "$python" "$LIB/scheduler.py" "$@"
}

mc_scheduler_hook() {
  local python
  if ! mc_scheduler_config || ! python=$(mc_scheduler_python); then
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"memcap queue configuration or Python runtime unavailable"}}'
    return 0
  fi
  if ! "$python" "$LIB/scheduler.py" hook "${1:-codex}"; then
    echo 'memcap queue hook failed; refusing the launch' >&2
    return 2
  fi
}

mc_queue_sample() {
  local total cap free pressure fault=0 tracked
  mc_refuse_if_broken sample || return 1
  mc_snapshot_capture
  [ "${MC_MEASURE_DEGRADED:-1}" = 0 ] && [ "${MC_MEASURE_FAULT:-1}" = 0 ] || fault=1
  eval "$(printf '%s\n' "$MC_CAPTURE_SNAPSHOT" | mc_classify)"
  [ -n "${AGENT_KB+x}" ] && [ -n "${DOCKER_KB+x}" ] && [ -n "${PROTECTEDPIDS+x}" ] || return 1
  total=$(sysctl -n hw.memsize 2>/dev/null) || return 1
  case "$total" in ''|*[!0-9]*) return 1 ;; esac
  [ "$total" -gt 0 ] || return 1
  cap=$(mc_num "${TOTAL_BUDGET_GB:-$(mc_cap_gb "$((total / 1073741824))")}" 0 TOTAL_BUDGET_GB)
  [ "$cap" -gt 0 ] && [ "$cap" -le "$((total / 1073741824))" ] || return 1
  free=$(mc_free_pct)
  pressure=$(sysctl -n kern.memorystatus_vm_pressure_level 2>/dev/null) || pressure=0
  case "$pressure" in 1|2|4) ;; *) pressure=0; fault=1 ;; esac
  printf '%s %s %s %s %s\n' "$((cap * 1048576))" "$((AGENT_KB + DOCKER_KB))" \
    "$((total * free / 102400))" "$pressure" "$fault"
  tracked=" ${PROTECTEDPIDS} ${SIMPIDS} ${ORPHANS} "
  # Docker rows are already in tracked_kb. Registered jobs cannot own the VM,
  # but Docker helpers inside an agent tree are included by the propagated set.
  printf '%s\n' "$MC_CAPTURE_SNAPSHOT" | awk -v tracked="$tracked" \
    'NF >= 3 && $1 ~ /^[0-9]+$/ {printf "%s %s %d\n", $1, $3, (index(tracked," "$1" ")>0)}'
}

# Called only by a cancelling supervisor. Python checks a live owner identity,
# explicit cancel flag, group membership, UID and recorded per-PID start time.
# The choke point still excludes agent CLIs and its own ancestry, including the
# supervisor. Fresh authorization is repeated immediately before each signal.
mc_scheduled_allowed() {
  [ -n "${MC_QUEUE_CANCEL_ID:-}" ] && [ -n "${MC_QUEUE_CANCEL_CALLER:-}" ] || return 1
  mc_is_paused && return 1
  "$MC_QUEUE_PYTHON" "$LIB/scheduler.py" authorize "$MC_QUEUE_CANCEL_ID" "$MC_QUEUE_CANCEL_CALLER" "$1" >/dev/null
}

mc_queue_cancel() {
  local pids
  mc_scheduler_config || return 1
  mc_is_paused && return 0
  MC_QUEUE_CANCEL_ID="$1"
  MC_QUEUE_CANCEL_CALLER="$PPID"
  MC_QUEUE_PYTHON=$(mc_scheduler_python) || return 1
  pids=$("$MC_QUEUE_PYTHON" "$LIB/scheduler.py" authorize "$1" "$PPID") || return 1
  # Classification is required even for explicit cancellation: no agent CLI or
  # ancestor becomes a target merely because it appears in a registered group.
  eval "$(mc_ps_snapshot | mc_classify)"
  mc_kill_pids "$pids" 'queued job cancelled by its supervisor' scheduled
}
