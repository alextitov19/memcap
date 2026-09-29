#!/usr/bin/env bash
# Only this bridge requests signals; the planner never signals or releases leases.
# shellcheck disable=SC2034
mc_claims_prepare() {
  MC_CLAIM_PROTECTED=""
  [ -f "$(mc_state_dir)/queue/jobs.json" ] || return 0
  local python
  python=$(command -v python3) || return 1
  MC_CLAIM_PROTECTED=$("$python" "$LIB/orphan_recovery.py" protected) || return 1
}

mc_orphan_prepare() {
  local phase=initial
  mc_is_paused && return 1
  [ "${MC_CONFIG_BROKEN:-0}" != 1 ] || return 1
  [ -n "${MC_ORPHAN_PLAN:-}" ] && [ -n "${MC_ORPHAN_PYTHON:-}" ] || return 1
  [ "${MC_ORPHAN_ESCALATING:-0}" = 0 ] || phase=escalate
  MC_ORPHAN_ALLOWED=$(printf '%s' "$MC_ORPHAN_PLAN" | "$MC_ORPHAN_PYTHON" "$LIB/orphan_recovery.py" authorize "$phase") || return 1
  [ -n "$MC_ORPHAN_ALLOWED" ]
}

mc_orphan_allowed() {
  case " ${MC_ORPHAN_ALLOWED:-} " in *" $1 "*) return 0 ;; esac
  return 1
}

mc_recover_orphans() {
  local plans pids MC_ORPHAN_PLAN MC_ORPHAN_PYTHON MC_ORPHAN_ALLOWED MC_ORPHAN_ESCALATING
  MC_ORPHAN_RECLAIMED=0
  mc_is_paused && return 0
  [ "${MC_CONFIG_BROKEN:-0}" != 1 ] || return 0
  [ -f "$(mc_state_dir)/queue/jobs.json" ] || return 0
  mc_protection_ready || return 0
  MC_ORPHAN_PYTHON=$(command -v python3) || return 0
  plans=$("$MC_ORPHAN_PYTHON" "$LIB/orphan_recovery.py" scan) || return 0
  while IFS= read -r MC_ORPHAN_PLAN; do
    [ -n "$MC_ORPHAN_PLAN" ] || continue
    MC_ORPHAN_ESCALATING=0
    mc_orphan_prepare || continue
    pids="$MC_ORPHAN_ALLOWED"
    if mc_kill_pids "$pids" 'abandoned development helper' orphan-recovery; then
      MC_ORPHAN_RECLAIMED=1
    fi
  done <<EOF_PLANS
$plans
EOF_PLANS
}
