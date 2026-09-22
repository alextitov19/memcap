#!/usr/bin/env bash
# Standalone boot deadlines, including jobs owned by older queue supervisors.
# Python only proposes/authorizes. Every signal uses the shared kill choke point.
# jq dollar variables belong to jq, not the shell.
# shellcheck disable=SC2034,SC2016

mc_boot_prepare() {
  local phase=initial
  mc_is_paused && return 1
  [ "${MC_CONFIG_BROKEN:-0}" != 1 ] || return 1
  [ -n "${MC_BOOT_PLAN:-}" ] && [ -n "${MC_BOOT_PYTHON:-}" ] || return 1
  [ "${MC_BOOT_ESCALATING:-0}" = 0 ] || phase=escalate
  MC_BOOT_ALLOWED=$(printf '%s' "$MC_BOOT_PLAN" | "$MC_BOOT_PYTHON" "$LIB/boot_timeout.py" authorize "$phase") || return 1
  [ -n "$MC_BOOT_ALLOWED" ]
}

mc_boot_allowed() {
  case " ${MC_BOOT_ALLOWED:-} " in *" $1 "*) return 0 ;; esac
  return 1
}

mc_boot_notice() {
  local jqbin dir body group
  jqbin=$(mc_feedback_jq) || return 1
  dir="$(mc_state_dir)/job-feedback"
  mkdir -p "$dir" && chmod 700 "$dir" || return 1
  group=$(printf '%s' "$MC_BOOT_PLAN" | "$jqbin" -er '.group') || return 1
  body=$(printf '%s' "$MC_BOOT_PLAN" | "$jqbin" --arg at "$(date +%s)" \
    '{at:$at,pid:(.group|tostring),cwd:.cwd,kind:"boot-timeout",job_id:.job_id,
      message:("memcap: standalone simulator boot timed out after at least 180 seconds. Termination requested for managed job " + .job_id +
      "; this is a simulator preparation failure, not a failed test assertion. The reservation remains until its managed processes exit; simulator services remain measured separately. Read the final task result, check device readiness and other sessions before recovery, and continue independent work. Do not repeatedly retry an unchanged device state, reset a device owned by another session, or bypass memcap.")}') || return 1
  mc_state_write "$dir/$group.json" "$body" || return 1
  mc_feedback_trim "$dir"
}

mc_reap_boot_timeouts() {
  local plans pids
  local MC_BOOT_PLAN MC_BOOT_PYTHON MC_BOOT_ALLOWED MC_BOOT_ESCALATING
  MC_BOOT_RECLAIMED=0
  mc_is_paused && return 0
  [ "${MC_CONFIG_BROKEN:-0}" != 1 ] || return 0
  [ -f "$(mc_state_dir)/queue/jobs.json" ] || return 0
  mc_protection_ready || return 0
  MC_BOOT_PYTHON=$(command -v python3) || return 0
  plans=$("$MC_BOOT_PYTHON" "$LIB/boot_timeout.py" scan) || return 0
  while IFS= read -r MC_BOOT_PLAN; do
    [ -n "$MC_BOOT_PLAN" ] || continue
    MC_BOOT_ESCALATING=0
    mc_boot_prepare || continue
    pids="$MC_BOOT_ALLOWED"
    if mc_kill_pids "$pids" 'simulator boot timeout' boot-timeout; then
      MC_BOOT_RECLAIMED=1
    fi
  done <<EOF_PLANS
$plans
EOF_PLANS
}
