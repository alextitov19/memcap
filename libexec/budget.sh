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

# Docker VM ceiling: 40% of the cap, clamped to a sane 4-12 GB.
mc_docker_gb() {
  awk -v c="$1" 'BEGIN{ d=c*0.4; if(d<4)d=4; if(d>12)d=12; printf "%d", int(d+0.5) }'
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
