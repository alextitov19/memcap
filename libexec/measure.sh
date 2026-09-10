#!/usr/bin/env bash
# Impure measurement. Isolated here so the classifier stays testable.
set -uo pipefail

# --- Measurement health (contract C4) ----------------------------------------
#
# Every number memcap enforces on comes out of mc_ps_snapshot, and the snapshot
# has a silent failure mode: when `top` cannot be read, every pid falls back to
# `ps` RSS and every total changes underneath the budget with no trace. Measured
# on the author's machine, same instant, same process table:
#
#   footprint (top)     AGENT=6.19  SIM=1.26  DOCKER=6.41  net=4.93  combined=12.60
#   ps RSS (fallback)   AGENT=4.36  SIM=1.50  DOCKER=2.92  net=2.86  combined= 7.28
#
# That is a 42% under-enforcement, and SIM_KB moves the OPPOSITE way to the
# others -- the fallback is not even a consistent bias a user could reason
# about. It happened with no log line and nothing in `status`. These globals
# exist so that state is impossible to be in without saying so.
#
#   MC_MEASURE_DEGRADED  1 when the emitted totals are ps RSS rather than top
#                        footprint to a material degree -- for ANY reason,
#                        including a deliberate MC_NO_TOP=1.
#   MC_MEASURE_FAULT     1 only when that fallback was NOT asked for. This is
#                        the signal to shout about or refuse to enforce on.
#                        MC_NO_TOP=1 is a documented escape hatch, so it sets
#                        DEGRADED without setting FAULT: a warning that fires on
#                        the configuration a user deliberately chose is a
#                        warning they learn to ignore before the real fault
#                        arrives.
#   MC_MEASURE_REASON    none | no-top | mktemp-failed | top-failed | top-empty
#                        | bad-units | missing-rows
#   MC_MEASURE_MISSING   pids that fell back to RSS individually.
#   MC_MEASURE_ROWS      pids measured in total. Always reported, flag or not,
#                        so a consumer can render "12 of 558" without anything
#                        having to trip.
#
# On PARTIAL versus TOTAL degradation: they are NOT the same event and must not
# share a trip point. A pid that started or exited between the `top` and `ps`
# samples legitimately has no top row -- 10 of 558 rows in a live snapshot, a
# 0.03% error on the sum, and it happens on every busy pass. A total fallback is
# a 42% error. Flagging both identically would light the flag permanently, which
# is the same as not having one. So the partial case is MEASURED rather than
# flagged: the raw counts are always published, and the flag trips only once the
# missing fraction passes MEASURE_MISSING_PCT_MAX (10% by default). That is
# scale-free -- ordinary churn stays quiet on any size of machine, while an iOS
# Simulator booting inside the sample window (266 processes appearing at once,
# the case that would enter the total ~2.5x understated) trips it immediately.
MC_MEASURE_DEGRADED=0
MC_MEASURE_FAULT=0
MC_MEASURE_REASON=none
MC_MEASURE_MISSING=0
MC_MEASURE_ROWS=0

# top's memory column, converted to KB. Shared verbatim by mc_ps_snapshot and
# mc_footprint_kb -- it used to be copy-pasted between them, and the copy is
# exactly the kind of thing that gets fixed in one place only.
#
# Returns -1 for a value this table does not recognise, rather than the old
# `else kb = v + 0`. That fallthrough treated an unknown suffix as bare
# kilobytes, which turned "512B" into 512 KB (1024x over) and "1.5T" into 1 KB
# (1.6-billion-x under). Neither appears in today's top output -- 336 K rows and
# 211 M rows on this machine, nothing else -- but a silent 1024x is not a thing
# to leave armed against a future format change. Callers treat -1 as "no usable
# reading", keep ps RSS for that pid, and say so.
MC_TOP_KB_AWK='
function mc_top_kb(v,   u, n) {
  gsub(/[+-]$/, "", v)
  u = substr(v, length(v))
  n = substr(v, 1, length(v) - 1) + 0
  if (u == "K") return n
  if (u == "M") return n * 1024
  if (u == "G") return n * 1024 * 1024
  if (u == "B") return n / 1024
  if (u == "T") return n * 1024 * 1024 * 1024
  return -1
}'

# Where the health of the LAST snapshot is recorded.
#
# The globals above cannot reach a consumer on their own: every call site is
# `eval "$(mc_ps_snapshot | mc_classify)"`, so the function runs in a pipeline
# inside a command substitution -- two subshells deep. Anything it assigns dies
# with them. The file is how the flag survives that; mc_measure_status_load
# reads it back into the same globals in the caller's own shell.
#
# Empty (and every writer below a no-op) when common.sh is not loaded, so
# measure.sh stays sourceable on its own the way the tests source it.
mc_measure_status_file() {
  command -v mc_state_dir >/dev/null 2>&1 || return 0
  printf '%s/measure-status\n' "$(mc_state_dir)"
}

mc_measure_free_marker() {
  command -v mc_state_dir >/dev/null 2>&1 || return 0
  printf '%s/free-pct-unavailable\n' "$(mc_state_dir)"
}

# Non-negative integer or the default. A local shim, not a duplicate policy:
# it defers to mc_num (contract C1) whenever config.sh has been loaded, and only
# covers the case where measure.sh is sourced by itself.
mc_measure_num() {
  if command -v mc_num >/dev/null 2>&1; then mc_num "$1" "$2" "$3"; return 0; fi
  case "$1" in
    ''|*[!0-9]*) printf '%s' "$2" ;;
    *) printf '%s' "$((10#$1))" ;;
  esac
}

# Degradation is a standing condition, not an event: unthrottled, a broken `top`
# would write one line every 60s pass and bury actions.log the way the
# combined-over-cap line once did. Throttled, it is one line per key per window,
# and the always-current truth lives in the status file for `status` to render.
mc_measure_log() {
  if command -v mc_log_throttled >/dev/null 2>&1; then
    mc_log_throttled "$1" "$2"
  elif command -v mc_log >/dev/null 2>&1; then
    mc_log "$2"
  fi
  return 0
}

mc_measure_mark() {
  MC_MEASURE_REASON="$1"
  MC_MEASURE_DEGRADED=1
  MC_MEASURE_FAULT="$2"
}

mc_measure_reset() {
  MC_MEASURE_DEGRADED=0
  MC_MEASURE_FAULT=0
  MC_MEASURE_REASON=none
  MC_MEASURE_MISSING=0
  MC_MEASURE_ROWS=0
}

# The enforcing caller needs this sample's health even when the state volume
# is full. Carry metadata through the same pipe as the sample, not a disk file
# which can silently retain yesterday's healthy result. Never eval metadata.
mc_snapshot_capture() {
  local bundle meta marker
  bundle=$(
    mc_measure_reset
    mc_ps_snapshot || mc_measure_mark top-failed 1
    printf '\nMEMCAP_HEALTH %s %s %s %s %s' "$MC_MEASURE_DEGRADED" "$MC_MEASURE_FAULT" \
      "$MC_MEASURE_REASON" "$MC_MEASURE_MISSING" "$MC_MEASURE_ROWS"
  )
  meta="${bundle##*$'\n'}"
  MC_CAPTURE_SNAPSHOT="${bundle%$'\n'*}"
  read -r marker MC_MEASURE_DEGRADED MC_MEASURE_FAULT MC_MEASURE_REASON MC_MEASURE_MISSING MC_MEASURE_ROWS <<< "$meta"
  if [ "$marker" != MEMCAP_HEALTH ] || [ -z "$MC_CAPTURE_SNAPSHOT" ]; then
    mc_measure_mark top-failed 1
  fi
}

# Written whole, every pass, including the healthy one -- a stale fault record
# left lying around would be read as a current fault long after top recovered.
mc_measure_persist() {
  local f tmp
  f=$(mc_measure_status_file)
  [ -n "$f" ] || return 0
  mkdir -p "$(dirname "$f")" 2>/dev/null || return 0
  tmp="$f.$$"
  if {
    printf 'degraded=%s\n' "$MC_MEASURE_DEGRADED"
    printf 'fault=%s\n' "$MC_MEASURE_FAULT"
    printf 'reason=%s\n' "$MC_MEASURE_REASON"
    printf 'missing=%s\n' "$MC_MEASURE_MISSING"
    printf 'rows=%s\n' "$MC_MEASURE_ROWS"
    printf 'at=%s\n' "$(date +%s)"
  } > "$tmp" 2>/dev/null; then
    mv -f "$tmp" "$f" 2>/dev/null || rm -f "$tmp" 2>/dev/null
  else
    rm -f "$tmp" 2>/dev/null
  fi
  return 0
}

# Consumer side of contract C4: call this after `eval "$(mc_ps_snapshot |
# mc_classify)"` and read the MC_MEASURE_* globals. Resets to healthy when no
# record exists, so a first run before any snapshot does not read as degraded.
mc_measure_status_load() {
  local f k v
  mc_measure_reset
  f=$(mc_measure_status_file)
  [ -n "$f" ] && [ -f "$f" ] || return 0
  while IFS='=' read -r k v; do
    case "$k" in
      degraded) MC_MEASURE_DEGRADED=$(mc_measure_num "$v" 0 degraded) ;;
      fault)    MC_MEASURE_FAULT=$(mc_measure_num "$v" 0 fault) ;;
      missing)  MC_MEASURE_MISSING=$(mc_measure_num "$v" 0 missing) ;;
      rows)     MC_MEASURE_ROWS=$(mc_measure_num "$v" 0 rows) ;;
      reason)
        case "$v" in
          no-top|mktemp-failed|top-failed|top-empty|bad-units|missing-rows) MC_MEASURE_REASON="$v" ;;
          *) MC_MEASURE_REASON=none ;;
        esac
        ;;
    esac
  done < "$f"
  return 0
}

# One human sentence for whatever state the globals are in. Kept here rather
# than in status.sh so the wording of a measurement fault stays with the code
# that can actually produce it.
mc_measure_summary() {
  case "$MC_MEASURE_REASON" in
    none)
      if [ "$MC_MEASURE_MISSING" -gt 0 ]; then
        printf 'top footprint (%s processes; %s fell back to ps RSS, within normal churn)' "$MC_MEASURE_ROWS" "$MC_MEASURE_MISSING"
      else
        printf 'top footprint (%s processes)' "$MC_MEASURE_ROWS"
      fi
      ;;
    no-top)       printf 'ps RSS -- MC_NO_TOP=1 is set, so footprint measurement is off by request' ;;
    mktemp-failed) printf 'ps RSS -- mktemp failed, footprint measurement could not run' ;;
    top-failed)   printf 'ps RSS -- top failed, footprint measurement could not run' ;;
    top-empty)    printf 'ps RSS -- top returned no usable rows, footprint measurement could not run' ;;
    bad-units)    printf 'top footprint, but top reported a unit memcap does not recognise' ;;
    missing-rows) printf 'mixed -- top had no row for %s of %s processes' "$MC_MEASURE_MISSING" "$MC_MEASURE_ROWS" ;;
    *)            printf 'unknown' ;;
  esac
}

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
# entirely when MC_NO_TOP=1 (used by tests, and as an escape hatch) or when the top
# sample cannot be taken at all -- setting MC_MEASURE_* on the way out in every case,
# because the fallback is a 42% change in every total and used to be invisible.
#
# Measured cost: 0.43s per call, versus 0.05s for bare ps. At one call per 60s pass on
# a background-priority daemon, that is not a meaningful trade against correctness.
mc_ps_snapshot() {
  local tmpd raw counts errs st rows seen missing bad sample pct_max reason errline
  mc_measure_reset

  if [ "${MC_NO_TOP:-0}" = "1" ]; then
    # Deliberate, so no log and no fault -- but still degraded: the numbers this
    # returns really are RSS, and a consumer comparing them to the budget needs
    # to know that whether or not anyone chose it.
    mc_measure_mark no-top 0
    mc_measure_persist
    ps -Ao pid=,ppid=,rss=,command=
    return 0
  fi

  # Was `tmp=$(mktemp)`, unchecked. On failure $tmp was empty, the redirect
  # below wrote nothing anywhere, the merge found an empty map, and EVERY pid
  # silently fell back to RSS -- the 42% error, with no way to tell from the
  # outside.
  #
  # `|| tmpd=""` here, and the same shape on every failable command in this
  # file: a bare assignment from a failing command substitution aborts the
  # function outright under a caller that runs `set -e` (bats does), which would
  # turn each of these degradation paths back into a silent absence of numbers.
  tmpd=$(mktemp -d 2>/dev/null) || tmpd=""
  if [ -z "$tmpd" ] || [ ! -d "$tmpd" ]; then
    mc_measure_mark mktemp-failed 1
    mc_measure_log measure-degraded "measure: mktemp -d failed -- falling back to ps RSS for every process this pass. RSS over-counts shared pages ~2.5x on simulators and under-counts compressed memory, so these totals are NOT comparable to the budget (measured elsewhere as a 42% under-count)."
    mc_measure_persist
    ps -Ao pid=,ppid=,rss=,command=
    return 0
  fi

  # Plain rm, not `trap ... RETURN`: bash 3.2 does not scope a RETURN trap to
  # the function that installed it. Reproduced: it fires correctly here, then
  # stays registered and fires AGAIN on the CALLER's own return, where $tmpd is
  # gone and `set -u` kills the shell. Disarming it before every possible exit
  # (including one a future edit might add) is exactly the kind of fragile
  # discipline this change was meant to avoid needing in the first place -- a
  # crashed daemon is a worse failure than the one-file leak on interruption
  # this traded for, and mc_ps_snapshot's only call sites today are all inside
  # `eval "$(mc_ps_snapshot | mc_classify)"`, a command substitution subshell
  # that a real interrupt (Ctrl-C, or nothing at all under `brew services`,
  # which has no terminal) tears down as a whole regardless of what's trapped
  # inside it.
  raw="$tmpd/top"; counts="$tmpd/counts"; errs="$tmpd/top.err"
  # The pipeline as an `if` condition rather than `top ...; st=$?`: with pipefail
  # set (line 3) a failing top makes the whole pipeline fail, which under a
  # `set -e` caller would abort mc_ps_snapshot right here -- the exact silence
  # this function is being changed to end. As a condition, its status is ours to
  # inspect instead of the shell's to act on, and pipefail means an awk that
  # dies is caught too, not just top.
  if top -l 1 -stats pid,mem 2>"$errs" |
    awk '$1 ~ /^[0-9]+$/ { gsub(/[+-]$/, "", $2); print $1, $2 }' > "$raw"
  then st=0; else st=$?; fi
  rows=$(awk 'END{print NR+0}' "$raw" 2>/dev/null) || rows=0
  rows=$(mc_measure_num "$rows" 0 rows)

  if [ "$st" -ne 0 ] || [ "$rows" -eq 0 ]; then
    if [ "$st" -ne 0 ]; then reason=top-failed; else reason=top-empty; fi
    errline=$(head -1 "$errs" 2>/dev/null) || errline=""
    mc_measure_mark "$reason" 1
    mc_measure_log measure-degraded "measure: $reason (top exit $st, $rows usable rows${errline:+; $errline}) -- falling back to ps RSS for every process this pass. RSS over-counts shared pages ~2.5x on simulators and under-counts compressed memory, so these totals are NOT comparable to the budget (measured elsewhere as a 42% under-count)."
    if [ -n "$tmpd" ]; then rm -rf "$tmpd"; fi
    mc_measure_persist
    ps -Ao pid=,ppid=,rss=,command=
    return 0
  fi

  ps -Ao pid=,ppid=,rss=,command= | awk -v mf="$raw" -v cf="$counts" "$MC_TOP_KB_AWK"'
    BEGIN { while ((getline l < mf) > 0) { split(l, a, " "); fp[a[1]] = a[2] } }
    {
      pid=$1; ppid=$2; kb=$3
      cmd=$0; sub(/^[[:space:]]*[0-9]+[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+/, "", cmd)
      seen++
      if (pid in fp) {
        v = mc_top_kb(fp[pid])
        # -1 means the unit table did not recognise the reading. Keep ps RSS for
        # this pid (a wrong-by-1024x footprint is far worse than a known-biased
        # RSS) and hand the offending value back so the log can name it.
        if (v < 0) { bad++; missing++; if (sample == "") sample = fp[pid] }
        else kb = v
      } else missing++
      printf "%s %s %d %s\n", pid, ppid, kb, cmd
    }
    END { printf "%d %d %d %s\n", seen+0, missing+0, bad+0, (sample == "" ? "-" : sample) > cf }'

  seen=0; missing=0; bad=0; sample="-"
  read -r seen missing bad sample < "$counts" 2>/dev/null || true
  seen=$(mc_measure_num "$seen" 0 seen)
  missing=$(mc_measure_num "$missing" 0 missing)
  bad=$(mc_measure_num "$bad" 0 bad)
  MC_MEASURE_ROWS="$seen"
  MC_MEASURE_MISSING="$missing"

  if [ "$bad" -gt 0 ]; then
    # Even one is a fault, regardless of count: it means top's format is not the
    # one this code was written against, and every reading is suspect.
    mc_measure_mark bad-units 1
    mc_measure_log measure-bad-units "measure: top reported $bad memory value(s) with a unit memcap does not recognise (e.g. '$sample') -- those processes kept ps RSS. top's output format has changed; mc_top_kb in libexec/measure.sh needs the new unit."
  elif [ "$seen" -gt 0 ]; then
    pct_max=$(mc_measure_num "${MEASURE_MISSING_PCT_MAX:-10}" 10 MEASURE_MISSING_PCT_MAX)
    if [ $((missing * 100)) -gt $((seen * pct_max)) ]; then
      mc_measure_mark missing-rows 1
      mc_measure_log measure-degraded "measure: top had no row for $missing of $seen processes (over the ${pct_max}% churn threshold) -- those fell back to ps RSS, so the totals this pass mix two metrics and under-state shared memory. A simulator or container starting inside the sample window looks like this."
    fi
  fi

  if [ -n "$tmpd" ]; then rm -rf "$tmpd"; fi
  mc_measure_persist
}

# Physical footprint for ONE pid, via the same top-based measurement as
# mc_ps_snapshot (unit conversion is literally the same awk function, MC_TOP_KB_AWK
# -- see that function's comment for why footprint, not RSS, is the metric
# everything downstream is built on). Used by tier 2 to rank kill candidates by
# the same measure that decided agents were over budget in the first place,
# rather than summed ps RSS, which over-counts shared pages ~2.5x and can rank
# the wrong process as the victim. Falls back to ps RSS -- same fallback
# mc_ps_snapshot itself uses -- when top has no row for the pid, or reports it in
# a unit this code does not recognise.
mc_footprint_kb() {
  local pid="$1" v kb
  if [ "${MC_NO_TOP:-0}" != "1" ]; then
    v=$(top -l 1 -pid "$pid" -stats pid,mem 2>/dev/null | awk -v p="$pid" '$1==p{print $2}') || v=""
    if [ -n "$v" ]; then
      kb=$(awk -v v="$v" "$MC_TOP_KB_AWK"'BEGIN{ k = mc_top_kb(v); if (k < 0) exit 1; printf "%d", k }') || kb=""
      if [ -n "$kb" ]; then
        printf '%s' "$kb"
        return 0
      fi
      mc_measure_log measure-bad-units "measure: top reported '$v' for pid $pid with a unit memcap does not recognise -- ranking it by ps RSS instead. mc_top_kb in libexec/measure.sh needs the new unit."
    fi
  fi
  # An explicit success: a pid that exited mid-rank makes ps exit non-zero, and
  # with pipefail that would be this function's status -- enough to abort tier 2
  # under a `set -e` caller over a process that is already gone. The empty
  # output callers already handle says the same thing without the collateral.
  ps -o rss= -p "$pid" 2>/dev/null | tr -d ' '
  return 0
}

# Percentage of physical RAM that is free-ish (free + inactive + speculative +
# purgeable). This is the ONE pressure signal grounded in actual physical memory
# rather than footprint arithmetic, which makes it the authority when the budget
# math has missed something -- and it used to print a hardcoded 100 whenever it
# could not do its job.
#
# The old version ran `"sysctl -n hw.memsize" | getline total` inside awk and
# ended with `(total > 0 ? ... : 100)`. With sysctl unreachable, total stayed
# unset and it returned a confident 100 -- verified against a real value of 17.
# enforce.sh's `[ "$free" -lt "${MIN_FREE_PCT:-15}" ]` can then never fire, so
# tier 1's low-memory trigger is dead and nothing says so: a fail-OPEN constant
# on the last line of defence.
#
# Now it fails CLOSED and loud. An unmeasurable machine reports 0% free, which
# trips the low-memory path (mc_reap_orphans -- processes whose parent is
# already dead, the safest thing memcap does), logs, and leaves a marker file
# `status` can surface. Wrongly reaping an orphan is recoverable; silently
# disabling the pressure trigger is what this audit found.
mc_free_pct() {
  local total pct marker
  marker=$(mc_measure_free_marker)

  total=$(sysctl -n hw.memsize 2>/dev/null) || total=""
  case "$total" in
    ''|*[!0-9]*) total=0 ;;
  esac

  if [ "$total" -gt 0 ]; then
    pct=$(vm_stat 2>/dev/null | awk -v total="$total" '
      /page size of/ { for(i=1;i<=NF;i++) if ($i+0>1000) ps=$i+0 }
      /Pages free/        { gsub(/\./,"",$3); f=$3;  seen=1 }
      /Pages inactive/    { gsub(/\./,"",$3); ia=$3; seen=1 }
      /Pages speculative/ { gsub(/\./,"",$3); sp=$3; seen=1 }
      /Pages purgeable/   { gsub(/\./,"",$3); pu=$3; seen=1 }
      END { if (!seen) exit 1
            if (!ps) ps=16384
            printf "%d", (f+ia+sp+pu)*ps*100/total }') || pct=""
    case "$pct" in
      ''|*[!0-9]*) pct="" ;;
    esac
    if [ -n "$pct" ]; then
      if [ "$pct" -gt 100 ]; then pct=100; fi
      if [ -n "$marker" ]; then rm -f "$marker" 2>/dev/null; fi
      printf '%d' "$pct"
      return 0
    fi
    mc_measure_log free-pct-unavailable "measure: vm_stat produced no usable page counts -- reporting 0% free so tier 1's low-memory trigger fails CLOSED rather than reading a confident 100 forever."
  else
    mc_measure_log free-pct-unavailable "measure: sysctl -n hw.memsize is unavailable, so free memory cannot be computed -- reporting 0% free so tier 1's low-memory trigger fails CLOSED rather than reading a confident 100 forever. Check that /usr/sbin is on the daemon's PATH."
  fi

  if [ -n "$marker" ]; then
    mkdir -p "$(dirname "$marker")" 2>/dev/null && : > "$marker" 2>/dev/null
  fi
  printf '0'
  # Deliberately 0, not a non-zero status. Every caller reads this through
  # `free=$(mc_free_pct)`, where a non-zero status aborts the whole enforcement
  # pass under any `set -e` caller -- trading a dead trigger for a dead daemon.
  # The 0% reading, the log line and the marker file already say it loudly,
  # three ways, without being able to take anything down.
  return 0
}
