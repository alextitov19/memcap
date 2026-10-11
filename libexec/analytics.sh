#!/usr/bin/env bash
# Rare enforcement events only. No process IDs or free-form reasons are emitted.
mc_analytics_signal() {
  local scope="$1" sig="$2" result="$3" targets="$4" ident="$5" count=0 p python
  local job_ref="" args cancel_id="${MC_QUEUE_CANCEL_ID:-}"
  [ -f "$(mc_state_dir)/analytics/enabled" ] || return 0
  python=$(command -v python3) || return 0
  for p in $targets; do [ -n "$p" ] && count=$((count + 1)); done
  args=("_signal" "$scope" "$sig" "$result" "$count" "$ident")
  if [ "$scope" = scheduled ] && [ "${#cancel_id}" -eq 32 ]; then
    case "$cancel_id" in
      *[!a-f0-9]*) ;;
      *) job_ref=$((16#${cancel_id:0:13})); args+=(--job-ref "$job_ref") ;;
    esac
  fi
  "$python" "$LIB/analytics.py" "${args[@]}" >/dev/null 2>&1 || :
}
