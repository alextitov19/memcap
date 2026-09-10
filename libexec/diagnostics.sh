#!/usr/bin/env bash
# Host pressure and bounded forensic records. Never selects or signals a process.
set -uo pipefail

mc_host_pressure() {
  local disk swap min_disk max_swap
  MC_HOST_DISK_KB=unknown
  MC_HOST_SWAP_KB=unknown
  MC_HOST_PRESSURE=0
  MC_HOST_FAULT=0
  disk=$(LC_ALL=C df -Pk "$HOME" 2>/dev/null | awk 'NR==2 && $4 ~ /^[0-9]+$/ {print $4}') || disk=""
  case "$disk" in ''|*[!0-9]*) MC_HOST_FAULT=1 ;; *) MC_HOST_DISK_KB="$disk" ;; esac
  # macOS grows swap dynamically. Free space in currently allocated swapfiles
  # being zero is not itself pressure; large USED swap and low disk are signals.
  swap=$(LC_ALL=C sysctl -n vm.swapusage 2>/dev/null | awk '
    function kb(v, u,n) {
      if (v !~ /^[0-9]+([.][0-9]+)?[KMGT]$/) return -1
      u=substr(v,length(v)); n=substr(v,1,length(v)-1)+0
      return n*(u=="K"?1:u=="M"?1024:u=="G"?1048576:1073741824)
    }
    { for(i=1;i<=NF-2;i++) {
        if ($i=="total" && $(i+1)=="=") {total=kb($(i+2)); t=1}
        if ($i=="used" && $(i+1)=="=") {used=kb($(i+2)); u=1}
      }
    }
    END {if(t && u && used>=0 && total>=used) printf "%.0f",used; else exit 1}') || swap=""
  case "$swap" in ''|*[!0-9]*) MC_HOST_FAULT=1 ;; *) MC_HOST_SWAP_KB="$swap" ;; esac
  min_disk=$(mc_num "${HOST_MIN_DISK_GB:-10}" 10 HOST_MIN_DISK_GB)
  max_swap=$(mc_num "${HOST_MAX_SWAP_GB:-8}" 8 HOST_MAX_SWAP_GB)
  MC_HOST_PRESSURE=$(awk -v d="$MC_HOST_DISK_KB" -v s="$MC_HOST_SWAP_KB" -v md="$min_disk" -v ms="$max_swap" \
    'BEGIN {print ((d!="unknown" && md>0 && d<md*1048576) || (s!="unknown" && ms>0 && s>=ms*1048576)) ? 1 : 0}')
  MC_HOST_SUMMARY="disk available $(mc_diag_gb "$MC_HOST_DISK_KB"), swap used $(mc_diag_gb "$MC_HOST_SWAP_KB") (warning thresholds: disk < ${min_disk} GB, swap >= ${max_swap} GB; 0 disables a threshold)"
}

mc_diag_gb() {
  case "$1" in unknown) printf 'unknown' ;; *) printf '%s GB' "$(mc_gb "$1")" ;; esac
}

mc_host_report() {
  if [ "$MC_HOST_FAULT" = 1 ]; then
    mc_log_throttled host-probe "pressure: host measurement unavailable -- $MC_HOST_SUMMARY"
  else
    mc_log_throttle_clear host-probe
  fi
  if [ "$MC_HOST_PRESSURE" = 1 ]; then
    mc_log_throttled host-pressure "pressure: $MC_HOST_SUMMARY -- reduce workload or free disk space; memcap cannot reclaim protected work or Docker memory"
    [ "${MC_DRY_RUN:-0}" = 1 ] || mc_notify "Memory/storage pressure: $MC_HOST_SUMMARY"
  elif [ "$MC_HOST_FAULT" = 0 ]; then
    if [ -f "$(mc_log_throttle_dir)/host-pressure" ]; then
      mc_log "pressure: host warning cleared -- $MC_HOST_SUMMARY"
    fi
    mc_log_throttle_clear host-pressure
  fi
  return 0
}

# Best-effort redaction, then a hard line/size bound. Snapshots are private, not
# suitable for public issue attachments without inspection for sensitive argv.
mc_diag_command() {
  printf '%s\n' "$1" | LC_ALL=C awk '
    {gsub(/[[:cntrl:]]/," ")
     for(i=1;i<=NF;i++) {
       low=tolower($i)
       if(hide) {$i="[redacted]"; hide=0; continue}
       if(low ~ /(^|[-_])(token|password|passwd|secret|api[-_]?key|authorization)(=|$)/) {
         if(index($i,"=")) sub(/=.*/,"=[redacted]",$i); else hide=1
       }
       if ($i ~ /:\/\/[^ \/]+@/) sub(/:\/\/[^ \/]+@/,"://[redacted]@",$i)
     }
     text=$0; if(length(text)>600) text=substr(text,1,295) " ... " substr(text,length(text)-294)
     print text; exit}'
}

mc_diag_ancestry() {
  printf '%s\n' "$2" | awk -v pid="$1" '
    {pp[$1]=$2; exe[$1]=$4}
    END {for(i=0;i<8;i++) {
      pid=pp[pid]
      if(pid<=1) break
      if(!(pid in pp) || seen[pid]++) {printf "unknown"; break}
      printf "%s%s:%s",(i ? " <- " : ""),pid,substr(exe[pid],1,120)
    }
    if(i==8) printf " <- ..."
    }'
}

mc_pressure_capture() {
  local sample="$1" reason="$2" dir now last every slot rows row pid ppid kb cmd identity kind detail body
  dir="$(mc_state_dir)/pressure"
  now=$(date +%s)
  every=$(mc_num "${PRESSURE_SNAPSHOT_SEC:-300}" 300 PRESSURE_SNAPSHOT_SEC)
  last=$(cat "$dir/last" 2>/dev/null) || last=0
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  [ ${#last} -le 12 ] || last=0
  if [ "$now" -ge "$last" ] && [ $((now-last)) -lt "$every" ]; then return 0; fi
  if ! { mkdir -p "$dir" && chmod 700 "$dir"; } 2>/dev/null; then
    mc_state_error "$dir"; return 0
  fi
  slot=$(cat "$dir/next" 2>/dev/null) || slot=0
  case "$slot" in ''|*[!0-9]*) slot=0 ;; esac
  [ ${#slot} -le 2 ] || slot=0
  slot=$((slot % 12))
  # Top twenty from ALL processes, not just the classified pools. Otherwise a
  # detached Python worker remains invisible in the very evidence meant to find it.
  rows=$(printf '%s\n' "$sample" | LC_ALL=C sort -k3,3nr | awk 'NR<=20')
  body="[$(date '+%Y-%m-%d %H:%M:%S')] memcap ${MEMCAP_VERSION:-unknown}: $(mc_diag_command "$reason")
${MC_HOST_SUMMARY:-host pressure not sampled}
measurement: $(mc_measure_summary)
pids/ppids/commands from the budget snapshot; start identity sampled afterwards (PID reuse may race)
"
  while IFS= read -r row; do
    [ -n "$row" ] || continue
    read -r pid ppid kb cmd <<< "$row"
    case "$pid:$ppid:$kb" in *[!0-9:]*|::*|:*) continue ;; esac
    kind=unclassified; detail='outside reclaim scope; ownership unproven'
    case " ${ORPHANS:-} " in *" $pid "*) kind=orphan; detail='root and age checks required' ;; esac
    case " ${SIMPIDS:-} " in *" $pid "*) kind=sim-browser; detail='idle, ownership and mobile checks required' ;; esac
    case " ${PROTECTEDPIDS:-} " in *" $pid "*) kind=agent-protected; detail='live agent tree protected from tiers 1/2' ;; esac
    case " ${AGENTPIDS:-} " in *" $pid "*) kind=agent-cli; detail='never a reclaim target' ;; esac
    if printf '%s\n' "$cmd" | LC_ALL=C awk -v pat="$MC_DOCKER_PATTERN" '$0 ~ pat {found=1} END {exit !found}'; then
      kind=docker; detail='VM ceiling managed separately'
    fi
    identity=$(ps -p "$pid" -o lstart= 2>/dev/null) || identity=unavailable
    [ -n "$identity" ] || identity=unavailable
    body="${body}pid=$pid ppid=$ppid footprint_kb=$kb class=$kind start=$(mc_diag_command "$identity")
  reason=$detail command=$(mc_diag_command "$cmd")
  ancestry=$(mc_diag_ancestry "$pid" "$sample")
"
  done <<EOF_ROWS
$rows
EOF_ROWS
  if mc_state_write "$dir/pressure-$slot.log" "$body"; then
    mc_state_write "$dir/next" "$(((slot+1)%12))" || :
    mc_state_write "$dir/last" "$now" || :
  fi
  return 0
}

mc_pressure_recovered() {
  local file
  file="$(mc_state_dir)/pressure/last"
  [ ! -f "$file" ] || rm -f "$file" 2>/dev/null || :
}

mc_render_diagnostics() {
  local dir latest="" file
  dir="$(mc_state_dir)/pressure"
  for file in "$dir"/pressure-*.log; do
    [ -f "$file" ] || continue
    if [ -z "$latest" ] || [ "$file" -nt "$latest" ]; then latest="$file"; fi
  done
  if [ -z "$latest" ]; then
    printf 'No pressure snapshots recorded. The watcher captures them when host or budget pressure is detected.\n'
    return 0
  fi
  printf 'Latest pressure snapshot: %s\nPrivate process arguments: inspect before sharing.\n\n' "$latest"
  cat "$latest"
}
