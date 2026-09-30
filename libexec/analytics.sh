#!/usr/bin/env bash
# Rare enforcement events only. No process IDs or free-form reasons are emitted.
mc_analytics_signal() {
  local scope="$1" sig="$2" result="$3" targets="$4" ident="$5" count=0 p python
  [ -f "$(mc_state_dir)/analytics/enabled" ] || return 0
  python=$(command -v python3) || return 0
  for p in $targets; do [ -n "$p" ] && count=$((count + 1)); done
  "$python" "$LIB/analytics.py" _signal "$scope" "$sig" "$result" "$count" "$ident" >/dev/null 2>&1 || :
}
