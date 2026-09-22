#!/usr/bin/env bash
# Retire registered synthetic polling loops, including jobs from older supervisors.
# Python only proposes/authorizes. Every signal uses the shared kill choke point.
# jq dollar variables belong to jq, not the shell.
# shellcheck disable=SC2034,SC2016

mc_poll_prepare() {
  local phase=initial
  mc_is_paused && return 1
  [ "${MC_CONFIG_BROKEN:-0}" != 1 ] || return 1
  [ -n "${MC_POLL_PLAN:-}" ] && [ -n "${MC_POLL_PYTHON:-}" ] || return 1
  [ "${MC_POLL_ESCALATING:-0}" = 0 ] || phase=escalate
  MC_POLL_ALLOWED=$(printf '%s' "$MC_POLL_PLAN" | "$MC_POLL_PYTHON" "$LIB/poll_cleanup.py" authorize "$phase") || return 1
  [ -n "$MC_POLL_ALLOWED" ]
}

mc_poll_allowed() {
  case " ${MC_POLL_ALLOWED:-} " in *" $1 "*) return 0 ;; esac
  return 1
}

mc_poll_notice() {
  local jqbin dir body group
  jqbin=$(mc_feedback_jq) || return 1
  dir="$(mc_state_dir)/job-feedback"
  mkdir -p "$dir" && chmod 700 "$dir" || return 1
  group=$(printf '%s' "$MC_POLL_PLAN" | "$jqbin" -er '.owner') || return 1
  body=$(printf '%s' "$MC_POLL_PLAN" | "$jqbin" --arg at "$(date +%s)" \
    '{at:$at,pid:(.owner|tostring),cwd:.cwd,kind:"poll-cleanup",job_id:.job_id,
      message:("memcap requested retirement of a synthetic waiting-only loop " + .job_id + "; no build/render was canceled by this cleanup. Do not recreate drain ticks or Bash sleep loops. TaskOutput may be unavailable in this Claude build: use memcap wait JOB_ID --timeout 60 with the ORIGINAL workload ID from memcap queue. This waits locally without a reservation. Read the original task result before retrying actual work once. Respect explicit user cancellation.")}') || return 1
  mc_state_write "$dir/$group.json" "$body" || return 1
  mc_feedback_trim "$dir"
}

mc_reap_poll_loops() {
  local plans pids count=0
  local MC_POLL_PLAN MC_POLL_PYTHON MC_POLL_ALLOWED MC_POLL_ESCALATING
  MC_POLL_RECLAIMED=0
  mc_is_paused && return 0
  [ "${MC_CONFIG_BROKEN:-0}" != 1 ] || return 0
  [ -f "$(mc_state_dir)/queue/jobs.json" ] || return 0
  mc_protection_ready || return 0
  MC_POLL_PYTHON=$(command -v python3) || return 0
  plans=$("$MC_POLL_PYTHON" "$LIB/poll_cleanup.py" scan) || return 0
  while IFS= read -r MC_POLL_PLAN; do
    [ -n "$MC_POLL_PLAN" ] || continue
    [ "$count" -lt 8 ] || break
    MC_POLL_ESCALATING=0
    mc_poll_prepare || continue
    pids="$MC_POLL_ALLOWED"
    count=$((count + 1))
    if mc_kill_pids "$pids" 'synthetic polling loop cleanup' poll-cleanup; then
      MC_POLL_RECLAIMED=1
    fi
  done <<EOF_PLANS
$plans
EOF_PLANS
}
