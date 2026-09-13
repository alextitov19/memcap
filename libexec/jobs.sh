#!/usr/bin/env bash
# Oversized live jobs are distinct from orphan/idle cleanup. Their ownership and
# footprint must be proved again immediately before bypassing tree protection.
# Feedback fields below are consumed through mc_kill_pids by feedback.sh.
# shellcheck disable=SC2034
set -uo pipefail

# Strictly executable identity, never a mention of an agent in an argument.
# Output the nearest Claude/Codex ancestor. Missing/cyclic trees fail closed.
# Docker and sims remain governed by their dedicated policies.
mc_job_owner() {
  local pid="$1"
  ps -Ao pid=,ppid=,command= 2>/dev/null | awk -v start="$pid" '
    { p=$1; pp[p]=$2; c=$0; sub(/^[[:space:]]*[0-9]+[[:space:]]+[0-9]+[[:space:]]+/, "", c); cmd[p]=c }
    END {
      c=cmd[start]
      if(c=="" || c ~ /^([^[:space:]]*\/)?(claude|codex)([[:space:]]|$)/) exit 1
      if(c ~ /CoreSimulator|Simulator\.app|launchd_sim|simdiskimaged|qemu-system|ms-playwright|headless_shell|com\.docker|Docker\.app|Virtualization\.framework/) exit 1
      cur=start
      for(i=0;i<32;i++) {
        if(seen[cur]++ || !(cur in pp)) exit 1
        cur=pp[cur]
        if(cur<=1) exit 1
        if(cmd[cur] ~ /^([^[:space:]]*\/)?(claude|codex)([[:space:]]|$)/) {print cur; exit 0}
      }
      exit 1
    }'
}

# Unlike the ranking helper, this MUST NOT fall back to ps RSS. Two direct top
# reads confirm a suspect snapshot even when unrelated rows are missing.
mc_job_footprint_kb() {
  local pid="$1" value
  [ "${MC_NO_TOP:-0}" != 1 ] || return 1
  value=$(top -l 1 -pid "$pid" -stats pid,mem 2>/dev/null |
    awk -v p="$pid" '$1==p {print $2}') || return 1
  [ -n "$value" ] || return 1
  awk -v v="$value" "$MC_TOP_KB_AWK"'BEGIN {k=mc_top_kb(v); if(k<0) exit 1; printf "%d", k}'
}

mc_job_cwd() {
  local cwd
  cwd=$(lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p') || return 1
  [ -n "$cwd" ] || return 1
  (cd "$cwd" && pwd -P) 2>/dev/null
}

# Only the exact revalidated process may use this exception. No unrestricted
# "ignore protection" switch: the ordinary tiers retain full-tree protection.
mc_oversized_allowed() {
  local pid="$1" owner identity
  [ "$pid" = "${MC_JOB_PID:-}" ] || return 1
  [ -n "${MC_JOB_IDENTITY:-}" ] && [ -n "${MC_JOB_OWNER_IDENTITY:-}" ] || return 1
  mc_is_paused && return 1
  [ "${MC_CONFIG_BROKEN:-0}" != 1 ] || return 1
  identity=$(mc_pid_identity "$pid") || return 1
  [ "$identity" = "$MC_JOB_IDENTITY" ] || return 1
  owner=$(mc_job_owner "$pid") || return 1
  [ "$owner" = "${MC_JOB_OWNER:-}" ] || return 1
  identity=$(mc_pid_identity "$owner") || return 1
  [ "$identity" = "$MC_JOB_OWNER_IDENTITY" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  return 0
}

mc_reap_oversized() {
  local sample="$1" limit rows pid kb cmd confirmed second
  local MC_JOB_PID MC_JOB_IDENTITY MC_JOB_OWNER MC_JOB_OWNER_IDENTITY MC_JOB_CWD MC_JOB_KB MC_JOB_LIMIT
  MC_JOBS_RECLAIMED=0
  mc_is_paused && return 0
  [ "${MC_CONFIG_BROKEN:-0}" != 1 ] || return 0
  limit=$(mc_job_limit_gb)
  [ "$limit" -gt 0 ] || return 0
  mc_protection_ready || return 0
  rows=$(printf '%s\n' "$sample" | awk -v limit="$limit" '$3>limit*1048576 {print $1}' )
  for pid in $rows; do
    case "$pid" in ''|*[!0-9]*) continue ;; esac
    case " ${AGENTPIDS} ${SIMPIDS:-} " in *" $pid "*) continue ;; esac
    MC_JOB_PID="$pid"
    MC_JOB_IDENTITY=$(mc_pid_identity "$pid") || continue
    MC_JOB_OWNER=$(mc_job_owner "$pid") || continue
    MC_JOB_OWNER_IDENTITY=$(mc_pid_identity "$MC_JOB_OWNER") || continue
    confirmed=$(mc_job_footprint_kb "$pid") || confirmed=""
    second=$(mc_job_footprint_kb "$pid") || second=""
    case "$confirmed:$second" in *[!0-9:]*|:*|*:)
      mc_log_throttled "job-measure-$pid" "oversized-job: cannot confirm footprint for pid $pid -- no RSS-based kill"
      continue ;;
    esac
    kb=$((limit * 1048576))
    [ "$confirmed" -gt "$kb" ] && [ "$second" -gt "$kb" ] || continue
    mc_oversized_allowed "$pid" || continue
    # This optional directory is only for project-scoped feedback, never proof
    # of process ownership and never a reason to make another process eligible.
    MC_JOB_CWD=$(mc_job_cwd "$pid") || MC_JOB_CWD=""
    MC_JOB_KB="$second"; MC_JOB_LIMIT="$limit"
    cmd=$(mc_filter_protected "$pid" oversized) || continue
    [ -n "${cmd// /}" ] || continue
    mc_log "oversized-job: pid $pid under agent $MC_JOB_OWNER measured $(mc_gb "$second") GB, limit $limit GB -- split this job into smaller batches and reduce parallel workers${MC_DRY_RUN:+ (dry-run=$MC_DRY_RUN)}"
    if mc_kill_pids "$pid" oversized-job oversized; then MC_JOBS_RECLAIMED=1; fi
  done
  return 0
}
