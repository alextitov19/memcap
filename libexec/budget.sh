#!/usr/bin/env bash
# Pure budget math. No side effects, no process inspection -- fully unit testable.
set -uo pipefail

# Memory held back for the human: browser, chat, video calls.
# 35% of total, never below 6 GB, never above 16 GB.
mc_reserve_gb() {
  awk -v t="$1" 'BEGIN{ r=t*0.35; if(r<6)r=6; if(r>16)r=16; printf "%.2f", r }'
}

# Everything agent-related shares what is left. Floored at 40% of the machine so a
# small laptop still gets a usable budget rather than zero. Derives the reserve from
# mc_reserve_gb rather than re-inlining the clamp, so the budget rule lives in exactly
# one place and a future change to 35%/6/16 cannot silently diverge.
mc_cap_gb() {
  local r; r=$(mc_reserve_gb "$1")
  awk -v t="$1" -v r="$r" 'BEGIN{ c=t-r; f=t*0.4; if(c<f)c=f; printf "%d", int(c+0.5) }'
}

# Docker VM ceiling: 40% of the cap within sane absolute bounds, but never so large
# that agents are left with less than 2 GB. Without that last clamp an 8 GB machine
# produces cap 3 / docker 4, i.e. a NEGATIVE agent budget, and `watch` then treats
# agents as permanently over budget and kills a dev server every pass.
# Values at 16/24/32/64 GB are unaffected by the clamp; only small machines move.
mc_docker_gb() {
  awk -v c="$1" 'BEGIN{
    d=c*0.4
    if(d<2)d=2; if(d>12)d=12
    m=c-2; if(d>m)d=m
    if(d<0)d=0
    printf "%d", int(d+0.5) }'
}

# classify.sh folds every sim-classified process's footprint into AGENT_KB as well as
# SIM_KB (sims are agent-adjacent work, so they count toward the combined cap), but tier
# 3 is the only tier that can reclaim that memory -- tier 2 kills dev servers and cannot
# touch a simulator. Triggering tier 2 on the gross figure fires it against an overage it
# is structurally unable to fix, killing a dev server every pass without converging.
# Floored at zero: SIM_KB should never exceed AGENT_KB since classify.sh adds sim
# footprint into both, but a classifier quirk should not produce a negative "net".
mc_agent_net_kb() {
  local agent="${1:-0}" sim="${2:-0}" net
  net=$((agent - sim))
  [ "$net" -lt 0 ] && net=0
  printf '%s' "$net"
}

# Profiles are percentages so they mean the same thing on any machine size.
mc_profile_split() {
  local cap="$1" profile="$2" pct
  case "$profile" in
    stacks)   pct=65 ;;
    mobile)   pct=25 ;;
    balanced|*) pct=40 ;;
  esac
  awk -v c="$cap" -v p="$pct" 'BEGIN{ d=int(c*p/100+0.5); printf "%d %d", d, c-d }'
}
