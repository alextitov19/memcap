#!/usr/bin/env bash
# Impure measurement. Isolated here so the classifier stays testable.
set -uo pipefail

# Emits `pid ppid kb command`, where kb is PHYSICAL FOOTPRINT, not ps RSS.
#
# This matters more than it looks. Summing ps RSS across a process tree that shares
# memory counts the shared pages once per process: measured on a real machine, a booted
# iOS Simulator's 266 processes summed to 16.18 GB of RSS against 6.42 GB of actual
# footprint -- a 2.5x over-count. Feeding that to the budget would make the watchdog
# believe agents were far over budget and kill a dev server on every pass.
#
# One `top` sample supplies footprint for every process; ps supplies the structure
# (ppid and command) that top does not give usefully. Merging them makes every
# downstream total footprint-based, which is why no per-process correction is needed
# anywhere else. Falls back to per-process RSS when top has no row for a pid, and
# entirely when MC_NO_TOP=1 (used by tests, and as an escape hatch).
#
# Measured cost: 0.43s per call, versus 0.05s for bare ps. At one call per 60s pass on
# a background-priority daemon, that is not a meaningful trade against correctness.
mc_ps_snapshot() {
  local tmp
  if [ "${MC_NO_TOP:-0}" = "1" ]; then
    ps -Ao pid=,ppid=,rss=,command=
    return 0
  fi
  tmp=$(mktemp)
  top -l 1 -stats pid,mem 2>/dev/null |
    awk '$1 ~ /^[0-9]+$/ { gsub(/[+-]$/, "", $2); print $1, $2 }' > "$tmp"
  ps -Ao pid=,ppid=,rss=,command= | awk -v mf="$tmp" '
    BEGIN { while ((getline l < mf) > 0) { split(l, a, " "); fp[a[1]] = a[2] } }
    {
      pid=$1; ppid=$2; kb=$3
      cmd=$0; sub(/^[[:space:]]*[0-9]+[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+/, "", cmd)
      if (pid in fp) {
        v=fp[pid]; u=substr(v, length(v)); n=substr(v, 1, length(v)-1)+0
        if (u=="G") kb=n*1024*1024
        else if (u=="M") kb=n*1024
        else if (u=="K") kb=n
        else kb=v+0
      }
      printf "%s %s %d %s\n", pid, ppid, kb, cmd
    }'
  rm -f "$tmp"
}

mc_free_pct() {
  vm_stat | awk '
    /page size of/ { for(i=1;i<=NF;i++) if ($i+0>1000) ps=$i+0 }
    /Pages free/        { gsub(/\./,"",$3); f=$3 }
    /Pages inactive/    { gsub(/\./,"",$3); ia=$3 }
    /Pages speculative/ { gsub(/\./,"",$3); sp=$3 }
    /Pages purgeable/   { gsub(/\./,"",$3); pu=$3 }
    END { if(!ps) ps=16384
          "sysctl -n hw.memsize" | getline total
          printf "%d", (total>0 ? (f+ia+sp+pu)*ps*100/total : 100) }'
}
