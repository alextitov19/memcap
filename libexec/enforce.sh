#!/usr/bin/env bash
set -uo pipefail

MC_DRY_RUN="${MC_DRY_RUN:-0}"

# --- Numeric knobs (contract C1) ---------------------------------------------
# Every number read from the config reaches either `[ -lt ]` or `$(( ))`, and
# both fail OPEN on garbage: `[ 1000 -lt "5m" ]` returns status 2, and because
# every gate in this file sits to the LEFT of an `&&`, a status-2 comparison does
# not fail the gate -- it makes the gate evaporate. TIER2_MIN_AGE_SEC="5m" is
# therefore not "an unusually long minimum age", it is NO minimum age, and a
# process one second old becomes a kill target.
#
# mc_num (config.sh) is the sanctioned reader. The guard exists because
# enforce.sh is also sourced on its own -- by a unit test, or by any caller that
# wants only the tier functions -- where config.sh may not have been loaded.
# Same idiom, and the same reasoning, as roots.sh's mc_roots_num.
mc_enf_num() {
  if command -v mc_num >/dev/null 2>&1; then
    mc_num "$1" "$2" "$3"
    return
  fi
  case "$1" in
    ''|*[!0-9]*) printf '%s' "$2" ;;
    *) [ ${#1} -le 18 ] && printf '%s' "$((10#$1))" || printf '%s' "$2" ;;
  esac
}

# SOFT_TRIGGER is the one knob that is a ratio rather than a count, and it
# reaches awk rather than `[`. awk coerces a non-number to 0, so `agents >
# budget*0` is true for any live agent and the soft trigger fires on EVERY pass
# instead of never -- the same class of defect as mc_enf_num's, opposite sign.
mc_enf_frac() {
  if command -v mc_frac >/dev/null 2>&1; then
    mc_frac "$1" "$2" "$3"
    return
  fi
  case "$1" in
    ''|.|*[!0-9.]*|*.*.*) printf '%s' "$2" ;;
    *) printf '%s' "$1" ;;
  esac
}

# --- Liveness (contract C6) ---------------------------------------------------
# mc_pid_alive lives in common.sh, which bin/memcap sources before this file.
# There is deliberately no local copy or fallback here: two definitions of a
# liveness test is how the codebase got one that was wrong.
#
# Why this matters more than any other line in this file: `kill -0` conflates
# EPERM ("alive, but not yours to signal") with ESRCH ("dead"). simdiskimaged is
# root-owned, is listed in MC_SIM_EXE, and lands in SIMPIDS on every pass on any
# Mac with Xcode installed. Every pass, the old `kill -0` liveness test declared
# it dead, the prune deleted its idle stamp, the tracking loop re-saw it as
# never-tracked, and the reap was blocked -- through an unlogged return. Eleven
# days of production logs: 1,973 tier-3 declines, zero reclaims, all of it caused
# by a process that is not even in MC_SIM_KILL_PATTERN and could never have been
# killed if it had been selected.
#
# `kill -0` still appears once in this file, in mc_sim_is_target, as a PERMISSION
# test -- the one question it answers well.

# --- The protection filter ----------------------------------------------------
# Every pid memcap kills passes through mc_kill_pids, so the protection filter
# lives here rather than in one tier. A tier-2 subtree walk is role-blind: if a
# dev server has an agent CLI anywhere beneath it -- an ordinary shape in agentic
# workflows -- a naive walk would kill the agent along with the server.

# Fails CLOSED. The old version broke out of the walk on any empty `ps` output,
# which silently reduced the protected set to `$$` alone -- so a `ps` that was
# failing (a full process table, a sandbox, a PATH without /bin) would quietly
# strip memcap's own ancestry out of the protection filter at exactly the moment
# the machine was under enough pressure for a tier to be firing. A walk that
# cannot complete returns 1 and the caller refuses to kill anything.
mc_self_ancestry() {
  local p="$$" out=" $$ " i=0 rc
  while [ "$i" -lt 32 ]; do
    p=$(ps -o ppid= -p "$p" 2>/dev/null)
    rc=$?
    p="${p//[[:space:]]/}"
    [ "$rc" -ne 0 ] && return 1
    case "$p" in ''|*[!0-9]*) return 1 ;; esac
    [ "$p" = "0" ] && break
    [ "$p" = "1" ] && break
    out="$out$p "
    i=$((i + 1))
  done
  # 32 levels without reaching init is not a process tree, it is a bug or a
  # cycle. Refuse rather than truncate the ancestry and call it protected.
  [ "$i" -lt 32 ] || return 1
  printf '%s' "$out"
  return 0
}

# `${AGENTPIDS:-}` was a fail-open default ON THE PROTECTION FILTER ITSELF: if
# classification had not run, or its awk died, the filter protected nothing and
# every tier killed freely. Unset (not merely empty) means "no classification
# happened", which is never a licence to kill.
mc_protection_ready() {
  [ -n "${AGENTPIDS+x}" ] || return 1
  [ -n "${PROTECTEDPIDS+x}" ] || return 1
  return 0
}

# --- The tooling invariant ----------------------------------------------------
# THE RULE: memcap must never reap a process its own vetoes count as evidence of
# active work. A process cannot simultaneously be proof that a human is working
# and be reclaimable garbage.
#
# This is not a special case for maestro. It is the general statement of a defect
# that was about to ship: MC_SIM_KILL_PATTERN matches `\.maestro/lib`, and
# mc_active_mobile_tooling matches the same string as evidence that a simulator
# is being driven. With tier 3 unblocked, the author's own maestro MCP servers
# (long-lived, idle by design, registered in a Claude Code config) were selected
# as kill targets in the same pass in which they were being counted as active
# work. Any future veto whose evidence overlaps a kill pattern would reproduce
# it, so the rule is enforced once, at the choke point, over whatever set the
# veto matchers currently return -- not by editing one pattern to exclude
# another.
#
# Computed at most once per pass and cached: mc_watch clears the cache at the top
# of each pass, so every tier in one pass sees the same snapshot, and a `memcap
# clean` (a fresh process) computes it once.
#
# Populated by mc_veto_evidence_warm, which must be called from the CURRENT shell
# -- a `$(mc_veto_evidence_pids)` substitution runs in a subshell, so the cache it
# fills dies with it and the four pgreps run again on the next call. Tier 3 asks
# this question twice per ready pid, and this machine produced 239 distinct
# sim-classified pids in 9.5 minutes; uncached, that is thousands of pgreps a pass.
mc_veto_evidence_warm() {
  # The override is re-read rather than cached, so a caller that sets it partway
  # through a process (a test) is not answered from a stale snapshot.
  if [ -n "${MC_VETO_EVIDENCE_PIDS+x}" ]; then
    MC_VETO_EVIDENCE_CACHE=" $MC_VETO_EVIDENCE_PIDS "
    return 0
  fi
  [ -n "${MC_VETO_EVIDENCE_CACHE+x}" ] && return 0
  MC_VETO_EVIDENCE_CACHE=" $(mc_mobile_tooling_pids) $(mc_hands_on_mobile_pids) "
  return 0
}

mc_veto_evidence_pids() {
  mc_veto_evidence_warm
  printf '%s' "$MC_VETO_EVIDENCE_CACHE"
  return 0
}

# --- Evidence, second form: a resource HELD by a live server ------------------
# The flat evidence set above answers "is this pid itself a tool memcap vetoes
# on". It is too narrow, and the process tree says why:
#
#   41308 Google Chrome                        <- sim-classified, CPU-flat
#    41307 node .../playwright-mcp
#     41263 npm exec @playwright/mcp@latest     <- MCP server
#      93098 claude                             <- live agent session
#
# Seven "idle Chrome processes" are ONE browser an MCP server is holding open
# across requests. That is the maestro defect in a new place: a long-lived server
# registered in the user's agent config, holding a resource that is idle BY
# DESIGN between calls. Ten minutes of CPU-flatness while the agent reads code is
# entirely ordinary, and the reap is only discovered when the next
# browser_navigate fails. Shipping the tier-3 unblock without this would have
# made its first production act breaking the user's Playwright tooling, in the
# same release that stopped it breaking their maestro tooling.
#
# The rule is structural, one layer up from a vendor list -- refusing to
# enumerate `@playwright/mcp` and `chrome-devtools-mcp` for exactly the reason
# this file refuses to enumerate exclusions into MC_SIM_KILL_PATTERN. It matches
# the PROTOCOL as an argv token: `@playwright/mcp`, `chrome-devtools-mcp`,
# `mcp-server-filesystem` and `@modelcontextprotocol/server-*` all satisfy the
# same bounded-token rule, and so will the next one nobody has written yet.
# Bounded on both sides by non-alphanumerics, so `memcap` does not match itself.
MC_HELD_SERVER_PATTERN='(^|[^[:alnum:]])mcp([^[:alnum:]]|$)|modelcontextprotocol'

# One `ps` of the whole table per pass, for the ancestry walks below.
mc_proc_table_warm() {
  [ -n "${MC_PROC_TABLE+x}" ] && return 0
  MC_PROC_TABLE=$(ps -Ao pid=,ppid=,command= 2>/dev/null)
  return 0
}

# True when PID's ancestry passes through a live server-shaped process and then
# reaches a live agent session.
#
# BOTH halves are required, and the second is what keeps tier 3 from going inert
# again: a walk that hits ppid 1 without reaching an agent session is a resource
# whose owner is DEAD -- the leaked browser, the abandoned simulator. That is the
# population this tier exists for, and on a machine that has been running agents
# all day it is not empty. Reaching it is the whole point of the narrower `sims`
# protection scope; this rule narrows what that scope exposes without closing it.
mc_held_by_agent_server() {
  local pid="$1" verdict rest
  MC_HELD_BY_PID=""
  MC_HELD_BY_CMD=""
  # Fail closed: with no classification there is no way to tell a held resource
  # from a leaked one, and the wrong guess here kills a live browser.
  [ -n "${AGENTPIDS+x}" ] || return 0
  mc_veto_evidence_warm
  mc_proc_table_warm
  # An ancestor counts as a holder if it is server-shaped OR is already in the
  # flat evidence set -- a simulator launched by a running xcodebuild or maestro
  # flow is held by that run just as a browser is held by an MCP server. The two
  # forms of evidence compose rather than sitting side by side.
  verdict=$(printf '%s\n' "$MC_PROC_TABLE" | awk -v start="$pid" -v agents=" $AGENTPIDS " \
      -v evid="$MC_VETO_EVIDENCE_CACHE" -v sims=" ${SIMPIDS:-} " -v srv="$MC_HELD_SERVER_PATTERN" '
    {
      p=$1; pp=$2; cmd=$0
      sub(/^[[:space:]]*[0-9]+[[:space:]]+[0-9]+[[:space:]]+/, "", cmd)
      PP[p]=pp; C[p]=cmd
    }
    END {
      cur=start; holder=""; holdercmd=""
      # Bounded like every other ancestry walk in this file: a tree deeper than
      # this is a bug or a cycle, not a process tree.
      for (i=0; i<32; i++) {
        pp = PP[cur]
        if (pp == "" || pp == "0" || pp == "1") break
        if (index(agents, " " pp " ") > 0) {
          # Reached a live session. Held only if something on the way up was
          # actually holding this open.
          # Emitted whole; the caller abbreviates with mc_abbrev, which keeps
          # both ends. Truncating here would lose the tail -- the same mistake
          # `cut -c1-160` made on kill records, where the argument naming the
          # actual script never survived.
          if (holder != "") { print "held " holder " " holdercmd; exit }
          break
        }
        # Keep the INNERMOST holder: it is the process that actually owns the
        # resource, and naming `npm exec` two levels up would be less use.
        #
        # But never name a sim-classified process as the holder. A Chrome helper
        # IS held by Chrome, and on this machine Chrome matches the token itself
        # because its profile directory is `mcp-chrome-7ac0193` -- so the honest
        # walk produced "pid 41317 is held by pid 41308", which is true, useless,
        # and would send whoever reads it looking at the wrong process. Skipping
        # them walks on to the server that actually owns the browser.
        if (holder == "" && index(sims, " " pp " ") == 0 &&
            (C[pp] ~ srv || index(evid, " " pp " ") > 0)) {
          holder = pp; holdercmd = C[pp]
        }
        cur = pp
      }
      print "free"
    }')
  case "$verdict" in
    "held "*)
      rest="${verdict#held }"
      MC_HELD_BY_PID="${rest%% *}"
      MC_HELD_BY_CMD="${rest#* }"
      return 0
      ;;
  esac
  return 1
}

# The single question every kill path asks. MC_EVIDENCE_REASON carries which of
# the two rules answered, so the log line can say something useful rather than
# just "protected".
mc_pid_is_evidence() {
  local pid="$1"
  MC_EVIDENCE_REASON=""
  mc_veto_evidence_warm
  case "$MC_VETO_EVIDENCE_CACHE" in
    *" $pid "*)
      MC_EVIDENCE_REASON="memcap's own mobile-tooling veto counts it as active work"
      return 0
      ;;
  esac
  if mc_held_by_agent_server "$pid"; then
    MC_EVIDENCE_REASON="it is held open by live server pid $MC_HELD_BY_PID ($(mc_abbrev "$MC_HELD_BY_CMD" 160)) under an agent session -- a held resource is idle because it is WAITING, not because it is garbage"
    return 0
  fi
  return 1
}

# Two scopes, because tiers 1/2 and tier 3 are protecting against different
# mistakes. Returns non-zero when the protected set is unknown, so mc_kill_pids
# can refuse outright rather than kill through a filter that was never populated.
#
#   full  (default; tiers 1 and 2) -- PROTECTEDPIDS, the whole propagated agent
#         tree (contract C3), plus everything the `sims` scope covers. Tiers 1
#         and 2 select their victim by INFERENCE ("this looks like a leaked dev
#         server"), and the inference is what goes wrong: six of the ten real
#         tier-2 kills were chrome-devtools-mcp telemetry watchdogs running as
#         grandchildren of a live claude session. Anything under a session is off
#         limits to a guess.
#
#   sims  (tier 3) -- the agent CLI processes themselves (AGENTPIDS), memcap's
#         own ancestry, and veto evidence. NOT the whole tree.
#
# The `sims` scope is a deliberate, measured decision, not an oversight. Applying
# the full tree to tier 3 leaves tier 3 exactly as dead as the kill -0 bug did:
# on this machine every single reclaimable sim pid -- seven Chrome processes,
# five Playwright headless shells -- is a descendant of a live agent session,
# because that is how they are launched. Full-tree protection would have shipped
# a tier-3 "fix" that still reclaims nothing, for a new reason.
#
# What makes the narrower scope safe is that tier 3 does not infer. It acts on
# MC_SIM_KILL_PATTERN (a narrow, explicit list of browser and emulator binaries
# that can never match an agent CLI), on measured CPU flatness across the whole
# grace, and on the veto evidence set. An agent actually driving a browser burns
# CPU and resets its clock; an agent that launched one twenty minutes ago and
# forgot it is precisely the memory this tier exists to reclaim, and "a session
# is alive" was already rejected as a proxy for "this simulator is in use" --
# that blanket veto fired zero times in 1,643 real passes.
mc_filter_protected() {
  local pid out="" self scope="${2:-full}"
  mc_protection_ready || return 1
  self=$(mc_self_ancestry) || return 1
  mc_veto_evidence_warm
  if [ "$scope" = poll-cleanup ]; then
    command -v mc_poll_prepare >/dev/null 2>&1 || return 1
    mc_poll_prepare || return 1
    for pid in $MC_POLL_ALLOWED; do
      case " ${AGENTPIDS} $self " in *" $pid "*) return 1 ;; esac
    done
  fi
  if [ "$scope" = boot-timeout ]; then
    command -v mc_boot_prepare >/dev/null 2>&1 || return 1
    mc_boot_prepare || return 1
    # Never cancel only the children of a protected shell and let its remaining
    # script continue after a failed boot. The whole planned attempt must qualify.
    for pid in $MC_BOOT_ALLOWED; do
      case " ${AGENTPIDS} $self " in *" $pid "*) return 1 ;; esac
    done
  fi
  if [ "$scope" = idle-gc ]; then
    command -v mc_gc_prepare >/dev/null 2>&1 || return 1
    mc_gc_prepare "$1" || return 1
  fi
  for pid in $1; do
    if [ "$scope" = poll-cleanup ]; then
      mc_poll_allowed "$pid" || continue
      case " ${AGENTPIDS} $self " in *" $pid "*) continue ;; esac
      out="$out $pid"
      continue
    fi
    if [ "$scope" = boot-timeout ]; then
      mc_boot_allowed "$pid" || continue
      case " ${AGENTPIDS} $self " in *" $pid "*) continue ;; esac
      out="$out $pid"
      continue
    fi
    if [ "$scope" = idle-gc ]; then
      command -v mc_gc_allowed >/dev/null 2>&1 || continue
      mc_gc_allowed "$pid" || continue
      case " ${AGENTPIDS} $self " in *" $pid "*) continue ;; esac
      out="$out $pid"
      continue
    fi
    if [ "$scope" = scheduled ]; then
      command -v mc_scheduled_allowed >/dev/null 2>&1 || continue
      mc_scheduled_allowed "$pid" || continue
      case " ${AGENTPIDS} $self " in *" $pid "*) continue ;; esac
      out="$out $pid"
      continue
    fi
    if [ "$scope" = oversized ]; then
      command -v mc_oversized_allowed >/dev/null 2>&1 || continue
      mc_oversized_allowed "$pid" || continue
      case " ${AGENTPIDS} $self " in *" $pid "*) continue ;; esac
      out="$out $pid"
      continue
    fi
    if [ "$scope" != "sims" ]; then
      case " ${PROTECTEDPIDS} " in *" $pid "*) continue ;; esac
    fi
    case " ${AGENTPIDS} " in *" $pid "*) continue ;; esac
    case "$self" in *" $pid "*) continue ;; esac
    if mc_pid_is_evidence "$pid"; then
      mc_log_throttled "kill-skip-evidence" "protection: not reaping pid $pid -- $MC_EVIDENCE_REASON. A process cannot be both evidence of work and reclaimable garbage."
      continue
    fi
    out="$out $pid"
  done
  printf '%s' "$out"
  return 0
}

# --- Kill records -------------------------------------------------------------
# `cut -c1-160` truncated mid-token. On this machine 376 real kill records
# collapsed to 4 distinct strings, because the node binary path plus the
# `--require` shim consume the whole budget before the argument that names the
# script actually being killed. The tail is the informative half, so keep both
# ends and elide the middle.
MC_KILL_RECORD_MAX=400

mc_abbrev() {
  local s="$1" max="${2:-$MC_KILL_RECORD_MAX}" half
  [ "${#s}" -le "$max" ] && { printf '%s' "$s"; return 0; }
  half=$(( (max - 5) / 2 ))
  printf '%s ... %s' "${s:0:$half}" "${s:$(( ${#s} - half ))}"
}

# Identity of a pid as it exists RIGHT NOW: start time plus command line. `kill
# -0` after a sleep answers "is A process alive at this number", not "is it the
# one I signalled" -- and pid numbers are reused. Measured here: 135 pids
# allocated per 2-second idle window against a pid space of ~99999, so a
# 388-orphan sweep carries roughly half an expected SIGKILL delivered to an
# innocent bystander.
mc_pid_identity() {
  ps -o lstart=,command= -p "$1" 2>/dev/null | tr -s '[:space:]' ' '
}

mc_identity_lookup() {
  local want="$1" line
  while IFS= read -r line; do
    case "$line" in
      "$want|"*) printf '%s' "${line#*|}"; return 0 ;;
    esac
  done <<EOF
$2
EOF
  return 1
}

# Return status signals whether a real kill happened: 0 only when pids were
# actually sent a signal. Dry run, "nothing left after protection" and "the
# protected set is unknown" all return 1, so a caller that gates a "killed"
# notification on this cannot claim one that never happened.
mc_kill_pids() {
  local pids reason="$2" scope="${3:-full}" p alive="" idents="" now_ident was_ident
  if ! pids=$(mc_filter_protected "$1" "$scope"); then
    mc_log "$reason: refusing to kill -- the protected pid set is unknown (classification did not run, or ps could not resolve memcap's own ancestry)"
    return 1
  fi
  [ -z "${pids// /}" ] && return 1
  if [ "$MC_DRY_RUN" = "1" ]; then
    echo "would kill ($reason): $pids"; return 1
  fi
  if [ "$scope" = oversized ]; then
    # Publish before TERM so a tool-failure hook can already read the reason.
    command -v mc_job_feedback >/dev/null 2>&1 && mc_job_feedback || :
  fi
  if [ "$scope" = boot-timeout ]; then
    mc_boot_notice || mc_log 'boot timeout: could not record agent feedback'
  fi
  if [ "$scope" = poll-cleanup ]; then
    mc_poll_notice || mc_log 'poll cleanup: could not record agent feedback'
  fi
  for p in $pids; do
    mc_log "$reason: $(mc_abbrev "$(ps -o pid=,rss=,command= -p "$p" 2>/dev/null)")"
    idents="$idents$p|$(mc_pid_identity "$p")
"
  done
  if [ "$scope" = oversized ] || [ "$scope" = scheduled ] || [ "$scope" = idle-gc ] || [ "$scope" = boot-timeout ] || [ "$scope" = poll-cleanup ]; then
    # Logging and feedback can take time under pressure. Revalidate the original
    # identity after those subprocesses, as close to TERM as shell permits.
    pids=$(mc_filter_protected "$pids" "$scope") || return 1
    [ -n "${pids// /}" ] || return 1
  fi
  # shellcheck disable=SC2086
  if ! kill -TERM $pids 2>/dev/null; then
    mc_log "$reason: SIGTERM failed for:$pids"
    return 1
  fi
  sleep 2
  for p in $pids; do
    mc_pid_alive "$p" || continue
    now_ident=$(mc_pid_identity "$p")
    was_ident=$(mc_identity_lookup "$p" "$idents") || was_ident=""
    if [ -z "$now_ident" ] || [ "$now_ident" != "$was_ident" ]; then
      # The number is alive but it is not the process that was TERMed. This is
      # the widest snapshot-to-action window in the codebase; a shorter sleep
      # narrows it without closing it, an identity check closes it.
      mc_log "$reason: pid $p was reused between SIGTERM and SIGKILL -- not escalating"
      continue
    fi
    alive="$alive $p"
  done
  # Re-filtered, not just re-checked: the survivor list was built from a snapshot
  # taken before the TERM, and SIGKILL is the irreversible half of this function.
  # An agent session, or a tooling process, that appeared in the intervening two
  # seconds must get the same protection it would have got two seconds earlier.
  # shellcheck disable=SC2034 # read by boot_timeout.sh during the fresh filter
  if [ "$scope" = boot-timeout ]; then MC_BOOT_ESCALATING=1; fi
  # shellcheck disable=SC2034 # read by poll_cleanup.sh
  if [ "$scope" = poll-cleanup ]; then MC_POLL_ESCALATING=1; fi
  alive=$(mc_filter_protected "$alive" "$scope") || alive=""
  # shellcheck disable=SC2086
  [ -n "${alive// /}" ] && kill -KILL $alive 2>/dev/null
  return 0
}

mc_descendants() {
  local pid="$1" c
  printf '%s\n' "$pid"
  for c in $(pgrep -P "$pid" 2>/dev/null); do mc_descendants "$c"; done
}

# Footprint of a process AND everything under it. Tier 2 kills a subtree, so
# ranking candidates by the parent's own footprint ranked them by a number that
# is not what the kill reclaims: a fat worker outranks the Metro server that owns
# the worker pool, memcap kills the worker, the supervisor respawns it, and the
# next pass kills a different pid for the same reason. Two such kills 70 seconds
# apart on consecutive passes are in the production log.
#
# Returns non-zero when NOT ONE member could be measured -- an unmeasurable
# candidate must be skipped rather than ranked as zero.
mc_subtree_kb() {
  local p kb total=0 seen=0
  for p in $(mc_descendants "$1"); do
    kb=$(mc_footprint_kb "$p")
    case "$kb" in ''|*[!0-9]*) continue ;; esac
    total=$((total + kb))
    seen=1
  done
  [ "$seen" = "1" ] || return 1
  printf '%s' "$total"
  return 0
}

# macOS ps has no `etimes` (elapsed seconds) keyword -- that's Linux/procps only.
# `ps -o etimes=` fails on macOS with "keyword not found" and dumps the
# valid-keyword list to stdout instead, which is not a number. Asking `[ "$age"
# -lt N ]` to compare that against a minimum age does not fail closed: `[` errors
# on the non-integer and the age gate that is supposed to protect a fresh build
# ends up letting it through. macOS only gives `etime`, formatted
# `[[dd-]hh:]mm:ss` (e.g. "00:04", "01:02:03", "2-01:02:03").
#
# The _var form exists because tier 1 runs this once per orphan, and `$(...)`
# around a function that echoes costs a fork each time -- 388 forks per pass in
# the leak this tool was built for. The echoing wrapper is kept for callers (and
# tests) that want a value rather than a global.
mc_etime_secs_var() {
  local raw="${1//[[:space:]]/}" days=0 rest h=0 m s
  MC_ETIME_SECS=0
  [ -z "$raw" ] && return 1
  case "$raw" in
    *-*) days="${raw%%-*}"; rest="${raw#*-}" ;;
    *)   rest="$raw" ;;
  esac
  case "$rest" in
    *:*:*) h="${rest%%:*}"; rest="${rest#*:}"; m="${rest%%:*}"; s="${rest#*:}" ;;
    *:*)   m="${rest%%:*}"; s="${rest#*:}" ;;
    *)     m=0; s="$rest" ;;
  esac
  case "$days$h$m$s" in ''|*[!0-9]*) return 1 ;; esac
  MC_ETIME_SECS=$(( (10#$days * 86400) + (10#$h * 3600) + (10#$m * 60) + 10#$s ))
  return 0
}

mc_etime_secs() {
  mc_etime_secs_var "$1" || return 1
  echo "$MC_ETIME_SECS"
}

# `ps -o time=` (accumulated CPU) is NOT the same shape as `ps -o etime=`,
# despite looking similar at a glance -- confirmed empirically, not assumed: a
# process at 6 days' uptime showed `time` as "2960:52.31", never rolling into an
# hour or day segment the way `etime` does ("06-06:50:15" for the same process,
# same instant). `time` is plain MINUTES:SECONDS(.hundredths), with minutes
# growing unbounded. The fractional part is dropped -- sub-second precision
# doesn't matter against a multi-minute grace window.
mc_cputime_secs_var() {
  local raw="${1//[[:space:]]/}" m s
  MC_CPUTIME_SECS=0
  [ -z "$raw" ] && return 1
  case "$raw" in
    *:*) m="${raw%%:*}"; s="${raw#*:}" ;;
    *)   return 1 ;;
  esac
  s="${s%%.*}"
  case "$m$s" in ''|*[!0-9]*) return 1 ;; esac
  MC_CPUTIME_SECS=$(( (10#$m * 60) + 10#$s ))
  return 0
}

mc_cputime_secs() {
  mc_cputime_secs_var "$1" || return 1
  echo "$MC_CPUTIME_SECS"
}

# --- Idle stamps --------------------------------------------------------------
# `EPOCH CPU_SECONDS`, one file per tracked pid. Both tier 3 and the mobile-
# tooling veto keep their own directory of these (a tooling pid is not a sim pid
# and the two must not share history), and both read them through here.
#
# A corrupt stamp used to be catastrophic in two distinct ways, both reproduced:
#
#   `12345`        second field lost -- the CPU delta computed against an empty
#                  baseline, no reset ever fired, and `now - 12345` is decades,
#                  so the grace was bypassed ENTIRELY and the pid was killed on
#                  the first pass that saw it, reported as a successful reclaim.
#   `notanumber 0` `$(( ))` under `set -u` took down the whole pass -- and since
#                  a stamp is only pruned once its pid dies, EVERY subsequent
#                  pass died at the same line for as long as that simulator
#                  lived.
#
# Anything that does not parse as two whole numbers is treated as no stamp at
# all, which restarts the clock. That fails in the direction of more grace.
mc_read_idle_stamp() {
  local file="$1" a="" b=""
  MC_STAMP_FIRST=0
  MC_STAMP_CPU=0
  [ -f "$file" ] || return 1
  # shellcheck disable=SC2162
  read -r a b < "$file" 2>/dev/null || return 1
  case "$a" in ''|*[!0-9]*) return 1 ;; esac
  case "$b" in ''|*[!0-9]*) return 1 ;; esac
  [ ${#a} -le 18 ] && [ ${#b} -le 18 ] || return 1
  MC_STAMP_FIRST=$((10#$a))
  MC_STAMP_CPU=$((10#$b))
  return 0
}

mc_write_idle_stamp() {
  printf '%s %s\n' "$2" "$3" > "$1" 2>/dev/null || return 1
  return 0
}

# --- Tier 1: orphaned dev servers ---------------------------------------------
# Parent is dead, so no live session and no terminal owns it.
#
# A root recorded in the state file was safe AT RECORD TIME. It is
# re-canonicalized here, immediately before it is used to select a kill target,
# and matched against the stored string EXACTLY -- not just re-validated as
# "still resolves somewhere safe" -- because the directory it names could have
# been replaced by a symlink in the meantime (TOCTOU). mc_root_is_safe alone
# cannot catch every case: a root swapped to point at a DIFFERENT directory that
# also happens to be 2+ levels under HOME would still pass that check, letting a
# since-redirected root match against, and kill, a process it should never have
# been allowed to touch.
#
# Validation happens ONCE PER PASS, here, not once per (orphan, root) pair. That
# is the whole performance fix: canonicalizing a root does not depend on which
# orphan is being tested, and the old placement inside the inner loop cost 5,121
# microseconds per pair -- 388 orphans against this machine's 40 recorded roots
# is 79 seconds of work against a 60-second service interval, i.e. memcap at its
# slowest in exactly the leak it exists to clean up. Forty canonicalizations per
# pass replaces 15,520.
#
# The safety check is inlined against the ALREADY-canonical form rather than
# calling mc_root_is_safe, which would canonicalize the same path a second time
# one line after the equality test proved it resolves to itself. mc_prune_roots
# inlines the identical rule for the identical reason.
mc_valid_sweep_roots() {
  local root real home_real
  home_real=$(mc_home_real)
  while IFS= read -r root; do
    # Throttled, per reason: this is the self-healing path (mc_record_roots
    # re-registers live agents' cwds every pass, so a root that fails here once
    # typically starts matching again on its own), but it was previously
    # invisible while it happened. The key deliberately does NOT include the
    # root: keys become filenames under the state directory, and one file per
    # distinct root would be an unbounded, never-collected set. The message
    # names the root instead.
    if ! real=$(mc_canonicalize "$root"); then
      mc_log_throttled "root-skip-canon" "tier1: skipping sweep root $root -- no longer resolves"
      continue
    fi
    if [ "$real" != "$root" ]; then
      mc_log_throttled "root-skip-redirect" "tier1: skipping sweep root $root -- now resolves to $real, not what was recorded (TOCTOU)"
      continue
    fi
    case "$real" in
      "$home_real"/*/*) : ;;
      *)
        mc_log_throttled "root-skip-unsafe" "tier1: skipping sweep root $root -- no longer resolves somewhere safe"
        continue
        ;;
    esac
    printf '%s\n' "$root"
  done < <(mc_sweep_roots)
  # Keys are cleared on the pass where nothing was skipped for that reason, so a
  # recurrence gets its own line rather than being swallowed by a stale window.
  return 0
}

# TIER1_MIN_AGE_SEC. The old comment on this function claimed "the age gate is
# what stops a build from ever being the victim" -- that gate existed only in
# tier 2. Tier 1 had no age check of ANY kind, while MC_DEV_PATTERN matches
# /esbuild, /webpack, /rollup and /tsx: `npm run build &` reparented to init is
# an instant kill target, and one real production kill was `npm exec next start
# -p 3100`, a PRODUCTION server on an ad-hoc port.
#
# ppid==1 cannot distinguish "abandoned by a dead session" from "deliberately
# daemonized with nohup", and nothing else in the process table can either. The
# age gate does not fix that -- a nohup'd server is old by definition -- but it
# does fix the case the pattern actually creates, and the asymmetry decides it: a
# leak is a PERSISTENT condition (the 388 orphans accumulated over hours), so
# waiting five minutes to reap one costs nothing, while killing a three-second-
# old build destroys work that cannot be recovered.
MC_TIER1_MIN_AGE_DEFAULT=300
# lsof costs 35.8ms per call. The cwd fallback runs at most once per orphan, but
# 388 orphans is still 13.9 seconds -- so it is also budgeted per pass. Running
# out of budget means some orphans are not matched, i.e. fewer kills: the safe
# direction to fail in.
MC_TIER1_CWD_BUDGET_DEFAULT=64

mc_reap_orphans() {
  local roots rows row pid age_raw cmd keep="" matched root
  local min_age cwd_budget cwd_used=0 pid_cwd pid_cwd_real pid_cwd_tried plist

  if [ -z "${ORPHANS+x}" ]; then
    mc_log "tier1: refusing to act -- ORPHANS is unset, so classification did not run"
    return 0
  fi
  [ -n "${ORPHANS// /}" ] || return 0

  roots=$(mc_valid_sweep_roots)
  [ -n "$roots" ] || return 0

  min_age=$(mc_enf_num "${TIER1_MIN_AGE_SEC:-$MC_TIER1_MIN_AGE_DEFAULT}" "$MC_TIER1_MIN_AGE_DEFAULT" TIER1_MIN_AGE_SEC)
  cwd_budget=$(mc_enf_num "${TIER1_MAX_CWD_LOOKUPS:-$MC_TIER1_CWD_BUDGET_DEFAULT}" "$MC_TIER1_CWD_BUDGET_DEFAULT" TIER1_MAX_CWD_LOOKUPS)

  # ONE `ps` for every orphan's pid, age and command line together, instead of
  # one spawn per orphan per fact. A pid that has already exited simply has no
  # row, which is exactly the right handling for it.
  plist=$(printf '%s' "$ORPHANS" | tr -s '[:space:]' ',')
  plist="${plist#,}"; plist="${plist%,}"
  [ -n "$plist" ] || return 0
  rows=$(ps -o pid=,etime=,command= -p "$plist" 2>/dev/null)
  [ -n "$rows" ] || return 0

  # fd 3 for the outer loop so nothing inside it (lsof, in the cwd fallback) can
  # consume the row list by reading stdin.
  while IFS= read -r row <&3; do
    row="${row#"${row%%[![:space:]]*}"}"
    case "$row" in *[[:space:]]*) : ;; *) continue ;; esac
    pid="${row%%[[:space:]]*}"
    row="${row#*[[:space:]]}"
    row="${row#"${row%%[![:space:]]*}"}"
    case "$row" in *[[:space:]]*) : ;; *) continue ;; esac
    age_raw="${row%%[[:space:]]*}"
    # Padded with spaces so a root that is the process's ENTIRE command (no
    # trailing path segment) still matches at the boundary, the same trick
    # mc_self_ancestry uses. Matching "$root/" or "$root " -- not a bare
    # substring -- keeps root `~/dev/foo` from also matching `~/dev/foobar`, or
    # a process that merely names the root somewhere in an argument with no
    # separator after it.
    cmd=" ${row#*[[:space:]]} "
    case "$pid" in ''|*[!0-9]*) continue ;; esac

    mc_etime_secs_var "$age_raw" || continue
    [ "$MC_ETIME_SECS" -lt "$min_age" ] && continue

    matched=0
    pid_cwd_real=""
    pid_cwd_tried=0
    # `while read`, not `for root in $roots`: word-splitting breaks a root
    # containing a space into fragments, and a fragment like `/Users/x/dev/my`
    # (from `/Users/x/dev/my project`) can match on its own -- for the wrong
    # reason, since it was never the recorded root. A root can never contain a
    # newline: mc_canonicalize rejects control characters outright.
    while IFS= read -r root; do
      case "$cmd" in
        *"$root/"*|*"$root "*) matched=1 ;;
      esac
      # Second, independent way to be "under" the root: the orphan's own
      # CANONICAL cwd, compared against the canonical root. Argv alone misses a
      # project sitting behind a symlink (a HOME on /tmp or /var, ~/dev pointed
      # at an external volume) -- mc_record_roots stores the kernel's resolved
      # cwd, always canonical, but argv holds whatever string launched the
      # process, which is that path's UNRESOLVED form and so never textually
      # contains the canonical root. This is not a looser check than the argv
      # one: cwd is the kernel's own record of where the process actually lives,
      # not text the process chose to pass itself.
      #
      # Resolved lazily and at most ONCE per orphan -- only the first time a
      # root's argv match has failed -- so the common case (argv matches, usually
      # on the first root) never pays for lsof at all.
      if [ "$matched" != "1" ] && [ "$pid_cwd_tried" != "1" ]; then
        pid_cwd_tried=1
        if [ "$cwd_used" -lt "$cwd_budget" ]; then
          cwd_used=$((cwd_used + 1))
          pid_cwd=$(mc_pid_cwd "$pid")
          if [ -n "$pid_cwd" ]; then
            pid_cwd_real=$(mc_canonicalize "$pid_cwd") || pid_cwd_real=""
          fi
        else
          mc_log_throttled "tier1-cwd-budget" "tier1: cwd lookups capped at $cwd_budget this pass (TIER1_MAX_CWD_LOOKUPS) -- some orphans were matched on argv alone. Raise it if a leak this large is normal here."
        fi
      fi
      if [ "$matched" != "1" ] && [ -n "$pid_cwd_real" ]; then
        case "$pid_cwd_real" in
          "$root"|"$root"/*) matched=1 ;;
        esac
      fi
      [ "$matched" = "1" ] && break
    done <<EOF
$roots
EOF
    [ "$matched" = "1" ] && keep="$keep $pid"
  done 3<<EOF
$rows
EOF

  [ -n "${keep// /}" ] && mc_kill_pids "$keep" "tier1 orphan"
  return 0
}

# --- Tier 2: long-lived dev servers over budget -------------------------------
# A dev server runs for hours; a build runs for seconds. The age gate is what
# stops a build from ever being the victim.
#
# Production record before this pass: ten real kills, 126 MB reclaimed in total,
# against overages of 0.5-6 GB. Six of the ten were agent grandchildren (fixed by
# consuming PROTECTEDPIDS in mc_filter_protected), four were the Metro
# transformer feeding a simulator the developer was actively driving -- memcap
# declined tier 3 at 13:03:47 because of "active mobile tooling", then killed the
# bundler serving that same simulator at 13:03:48. Tier 2 had zero references to
# either mobile veto.
MC_TIER2_MIN_AGE_DEFAULT=300

# Tier 3 owns every sim-classified process. It decides when a browser or emulator
# is reclaimable by measuring CPU flatness across SIM_IDLE_GRACE_SEC, holds one
# that a live server is using for as long as its holder lives, and gives an agent
# session's own browser triple the clock. Tier 2 selects by INFERENCE and has no
# idleness test of any kind -- so a subtree that happens to contain a browser took
# one out from under every one of those rules.
#
# Not hypothetical: 2026-08-26 21:10:30, a Playwright driver plus a headed Chrome
# (`--user-data-dir=...playwright_chromiumdev_profile-...`, sim-classified) and
# its six helpers -- eight processes in one event, none idle-checked, ranked top
# precisely BECAUSE a browser subtree is the largest thing on the machine.
#
# "Orphaned" is not "idle": a Playwright run whose shell has exited is reparented
# to init while its tests are still running, which is exactly the shape tier 2
# ranks first. So a candidate holding a sim that has not cleared tier 3's grace is
# skipped, and the next candidate is considered instead.
#
# Fails closed. A sim pid with no readable stamp is not PROVEN idle, so it blocks:
# tier 3 stamps every sim pid on every pass, which makes an unstamped one a pid
# memcap has not seen yet rather than one it has watched sit still.
mc_tier2_sim_blocker() {
  local pid="$1" now="$2" grace="$3" p age
  MC_TIER2_BLOCKER=""
  [ -n "${SIMPIDS+x}" ] || return 1
  for p in $(mc_descendants "$pid"); do
    case " $SIMPIDS " in *" $p "*) : ;; *) continue ;; esac
    age=0
    if mc_read_idle_stamp "$(mc_sims_idle_stamp "$p")"; then
      age=$((now - MC_STAMP_FIRST))
      [ "$age" -ge "$grace" ] && continue
      [ "$age" -lt 0 ] && age=0
    fi
    MC_TIER2_BLOCKER="$p $age"
    return 0
  done
  return 1
}

mc_kill_over_budget() {
  local pid age_raw ranked="" kb min_age victim vcmd row cand now sim_grace blocked

  if [ -z "${DEVPIDS+x}" ]; then
    mc_log "tier2: refusing to act -- DEVPIDS is unset, so classification did not run"
    return 0
  fi

  if [ "$(mc_enf_num "${TIER2_ENABLED:-1}" 1 TIER2_ENABLED)" = "0" ]; then
    mc_log_throttled "tier2-disabled" "tier2: over budget, but TIER2_ENABLED=0 -- taking no action"
    return 0
  fi
  mc_log_throttle_clear "tier2-disabled"

  # Tier 3 refuses to reclaim a simulator while mobile tooling is driving it.
  # Tier 2 must refuse for the same reason: the dev server it would pick is
  # overwhelmingly likely to be the bundler feeding that simulator, and killing
  # it destroys the session tier 3 just protected.
  if mc_active_mobile_tooling; then
    mc_log_throttled "tier2-active-tooling" "tier2: declining -- active mobile tooling detected (maestro, xcodebuild, expo, react-native, or detox). The dev server over budget here is most likely the bundler feeding that simulator.$(mc_mobile_blocker_detail "${MC_ACTIVE_MOBILE_PIDS:-}")"
    return 0
  fi
  mc_log_throttle_clear "tier2-active-tooling"
  if mc_hands_on_mobile; then
    mc_log_throttled "tier2-hands-on-mobile" "tier2: declining -- hands-on mobile work detected (Xcode, Android Studio, or Simulator.app open).$(mc_mobile_blocker_detail "${MC_HANDS_ON_MOBILE_PIDS:-}")"
    return 0
  fi
  mc_log_throttle_clear "tier2-hands-on-mobile"

  min_age=$(mc_enf_num "${TIER2_MIN_AGE_SEC:-$MC_TIER2_MIN_AGE_DEFAULT}" "$MC_TIER2_MIN_AGE_DEFAULT" TIER2_MIN_AGE_SEC)
  for pid in $DEVPIDS; do
    age_raw=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')
    mc_etime_secs_var "$age_raw" || continue
    [ "$MC_ETIME_SECS" -lt "$min_age" ] && continue
    # mc_subtree_kb, not ps RSS and not the parent's own footprint: the trigger
    # that got tier 2 reached ranks by physical footprint (measure.sh), which
    # summed RSS over-counts by ~2.5x for a process tree sharing pages, and the
    # kill takes the whole subtree. A candidate that exits mid-rank, or that
    # cannot be measured at all, returns non-zero and is skipped rather than
    # feeding `sort -rn` a malformed row whose bare pid would be read as the
    # sort key.
    kb=$(mc_subtree_kb "$pid") || continue
    ranked="$ranked$kb $pid
"
  done

  if [ -z "${ranked// /}" ]; then
    # Throttled like every other per-pass decline. v0.4.0 throttled the repeating
    # status lines so they could not drown the kill records, and missed this one:
    # in 42 hours of production it was the single most frequent line in
    # actions.log (79 identical entries), because a machine that is chronically
    # over budget with only young processes running reaches this branch on every
    # pass. The notification below keeps its own 300-second throttle.
    mc_log_throttled "tier2-no-candidate" "tier2: over budget but no candidate is both older than ${min_age}s and measurable -- not touching active work"
    [ "$MC_DRY_RUN" = "1" ] || mc_notify "Agents over budget. Only fresh builds running, so nothing was killed."
    return 0
  fi
  mc_log_throttle_clear "tier2-no-candidate"
  # Down the ranking rather than `head -1`: the biggest subtree is not always one
  # tier 2 is allowed to take, and a blocked candidate must not stop a legitimate
  # one below it from being considered.
  now=$(date +%s)
  sim_grace=$(mc_enf_num "${SIM_IDLE_GRACE_SEC:-600}" 600 SIM_IDLE_GRACE_SEC)
  victim=""
  blocked=0
  while IFS= read -r row; do
    cand="${row##* }"
    case "$cand" in ''|*[!0-9]*) continue ;; esac
    if mc_tier2_sim_blocker "$cand" "$now" "$sim_grace"; then
      blocked=1
      mc_log_throttled "tier2-sim-subtree" "tier2: skipping pid $cand -- its subtree holds sim pid ${MC_TIER2_BLOCKER%% *}, idle ${MC_TIER2_BLOCKER##* }s of the ${sim_grace}s grace. A browser or emulator is tier 3's to judge, not tier 2's."
      continue
    fi
    victim="$cand"
    break
  done <<EOF
$(printf '%s' "$ranked" | sort -rn)
EOF
  if [ -z "$victim" ]; then
    [ "$blocked" = "1" ] && mc_log_throttled "tier2-all-blocked" "tier2: over budget, but every candidate's subtree holds a simulator or browser still inside its idle grace -- tier 3 will reclaim those once they have been idle long enough"
    return 0
  fi
  mc_log_throttle_clear "tier2-sim-subtree"
  mc_log_throttle_clear "tier2-all-blocked"
  vcmd=$(ps -o comm= -p "$victim" 2>/dev/null | tr -d ' ')
  vcmd="${vcmd##*/}"
  # Gated on mc_kill_pids actually killing something: a dry run, or a real pass
  # where mc_filter_protected removed the only candidate, must not tell the user
  # anything was killed. The old wording claimed "a leaked dev server" after
  # every kill -- in ten production cases it was neither leaked nor a dev server,
  # so it now says what was actually killed.
  mc_kill_pids "$(mc_descendants "$victim" | tr '\n' ' ')" "tier2 over-budget dev server" &&
    mc_notify "memcap killed ${vcmd:-pid $victim} (pid $victim) and its subtree to stay inside your agent budget."
  return 0
}

# --- Tier 3: idle simulators and browsers -------------------------------------
# Vetoed by hands-on mobile work and by active mobile tooling actually driving a
# simulator -- xcodebuild and detox are distinctive process names on their own
# (matched on argv[0]/comm via `pgrep -x`, the same "argv[0], not the whole
# command line" rule as everywhere else in this file); expo and react-native are
# commonly launched through node, so argv[0] alone ("node") would be too broad,
# matched instead on the tool name followed by a subcommand a casual mention
# cannot produce; maestro runs as a JVM process, identified by the same narrow
# argv pattern classify.sh's MC_SIM_ARG already uses for it.
MC_MOBILE_TOOLING_EXACT='xcodebuild detox'
MC_MOBILE_TOOLING_ARGV_PATTERN='(^|/| )expo[[:space:]]+(start|run:ios|run:android)|(^|/| )react-native[[:space:]]+(run-ios|run-android|start)|\.maestro/lib|maestro\.cli'

mc_mobile_tooling_idle_dir() { printf '%s/tooling-idle\n' "$(mc_state_dir)"; }
mc_mobile_tooling_idle_stamp() { printf '%s/%s\n' "$(mc_mobile_tooling_idle_dir)" "$1"; }

mc_mobile_tooling_pids() {
  # Match executable identity for native tools; argv for interpreter-hosted
  # tools, excluding programs that merely inspect or print those arguments.
  # comm retains the whole executable path, including spaces in Xcode Beta.app.
  ps -Ao pid=,comm= 2>/dev/null | awk -v exact="$MC_MOBILE_TOOLING_EXACT" '
    {pid=$1; exe=$0; sub(/^[[:space:]]*[0-9]+[[:space:]]+/,"",exe); sub(/^.*\//,"",exe)
     if(index(" " exact " "," " exe " ")) printf "%s ",pid}' || return 1
  ps -Ao pid=,command= 2>/dev/null | awk -v pat="$MC_MOBILE_TOOLING_ARGV_PATTERN" '
    {pid=$1; cmd=$0; sub(/^[[:space:]]*[0-9]+[[:space:]]+/,"",cmd)
     exe=cmd; sub(/[[:space:]].*$/,"",exe); sub(/^.*\//,"",exe)
     if(exe ~ /^(rg|grep|egrep|awk|sed|ps|top|tail|head|cat|sort|cut|tr|xargs|find|bash|zsh|sh|jq)$/) next
     if(cmd ~ pat) printf "%s ",pid}'
}

# Matching a tool's mere EXISTENCE vetoed tier 3 permanently the moment one of
# them ran as a background service rather than a foreground command --
# discovered in production, not hypothetically: maestro's own MCP server (`java
# ... maestro.cli.AppKt mcp`) idles for days between requests. Enumerating
# "server" subcommands to exclude is a list that rots; the CPU-flat test used for
# simulators fixes it with no new per-tool knowledge. An idle MCP server burns
# approximately zero CPU, same as an unused simulator; an actual maestro flow,
# xcodebuild or detox run does not.
#
# A pid never seen before starts its own clock and counts as active for this
# pass -- the same conservative bootstrapping mc_reap_sims uses for a freshly
# tracked sim.
#
# MOBILE_TOOLING_IDLE_SEC has to outlast one enforcement pass or it is not
# hysteresis at all, which is why the default is 300 and not the 60 it shipped
# as. A real pass takes ~64s (launchd coalesces the 60s StartInterval), so at 60
# a maestro MCP server that handled a single request flipped the veto on and the
# very next pass flipped it back off. Nine days of production logs held 544
# "declining -- active mobile tooling detected" lines alternating minute-to-
# minute with the hands-on veto, each transition clearing the throttle key and
# so logging again. The window is a claim about the TOOL, too, not just about
# the log: a Maestro run goes quiet for a minute between flows, and treating
# that gap as idle is how tier 3 gets to shut a simulator out from under it.
#
# The MC_ACTIVE_MOBILE_TOOLING escape hatch (same pattern as MC_DOCKER_RUNTIME)
# forces the answer: this check depends entirely on what is running on the host,
# with no way to make it deterministic in an environment that happens to have one
# of these processes alive for an unrelated reason. It deliberately does NOT
# affect mc_veto_evidence_pids -- forcing the veto's ANSWER for a test must not
# make a live tooling process killable.
mc_active_mobile_tooling() {
  MC_ACTIVE_MOBILE_PIDS=""
  if [ -n "${MC_ACTIVE_MOBILE_TOOLING:-}" ]; then
    [ "$MC_ACTIVE_MOBILE_TOOLING" = "1" ]
    return
  fi
  local pid pids dir now window active_cpu_sec stamp cpu_delta active=0

  if ! pids=$(mc_mobile_tooling_pids); then
    MC_ACTIVE_MOBILE_PIDS=unknown
    return 0
  fi
  dir="$(mc_mobile_tooling_idle_dir)"

  # Prune stamps for pids no longer alive or no longer matching, so a reused pid
  # cannot inherit a stale clock and an exited process does not leave a stray
  # file forever.
  if [ -d "$dir" ]; then
    for stamp in "$dir"/*; do
      [ -e "$stamp" ] || continue
      pid="${stamp##*/}"
      case " $pids " in
        *" $pid "*) mc_pid_alive "$pid" && continue ;;
      esac
      rm -f "$stamp"
    done
  fi

  [ -z "${pids// /}" ] && return 1

  mkdir -p "$dir"
  now=$(date +%s)
  window=$(mc_enf_num "${MOBILE_TOOLING_IDLE_SEC:-300}" 300 MOBILE_TOOLING_IDLE_SEC)
  active_cpu_sec=$(mc_enf_num "${SIM_ACTIVE_CPU_SEC:-2}" 2 SIM_ACTIVE_CPU_SEC)
  for pid in $pids; do
    stamp="$(mc_mobile_tooling_idle_stamp "$pid")"
    if ! mc_cputime_secs_var "$(ps -o time= -p "$pid" 2>/dev/null)"; then
      # Could not sample CPU. A measurement gap is not evidence of idleness.
      active=1
      MC_ACTIVE_MOBILE_PIDS="$MC_ACTIVE_MOBILE_PIDS $pid"
      continue
    fi
    if ! mc_read_idle_stamp "$stamp"; then
      mc_write_idle_stamp "$stamp" "$now" "$MC_CPUTIME_SECS"
      active=1
      MC_ACTIVE_MOBILE_PIDS="$MC_ACTIVE_MOBILE_PIDS $pid"
      continue
    fi
    cpu_delta=$(( MC_CPUTIME_SECS - MC_STAMP_CPU ))
    if [ "$cpu_delta" -ge "$active_cpu_sec" ]; then
      mc_write_idle_stamp "$stamp" "$now" "$MC_CPUTIME_SECS"
      active=1
      MC_ACTIVE_MOBILE_PIDS="$MC_ACTIVE_MOBILE_PIDS $pid"
      continue
    fi
    if [ $((now - MC_STAMP_FIRST)) -lt "$window" ]; then
      active=1
      MC_ACTIVE_MOBILE_PIDS="$MC_ACTIVE_MOBILE_PIDS $pid"
    fi
  done
  [ "$active" = "1" ]
}

MC_HANDS_ON_PATTERN='/Xcode\.app/Contents/MacOS/Xcode|Android Studio\.app/Contents/MacOS|/Simulator\.app/Contents/MacOS/Simulator'

mc_hands_on_mobile_pids() {
  ps -Ao pid=,command= 2>/dev/null | awk -v pat="$MC_HANDS_ON_PATTERN" '
    {pid=$1; cmd=$0; sub(/^[[:space:]]*[0-9]+[[:space:]]+/,"",cmd)
     if(match(cmd,pat)) {
       prefix=substr(cmd,1,RSTART-1)
       # Spaces inside an app path are legitimate; an argument introducing
       # another absolute path or a command option is not executable identity.
       if(cmd ~ /^\// && prefix !~ /[[:space:]]\// && prefix !~ /[[:space:]]-/)
         printf "%s ",pid
     }}'
}

mc_hands_on_mobile() {
  MC_HANDS_ON_MOBILE_PIDS=""
  if [ -n "${MC_HANDS_ON_MOBILE:-}" ]; then
    [ "$MC_HANDS_ON_MOBILE" = "1" ]
    return
  fi
  if ! MC_HANDS_ON_MOBILE_PIDS=$(mc_hands_on_mobile_pids); then
    MC_HANDS_ON_MOBILE_PIDS=unknown
    return 0
  fi
  [ -n "$MC_HANDS_ON_MOBILE_PIDS" ]
}

# A bounded parent walk returns a known session, never guesses ownership from
# a shared terminal or a project path. Missing/cyclic ancestry retains the veto.
mc_agent_owner() {
  [ -n "${AGENTPIDS+x}" ] || return 1
  mc_proc_table_warm
  printf '%s\n' "$MC_PROC_TABLE" | awk -v pid="$1" -v agents=" $AGENTPIDS " '
    {pp[$1]=$2}
    END {for(i=0;i<32;i++) {
      if(!(pid in pp) || seen[pid]++) exit 1
      if(index(agents," " pid " ")) {print pid; exit 0}
      pid=pp[pid]; if(pid<=1) exit 1
    } exit 1}'
}

mc_mobile_browser_independent() {
  local pid="$1" cmd="$2" exe owner other blocker
  exe="${cmd%% *}"
  case "$exe" in *ms-playwright*|*headless_shell*|*chrome-headless-shell*) ;; *) return 1 ;; esac
  [ -n "${MC_MOBILE_BLOCKERS// /}" ] || return 1
  owner=$(mc_agent_owner "$pid") || return 1
  for blocker in $MC_MOBILE_BLOCKERS; do
    other=$(mc_agent_owner "$blocker") || return 1
    [ "$owner" != "$other" ] || return 1
  done
  return 0
}

mc_mobile_blocker_detail() {
  local pids="$1" pid cmd out="" count=0
  for pid in $pids; do
    cmd=$(ps -p "$pid" -o comm= 2>/dev/null) || cmd=unavailable
    out="$out pid=$pid $(mc_abbrev "$cmd" 160);"
    count=$((count+1)); [ "$count" -ge 5 ] && break
  done
  printf '%s' "${out:- blocker identity unavailable; retaining protection}"
}

# Restores the pre-v0.3.0 behavior for TIER3_REQUIRE_NO_SESSION=1: tier 3 never
# reaps while any agent session is alive, full stop. That was the original design
# -- a conservative stand-in chosen because sims cannot be attributed to a
# session by process tree (CoreSimulatorService owns them), not a deliberate
# "only when idle AND alone" rule. In 1,643 real passes on a machine that always
# has a session open it fired zero times. Kept as an opt-in.
mc_no_live_session() {
  local pid
  # Fails CLOSED: an unset AGENTPIDS means classification did not run, which is
  # not the same as "no session is alive" -- and this function's `true` is a
  # licence to reap.
  [ -n "${AGENTPIDS+x}" ] || return 1
  for pid in $AGENTPIDS; do mc_pid_alive "$pid" && return 1; done
  return 0
}

MC_SIM_KILL_PATTERN='qemu-system|/emulator( |$)|emulator64|ms-playwright|headless_shell|\.maestro/lib'
# The ONE process that means "this specific iOS device is booted": launchd_sim,
# which CoreSimulator starts per device and whose argv names the device's own
# data directory --
#
#   launchd_sim /Users/u/Library/Developer/CoreSimulator/Devices/<UDID>/data/var/run/launchd_bootstrap.plist
#
# so the pid, its idle clock, and the UDID `simctl shutdown` takes are all the
# same fact. A freshly booted device gets a NEW launchd_sim pid, so its idle
# clock starts at zero rather than inheriting anything.
#
# Two processes are deliberately NOT here, for the same reason in two forms --
# a process that outlives every device cannot be evidence that one is booted:
#
#   simdiskimaged     a root-owned daemon that runs whether or not anything is
#                     booted. Including it made `simctl shutdown all` look
#                     justified on a machine with nothing booted at all.
#   SimulatorTrampoline
#                     a CoreSimulator helper with no device affinity: measured
#                     on the author's machine at 40 h alive, 11 CPU-seconds
#                     total, same pid across a device boot AND shutdown. As
#                     tier-3 evidence it is permanently true and permanently
#                     idle, so between 08-27 and 08-29 tier 3 ran `simctl
#                     shutdown all` 38 times in one-minute bursts against the
#                     simulator a live Maestro run was driving -- Maestro
#                     re-booted it, memcap shut it down again next pass, and the
#                     user stopped the daemon with `memcap off`. It stays in
#                     classify.sh's MC_SIM_EXE, because its memory is real and
#                     belongs in the budget; it is never evidence about a device.
MC_SIM_IOS_EXE='launchd_sim'

# `xcrun` through a variable, the same way service.sh reaches launchctl and brew:
# a PATH-based stub is a test convention, this is a guarantee. No test can reach
# a real `xcrun simctl shutdown` by forgetting a flag.
MC_XCRUN_BIN="${MC_XCRUN_BIN:-xcrun}"

# The booted devices, one "<UDID> <name>" line each; no output means nothing is
# booted (or that xcrun is not installed, which is the same thing here). Purely
# read-only -- the dry-run path calls it too, because without it a dry run
# announces a shutdown of devices that do not exist.
#
# jq is a formula dependency and `-j` is the parse we trust. The plain-text
# fallback follows docker.sh's sed fallback exactly: it is for a machine missing
# the formula's own dependency, and it either finds a UDID-shaped token on a
# `(Booted)` line or gives up. Giving up is safe -- an unrecognised device is a
# device memcap will not shut down.
mc_booted_devices() {
  local json
  command -v "$MC_XCRUN_BIN" >/dev/null 2>&1 || return 0
  if command -v jq >/dev/null 2>&1; then
    json=$("$MC_XCRUN_BIN" simctl list devices booted -j 2>/dev/null)
    if [ -n "$json" ]; then
      printf '%s' "$json" | jq -r '
        .devices // {} | to_entries[] | .value[]?
        | select(.state == "Booted") | "\(.udid) \(.name)"' 2>/dev/null
      return 0
    fi
  fi
  # `    iPhone 17 (F096B0A2-20F5-4637-BC0E-19098780FA83) (Booted)`
  "$MC_XCRUN_BIN" simctl list devices booted 2>/dev/null |
    sed -n 's/^[[:space:]]*\(.*\) (\([0-9A-Fa-f][0-9A-Fa-f-]*\)) (Booted).*$/\2 \1/p'
  return 0
}

# The pid in $2 (a newline-separated "<pid> <command line>" list) whose command
# line names device $1. `/Devices/<UDID>/` with both slashes so a UDID that is a
# prefix of another cannot match the wrong device.
mc_sim_device_pid() {
  local udid="$1" line
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    case "$line" in
      *"/Devices/$udid/"*) printf '%s' "${line%% *}"; return 0 ;;
    esac
  done <<EOF
$2
EOF
  return 1
}

# Is device $1 represented AT ALL among the tracked pids in $2 -- ready or not?
# Only asked about a booted device that no ready launchd_sim claimed, to separate
# "tracked, still inside its grace" (already reported by the tier3-holding line)
# from "not in the snapshot at all" (reported once per UDID, throttled). One `ps`
# per tracked pid, which it can afford: nothing calls it on a pass where every
# booted device mapped cleanly.
mc_sim_device_tracked() {
  local udid="$1" pid cmd
  for pid in $2; do
    cmd=$(ps -o command= -p "$pid" 2>/dev/null)
    case "$cmd" in
      *"/Devices/$udid/"*) return 0 ;;
    esac
  done
  return 1
}

# SIM_IDLE_GRACE_SEC gives a hand-booted simulator a reprieve. Without it, someone
# running Simulator.app or `simctl` directly -- no Xcode open, no agent session --
# has their simulator killed within one poll.
#
# One stamp PER TRACKED PID, not one shared file: a single machine-wide stamp
# meant a simulator booted by hand inherited whatever timestamp an unrelated,
# already-idle sim process (a stale Playwright browser, say) had accumulated.
#
# Readiness is now judged PER PID rather than as a global conjunction over every
# tracked pid. The old rule -- no pid is reaped until every tracked pid has
# individually cleared the grace -- was chosen as the conservative reading of
# "protect the newest one", and against real measured churn it is not
# conservative, it is inert: 239 distinct sim-classified pids appeared in 9.5
# minutes on this machine, including 28 Playwright Firefox processes born and
# dead inside a single 60-second pass. One fresh renderer protects an unrelated
# stale Chrome forever. Each pid now earns, and spends, its own clock.
mc_sims_idle_dir() { printf '%s/sims-idle\n' "$(mc_state_dir)"; }
mc_sims_idle_stamp() { printf '%s/%s\n' "$(mc_sims_idle_dir)" "$1"; }

# A tracked pid is a TARGET only if all three hold. Any one failing means the pid
# is tracked and aged like any other, but can never hold a reap back -- which is
# the second half of the tier-3 unblock: on this machine either disqualification
# alone would have been enough, because the pid that blocked 1,973 consecutive
# passes was both unsignalable and not in MC_SIM_KILL_PATTERN.
mc_sim_is_target() {
  local pid="$1" cmd="$2"
  [ -n "$cmd" ] || return 1
  printf '%s' "$cmd" | grep -Eq "$MC_SIM_KILL_PATTERN" || return 1
  # `kill -0` as a PERMISSION test, which is the one question it actually
  # answers well. It is wrong for liveness (mc_pid_alive above) and right here:
  # a process memcap cannot signal is not a candidate, and pretending otherwise
  # is what let a root-owned daemon gate the whole tier.
  if ! kill -0 "$pid" 2>/dev/null; then
    mc_log_throttled "tier3-unsignalable" "tier3: pid $pid matches the reclaim pattern but memcap cannot signal it (not ours) -- tracked, never a target"
    return 1
  fi
  if mc_pid_is_evidence "$pid"; then
    mc_log_throttled "tier3-target-is-evidence" "tier3: pid $pid matches the reclaim pattern but $MC_EVIDENCE_REASON -- never a target"
    return 1
  fi
  return 0
}

mc_reap_sims() {
  local pid dir stamp now grace tree_grace pid_grace active_cpu_sec cmd
  local ready="" targets="" held=0 held_pid="" held_age=-1 age mobile_veto=0
  local MC_MOBILE_BLOCKERS=""
  # Per-device bookkeeping. ios_ready_map is "<pid> <command line>" lines for
  # every ready, non-evidence launchd_sim; shutdowns is "<UDID> <pid> <name>"
  # lines for the devices this pass has actually proven idle.
  local ios_ready_map="" devices="" shutdowns="" device="" udid="" name="" dev_pid="" rc=0 err=""

  if [ -z "${SIMPIDS+x}" ]; then
    mc_log "tier3: refusing to act -- SIMPIDS is unset, so classification did not run"
    return 0
  fi

  mc_veto_evidence_warm
  dir="$(mc_sims_idle_dir)"
  now=$(date +%s)
  grace=$(mc_enf_num "${SIM_IDLE_GRACE_SEC:-600}" 600 SIM_IDLE_GRACE_SEC)
  # A sim pid that is a DESCENDANT OF A LIVE AGENT SESSION earns a longer clock
  # rather than either absolute immunity or none. Both extremes are wrong, and
  # the measurements say why.
  #
  # Absolute immunity (full PROTECTEDPIDS at this tier) is indistinguishable from
  # tier 3 being dead: on this machine all 14 reclaimable sim pids -- 7 Chrome, 5
  # Playwright shells, 2 maestro -- are agent descendants, because that is how a
  # Playwright or devtools-MCP browser is launched in the first place. Shipping
  # it would re-create the audit's headline finding under a new cause.
  #
  # No extra clock is wrong too: an agent's browser can sit genuinely idle
  # through a long turn while the agent thinks, and losing it mid-test is exactly
  # the "kills live work" failure this audit is about.
  #
  # So: the ordinary grace proves a browser is unused; triple it before acting on
  # one that still belongs to somebody. The measured churn supports the split --
  # 28 Playwright Firefox processes were born and died inside a single 60s pass
  # here, so a genuinely-in-use browser never approaches either threshold, while
  # a leaked one crosses both and keeps going. Clamped never to be shorter than
  # the ordinary grace: a config that lowered it would silently make agent-owned
  # browsers the FIRST thing reaped.
  tree_grace=$(mc_enf_num "${TIER3_AGENT_TREE_GRACE_SEC:-1800}" 1800 TIER3_AGENT_TREE_GRACE_SEC)
  [ "$tree_grace" -lt "$grace" ] && tree_grace="$grace"
  active_cpu_sec=$(mc_enf_num "${SIM_ACTIVE_CPU_SEC:-2}" 2 SIM_ACTIVE_CPU_SEC)

  # Prune stamps for pids no longer alive or no longer sim-classified, so a
  # reused pid number cannot inherit a stale idle clock and an exited sim does
  # not leave a stray file behind forever. Runs unconditionally, ahead of every
  # veto below -- pure garbage collection, unrelated to whether a reap is
  # currently permitted.
  if [ -d "$dir" ]; then
    for stamp in "$dir"/*; do
      [ -e "$stamp" ] || continue
      pid="${stamp##*/}"
      case " $SIMPIDS " in
        *" $pid "*) mc_pid_alive "$pid" && continue ;;
      esac
      rm -f "$stamp"
    done
  fi

  # Idle/CPU bookkeeping runs BEFORE the veto checks below, and no veto ever
  # deletes it -- contrast the old `rm -rf "$dir"` on every decline, which reset
  # every sim's clock to zero on every single pass under continuous agent use, so
  # tier 3 could not fire even in principle. A veto still blocks the kill a few
  # lines down; it just no longer erases the evidence.
  if [ -n "${SIMPIDS// /}" ]; then
    mkdir -p "$dir"
    for pid in $SIMPIDS; do
      stamp="$(mc_sims_idle_stamp "$pid")"
      age=0
      if ! mc_cputime_secs_var "$(ps -o time= -p "$pid" 2>/dev/null)"; then
        # Could not sample CPU this pass (pid raced between snapshot and check,
        # ps failed). A measurement gap is not evidence of activity, but it is
        # not evidence of idleness either -- hold this pid without resetting its
        # clock over it.
        :
      elif ! mc_read_idle_stamp "$stamp"; then
        # Never tracked before, or the stamp does not parse. Start the clock now
        # and take this pass's CPU as the baseline; there is nothing to compare
        # against yet.
        mc_write_idle_stamp "$stamp" "$now" "$MC_CPUTIME_SECS"
      elif [ $(( MC_CPUTIME_SECS - MC_STAMP_CPU )) -ge "$active_cpu_sec" ]; then
        # Real CPU work happened since the clock started -- this is what "in use"
        # actually looks like, unlike merely having a session open. A booted-but-
        # unused simulator burns approximately zero CPU.
        mc_write_idle_stamp "$stamp" "$now" "$MC_CPUTIME_SECS"
      else
        age=$(( now - MC_STAMP_FIRST ))
        pid_grace="$grace"
        case " $PROTECTEDPIDS " in
          *" $pid "*) pid_grace="$tree_grace" ;;
        esac
        if [ "$age" -ge "$pid_grace" ]; then
          ready="$ready $pid"
          continue
        fi
        if [ "$pid_grace" != "$grace" ] && [ "$age" -ge "$grace" ]; then
          # Held ONLY because it belongs to a live session. Worth a line: it is
          # the difference between "tier 3 is working" and "tier 3 is inert
          # again", and it is invisible from the outside otherwise.
          mc_log_throttled "tier3-agent-tree-grace" "tier3: pid $pid has been idle ${age}s, past the ${grace}s grace, but it is a live agent session's own process -- holding it to ${tree_grace}s (TIER3_AGENT_TREE_GRACE_SEC)"
        fi
      fi
      # Not ready, for whichever of the four reasons. Counted and remembered, so
      # a pass that reclaims nothing can say WHICH pid is holding it back and for
      # how long -- see the tier3-holding line below.
      held=$((held + 1))
      if [ "$age" -gt "$held_age" ]; then held_age="$age"; held_pid="$pid"; fi
    done
  fi

  # Vetoes, checked after the bookkeeping above so idle/CPU evidence keeps
  # accumulating even while one is in effect. Throttled, not mc_log: each can
  # hold for as long as the tool's normal operating state does, and unthrottled
  # they drowned the actual kill records. Each reason's key clears the moment its
  # own condition stops holding, so a state change still gets its own line.
  if [ "$(mc_enf_num "${TIER3_REQUIRE_NO_SESSION:-0}" 0 TIER3_REQUIRE_NO_SESSION)" = "1" ] && ! mc_no_live_session; then
    mc_log_throttled "tier3-agent-alive" "tier3: declining -- an agent session is alive (TIER3_REQUIRE_NO_SESSION=1)"
    return 0
  fi
  mc_log_throttle_clear "tier3-agent-alive"
  if mc_active_mobile_tooling; then
    mobile_veto=1
    MC_MOBILE_BLOCKERS="${MC_ACTIVE_MOBILE_PIDS:-unknown}"
    mc_log_throttled "tier3-active-tooling" "tier3: declining -- active mobile tooling detected (maestro, xcodebuild, expo, react-native, or detox).$(mc_mobile_blocker_detail "$MC_MOBILE_BLOCKERS")"
  else
    mc_log_throttle_clear "tier3-active-tooling"
  fi
  if mc_hands_on_mobile; then
    mobile_veto=1
    # An unknown blocker prevents any exception even if another is known.
    MC_MOBILE_BLOCKERS="$MC_MOBILE_BLOCKERS ${MC_HANDS_ON_MOBILE_PIDS:-unknown}"
    mc_log_throttled "tier3-hands-on-mobile" "tier3: declining -- hands-on mobile work detected (Xcode, Android Studio, or Simulator.app open).$(mc_mobile_blocker_detail "${MC_HANDS_ON_MOBILE_PIDS:-}")"
  else
    mc_log_throttle_clear "tier3-hands-on-mobile"
  fi

  for pid in $ready; do
    cmd=$(ps -o command= -p "$pid" 2>/dev/null)
    if [ "$mobile_veto" = 1 ]; then
      mc_mobile_browser_independent "$pid" "$cmd" || continue
    fi
    # A booted iOS device is reclaimed by `simctl shutdown <UDID>`, not by
    # signalling launchd_sim -- so "is a device idle" is a separate question from
    # "is any pid a kill target", answered over the ready set rather than the
    # target set. Recorded PER PID with its command line, because that command
    # line carries the UDID: the pid's own idle clock is the readiness of exactly
    # one device, and of no other.
    if printf '%s' "$cmd" | grep -Eq "$MC_SIM_IOS_EXE" && ! mc_pid_is_evidence "$pid"; then
      ios_ready_map="${ios_ready_map}${pid} ${cmd}
"
    fi
    mc_sim_is_target "$pid" "$cmd" || continue
    targets="$targets $pid"
  done

  # Which booted devices, if any, this pass may shut down. Decided BEFORE the
  # nothing-to-do return below, because a device shutdown is a reclaim in its own
  # right and the unmapped diagnostic below has to be reachable on a pass that
  # reclaims nothing.
  #
  # Gated on SIMPIDS being non-empty rather than running unconditionally: with no
  # sim-classified process at all, memcap knows of nothing to act on and nothing
  # to explain, and the query is a subprocess on every 60-second pass. A booted
  # device always has a launchd_sim, so an empty SIMPIDS means either nothing is
  # booted or the ps snapshot predates the boot -- and in both cases fail closed.
  if [ "$mobile_veto" = 0 ] && [ -n "${SIMPIDS// /}" ]; then
    devices="$(mc_booted_devices)"
  fi
  while IFS= read -r device; do
    [ -n "$device" ] || continue
    udid="${device%% *}"
    name="${device#* }"
    if dev_pid="$(mc_sim_device_pid "$udid" "$ios_ready_map")"; then
      shutdowns="${shutdowns}${udid} ${dev_pid} ${name}
"
      continue
    fi
    # Fail closed, both ways round. A device whose launchd_sim is tracked but
    # still inside its grace is held, and the tier3-holding line below already
    # names it -- the pid counted toward `held` in the bookkeeping loop like any
    # other. A device with no launchd_sim in the snapshot at all is the case that
    # has no other voice, so it gets a line: unmapped is not idle, and the whole
    # of this bug was treating a process with no device affinity as proof about a
    # device.
    if ! mc_sim_device_tracked "$udid" "$SIMPIDS"; then
      mc_log_throttled "tier3-device-unmapped-$udid" "tier3: device $udid ($name) is booted but no launchd_sim for it is in this pass's snapshot -- memcap will not shut down a device it cannot prove is idle"
    fi
  done <<EOF
$devices
EOF

  # A ready pid that is not a target is not a failure to report, but a pass that
  # reclaims nothing while pids sit inside the grace used to be the ONLY unlogged
  # return in this function -- and it is the one that fired 1,973 times. A line
  # naming the blocking pid and its idle age turns eleven days of silence into
  # five minutes of diagnosis.
  if [ -z "${targets// /}" ] && [ -z "$shutdowns" ]; then
    if [ "$held" -gt 0 ]; then
      mc_log_throttled "tier3-holding" "tier3: reclaimed nothing -- $held sim pid(s) still inside their idle grace (${grace}s, or ${tree_grace}s for a live session's own processes); longest-idle blocker is pid $held_pid at ${held_age}s"
    fi
    return 0
  fi
  mc_log_throttle_clear "tier3-holding"

  # One `simctl shutdown <UDID>` per device that earned it, never `shutdown all`.
  # `all` was the whole defect: it takes every booted device on the machine on the
  # authority of one pid that need not have anything to do with any of them, and
  # for 38 real shutdowns that pid was SimulatorTrampoline while the device it
  # took down was running somebody's Maestro flow.
  #
  # rc and stderr are captured, because the previous line was written before the
  # command ran and said only "shutdown all": actions.log could not distinguish a
  # device that went down from one that refused, which is the difference between
  # "memcap did this to me" and "memcap tried and something else did".
  # `2>&1 >/dev/null` in that order: stderr into the substitution, stdout away.
  while IFS= read -r device; do
    [ -n "$device" ] || continue
    udid="${device%% *}"
    dev_pid="${device#* }"; dev_pid="${dev_pid%% *}"
    name="${device#* * }"
    if mc_read_idle_stamp "$(mc_sims_idle_stamp "$dev_pid")"; then
      age=$(( now - MC_STAMP_FIRST ))
    else
      age=0
    fi
    if [ "$MC_DRY_RUN" = "1" ]; then
      echo "would shut down device $udid ($name)"
      continue
    fi
    rc=0
    err="$("$MC_XCRUN_BIN" simctl shutdown "$udid" 2>&1 >/dev/null)" || rc=$?
    if [ "$rc" = "0" ]; then
      mc_log "tier3: xcrun simctl shutdown $udid ($name) -- launchd_sim pid $dev_pid CPU-flat for ${age}s"
    else
      mc_log "tier3: simctl shutdown $udid ($name) failed (rc $rc): $(printf '%s' "$err" | head -1)"
    fi
  done <<EOF
$shutdowns
EOF

  # A booted device shut down above is a complete reclaim on its own; there does
  # not have to be a signalable process left over.
  [ -n "${targets// /}" ] || return 0

  for pid in $targets; do
    # Audit detail beyond mc_kill_pids' own log line: which pid, why it was
    # judged idle, and how long it had been flat -- what makes a reclaim
    # reviewable after the fact rather than just a bare kill record.
    if mc_read_idle_stamp "$(mc_sims_idle_stamp "$pid")"; then
      mc_log "tier3: reclaiming pid $pid -- idle $((now - MC_STAMP_FIRST))s, CPU flat at ~${MC_STAMP_CPU}s accumulated"
    else
      mc_log "tier3: reclaiming pid $pid -- idle stamp unreadable at kill time"
    fi
  done
  mc_kill_pids "$targets" "tier3 idle simulator" sims
  return 0
}

# --- The pass -----------------------------------------------------------------

# Contract C5. One word, next to the heartbeat, on every path that stamps it.
# The heartbeat answers "is the daemon ticking"; this answers "is it enforcing",
# and `status` renders anything but `enforced` as a remedy line.
mc_finish_pass() {
  local dir outcome="$1"
  dir="$(mc_state_dir)"
  mc_stamp_heartbeat
  mkdir -p "$dir" 2>/dev/null
  [ "${MC_STATE_WRITE_FAILED:-0}" = 1 ] && outcome=state-error
  mc_state_write "$dir/last-outcome" "$outcome" || :
  return 0
}

# One unconditional line per hour, whatever else the pass decides.
#
# The 28-hour outage that motivated the heartbeat was only detectable because a
# since-removed log line happened to fire every 30 minutes. Since v0.3.0 --
# correctly, since those lines were 94% of the file -- everything routine is
# throttled or conditional, so the same outage would now look identical to a
# quiet week in actions.log. The pass count is the part that carries information:
# "alive (60 passes since last mark)" is a healthy hour, "alive (3 passes)" is a
# daemon that has been restarting or stalling.
mc_watch_liveness() {
  local dir count mark now every elapsed
  dir="$(mc_state_dir)"
  mkdir -p "$dir" 2>/dev/null || return 0
  count=$(cat "$dir/pass-count" 2>/dev/null) || count=0
  case "$count" in ''|*[!0-9]*) count=0 ;; esac
  [ ${#count} -le 18 ] || count=0
  count=$((count + 1))
  mc_state_write "$dir/pass-count" "$count" || :
  mark=$(cat "$dir/liveness-mark" 2>/dev/null) || mark=0
  case "$mark" in ''|*[!0-9]*) mark=0 ;; esac
  [ ${#mark} -le 18 ] || mark=0
  now=$(date +%s)
  every=$(mc_enf_num "${LIVENESS_SEC:-3600}" 3600 LIVENESS_SEC)
  # No mark yet (fresh install, or a state directory that was cleared). `now -
  # 0` is the entire epoch, so every elapsed figure derived from it would be
  # nonsense -- start the clock and say so instead.
  if [ "$mark" = "0" ]; then
    mc_state_write "$dir/liveness-mark" "$now" || :
    mc_state_write "$dir/pass-count" 0 || :
    mc_log "watch: alive (memcap ${MEMCAP_VERSION:-unknown}, liveness clock started)"
    return 0
  fi
  [ $((now - mark)) -lt "$every" ] && return 0
  elapsed=$((now - mark))
  mc_state_write "$dir/liveness-mark" "$now" || :
  mc_state_write "$dir/pass-count" 0 || :
  # The interval is spelled out because the raw count is not self-interpreting
  # and the old comment here guessed it wrong: it called 60 passes "a healthy
  # hour", while a healthy hour on the author's Mac is 46-58. StartInterval is
  # not a guarantee -- launchd coalesces timers, and two consecutive intervals
  # measured on an awake machine were 73 seconds apart, not 60. Someone reading
  # "alive (47 passes)" against the old comment would diagnose a stalling daemon
  # that was working perfectly. A real stall shows up in the interval, which is
  # now stated rather than left to be inferred from a number nobody has a
  # baseline for.
  mc_log "watch: alive (memcap ${MEMCAP_VERSION:-unknown}, $count passes in ${elapsed}s -- one every $((elapsed / (count > 0 ? count : 1)))s)"
  return 0
}

mc_watch() {
  local total cap cap_default docker_budget docker_default agents_budget
  local agent_net_gb over free soft min_free outcome drift
  local agent_gb docker_gb combined_gb gross_over
  # The combined-over-cap attribution (below): which of Docker and the simulators
  # the overage actually belongs to. Declared here with everything else mc_watch
  # owns -- bash is dynamically scoped, so a name left undeclared here is a name
  # every function mc_watch calls can see and shadow.
  local overage sim_gb docker_excess docker_covers docker_none
  local docker_against docker_over docker_remedy docker_note

  local sample initial_fault=0 pressure=0
  MC_STATE_WRITE_FAILED=0

  mc_watch_liveness

  if mc_is_paused; then
    echo "memcap is paused (memcap on to resume)"
    mc_finish_pass paused
    return 0
  fi

  # config.sh deliberately does not guard `watch` with mc_refuse_if_broken, so
  # that this path is reached and the heartbeat and outcome still get stamped: a
  # daemon that is running but refusing must not look like a dead one.
  if [ "${MC_CONFIG_BROKEN:-0}" = "1" ]; then
    mc_log "watch: refusing to enforce -- $(mc_config_file) is broken, and enforcing on defaults would mean acting on a policy the user never chose"
    echo "memcap: not enforcing -- $(mc_config_file) is broken. Check it with: bash -n $(mc_config_file)" >&2
    mc_finish_pass refused-badconfig
    return 1
  fi

  # One snapshot of "what counts as evidence of active work" per pass, shared by
  # every tier -- both the flat tool set and the process table the held-resource
  # ancestry walk reads.
  unset MC_VETO_EVIDENCE_CACHE
  unset MC_PROC_TABLE

  mc_snapshot_capture
  sample="$MC_CAPTURE_SNAPSHOT"
  unset AGENT_KB DOCKER_KB SIM_KB AGENTPIDS PROTECTEDPIDS SIMPIDS ORPHANS DEVPIDS
  eval "$(printf '%s\n' "$sample" | mc_classify)"
  # Fail closed on a classifier that produced nothing: an `eval` of an empty
  # string leaves every variable below unset, and `set -u` would take the pass
  # down somewhere less obvious than here.
  if [ -z "${AGENT_KB+x}" ] || [ -z "${PROTECTEDPIDS+x}" ] || [ -z "${SIMPIDS+x}" ]; then
    mc_log "watch: refusing to act -- the classifier produced no assignments, so nothing about this machine is known"
    mc_finish_pass degraded-measurement
    return 1
  fi
  mc_record_roots "$AGENTPIDS"

  total=$(mc_total_ram_gb)
  cap_default=$(mc_cap_gb "$total")
  cap=$(mc_enf_num "${TOTAL_BUDGET_GB:-$cap_default}" "$cap_default" TOTAL_BUDGET_GB)
  docker_default=$(mc_docker_gb "$cap")
  docker_budget=$(mc_enf_num "${DOCKER_BUDGET_GB:-$docker_default}" "$docker_default" DOCKER_BUDGET_GB)
  agents_budget=$((cap - docker_budget))
  # A hand-edited config can set DOCKER_BUDGET_GB >= TOTAL_BUDGET_GB. That makes
  # agents_budget zero or negative, which would make every pass below believe
  # agents are permanently over budget and fire tier 2 forever. Refuse to act at
  # all rather than enforce against a budget that cannot be satisfied.
  if [ "$agents_budget" -lt 1 ]; then
    mc_log "watch: refusing to act -- DOCKER_BUDGET_GB ($docker_budget) leaves no room in TOTAL_BUDGET_GB ($cap)"
    echo "memcap.conf is misconfigured: DOCKER_BUDGET_GB ($docker_budget) >= TOTAL_BUDGET_GB ($cap). Fix memcap.conf; not enforcing."
    mc_finish_pass refused-misconfig
    return 1
  fi
  # Same divergence `status` renders, in the audit trail: a machine whose Docker
  # ceiling was never applied has been enforcing against the wrong agent budget
  # for as long as that has been true, and actions.log is where the "why was
  # memcap over budget all week" question gets answered afterwards.
  if command -v mc_docker_ceiling_drift >/dev/null 2>&1; then
    # Called IN-PROCESS, with the message read back out of a global, rather than
    # through the command substitution this used to be. A subshell returns the
    # drift text and nothing else, and "nothing else" is where the whole of
    # v0.5.1's silence lived: launchd denies this process access to
    # ~/Library/Group Containers, the read failed with EPERM on every pass, and a
    # failed read is indistinguishable from "the ceilings agree" once it has been
    # squeezed through an exit status. This line logged ZERO times in 8 days
    # while `memcap status`, run from a terminal, printed the drift every time.
    mc_docker_ceiling_drift "$docker_budget" >/dev/null || :
    drift="${MC_DOCKER_CEILING_DRIFT:-}"
    if [ -n "$drift" ]; then
      mc_log_throttled "docker-ceiling-drift" "watch: $drift"
    else
      mc_log_throttle_clear "docker-ceiling-drift"
    fi
    # The blind case gets its own line and its own key. It is NOT a drift -- memcap
    # does not know whether the ceilings agree -- and reporting it as one would be
    # inventing a number. It is also not silence, which is what the previous
    # version amounted to: a check that cannot run must say so, once, rather than
    # look like a check that ran and found nothing. Only when there is no cached
    # reading to fall back on, and only when a ceiling is actually being asked for
    # (DOCKER_BUDGET_GB=0 means Docker is unmanaged by choice, so there is no
    # check to be blind about).
    if [ "${MC_DOCKER_CEILING_UNREADABLE:-0}" = "1" ] && [ "$docker_budget" -gt 0 ]; then
      mc_log_throttled "docker-ceiling-unreadable" "watch: cannot read Docker's settings store from the background service -- macOS denies launchd agents access to ~/Library/Group Containers -- so the VM-ceiling check is blind here until 'memcap status' has been run once from a terminal"
    else
      mc_log_throttle_clear "docker-ceiling-unreadable"
    fi
  fi

  # Net of sims, not the gross AGENT_KB: sims still count toward the combined cap
  # and are still reclaimed by tier 3, but tier 1's soft trigger and tier 2's
  # kill decision must not fire on an overage that belongs to a simulator neither
  # tier can touch.
  agent_net_gb=$(mc_gb "$(mc_agent_net_kb "$AGENT_KB" "$SIM_KB")")
  free=$(mc_free_pct)
  soft=$(mc_enf_frac "${SOFT_TRIGGER:-0.80}" 0.80 SOFT_TRIGGER)
  min_free=$(mc_enf_num "${MIN_FREE_PCT:-15}" 15 MIN_FREE_PCT)

  initial_fault="${MC_MEASURE_FAULT:-0}"
  if command -v mc_reap_oversized >/dev/null 2>&1; then
    mc_reap_oversized "$sample"
    if [ "${MC_JOBS_RECLAIMED:-0}" = 1 ]; then
      # Never select a second, unrelated victim using the memory just reclaimed.
      mc_snapshot_capture
      sample="$MC_CAPTURE_SNAPSHOT"
      unset AGENT_KB DOCKER_KB SIM_KB AGENTPIDS PROTECTEDPIDS SIMPIDS ORPHANS DEVPIDS
      eval "$(printf '%s\n' "$sample" | mc_classify)"
      if [ -z "${AGENT_KB+x}" ] || [ -z "${DOCKER_KB+x}" ] || [ -z "${SIMPIDS+x}" ]; then
        mc_finish_pass degraded-measurement
        return 1
      fi
      [ "${MC_MEASURE_FAULT:-0}" = 1 ] && initial_fault=1
      agent_net_gb=$(mc_gb "$(mc_agent_net_kb "$AGENT_KB" "$SIM_KB")")
    fi
  fi
  if command -v mc_host_pressure >/dev/null 2>&1; then
    mc_host_pressure
    mc_host_report
    pressure=$(awk -v a="$AGENT_KB" -v d="$DOCKER_KB" -v c="$cap" -v f="$free" -v m="$min_free" -v h="$MC_HOST_PRESSURE" \
      'BEGIN {print (a+d>c*1048576 || f<m || h==1) ? 1 : 0}')
    if [ "$pressure" = 1 ]; then
      mc_pressure_capture "$sample" "combined $(mc_gb "$((AGENT_KB+DOCKER_KB))") GB / $cap GB; agent net $agent_net_gb GB / $agents_budget GB; free-ish RAM $free%"
    else
      mc_pressure_recovered
    fi
  fi

  over=$(awk -v a="$agent_net_gb" -v b="$agents_budget" -v t="$soft" 'BEGIN{print (a > b*t) ? 1 : 0}')
  if [ "$over" = "1" ] || [ "$free" -lt "$min_free" ]; then
    mc_reap_orphans
    mc_snapshot_capture
    sample="$MC_CAPTURE_SNAPSHOT"
    unset AGENT_KB DOCKER_KB SIM_KB AGENTPIDS PROTECTEDPIDS SIMPIDS ORPHANS DEVPIDS
    eval "$(printf '%s\n' "$sample" | mc_classify)"
    if [ -z "${AGENT_KB+x}" ] || [ -z "${DOCKER_KB+x}" ] || [ -z "${SIMPIDS+x}" ]; then
      mc_log "watch: refusing further cleanup -- reclassification produced no assignments"
      mc_finish_pass degraded-measurement
      return 1
    fi
    [ "${MC_MEASURE_FAULT:-0}" = 1 ] && initial_fault=1
    agent_net_gb=$(mc_gb "$(mc_agent_net_kb "$AGENT_KB" "$SIM_KB")")
  fi

  mc_reap_sims

  MC_POLL_RECLAIMED=0
  if command -v mc_reap_poll_loops >/dev/null 2>&1; then
    mc_reap_poll_loops
  fi
  MC_BOOT_RECLAIMED=0
  if command -v mc_reap_boot_timeouts >/dev/null 2>&1; then
    mc_reap_boot_timeouts
  fi
  if command -v mc_reap_idle_helpers >/dev/null 2>&1; then
    mc_reap_idle_helpers
  fi
  if [ "${MC_GC_RECLAIMED:-0}" = 1 ] || [ "$MC_BOOT_RECLAIMED" = 1 ] || [ "$MC_POLL_RECLAIMED" = 1 ]; then
    mc_snapshot_capture
    sample="$MC_CAPTURE_SNAPSHOT"
    unset AGENT_KB DOCKER_KB SIM_KB AGENTPIDS PROTECTEDPIDS SIMPIDS ORPHANS DEVPIDS
    eval "$(printf '%s\n' "$sample" | mc_classify)"
    if [ -z "${AGENT_KB+x}" ] || [ -z "${DOCKER_KB+x}" ] || [ -z "${SIMPIDS+x}" ]; then
      mc_finish_pass degraded-measurement
      return 1
    fi
    [ "${MC_MEASURE_FAULT:-0}" = 1 ] && initial_fault=1
    agent_net_gb=$(mc_gb "$(mc_agent_net_kb "$AGENT_KB" "$SIM_KB")")
  fi

  over=$(awk -v a="$agent_net_gb" -v b="$agents_budget" 'BEGIN{print (a > b) ? 1 : 0}')
  if [ "$over" = "1" ]; then
    if [ "$initial_fault" = 1 ]; then
      mc_log_throttled tier2-measurement "tier2: declining -- footprint measurement unreliable; safe orphan cleanup remains available"
    else
      mc_log_throttle_clear tier2-measurement
      mc_kill_over_budget
    fi
  fi
  # Combined diagnostics must run even when tier 2 was selected and vetoed.
  {
    # Net is fine, so tier 2 correctly declines -- but the machine can still sit
    # over its COMBINED cap when the excess is simulator/browser memory, which
    # only tier 3 (not tier 2) can reclaim. Pre-C1 this state was loud and wrong
    # (tier 2 killed a dev server that could never fix it, every pass); post-C1
    # it is correct but was entirely silent, which reads as broken for the one
    # state this tool exists to handle. Log it throttled -- unthrottled, this
    # line alone was 47% of a day's real actions.log -- and notify once.
    agent_gb=$(mc_gb "$AGENT_KB")
    docker_gb=$(mc_gb "$DOCKER_KB")
    combined_gb=$(awk -v a="$agent_gb" -v d="$docker_gb" 'BEGIN{printf "%.2f", a+d}')
    gross_over=$(awk -v c="$combined_gb" -v cap="$cap" 'BEGIN{print (c > cap) ? 1 : 0}')
    if [ "$gross_over" = "1" ]; then
      # WHICH excess, not just that there is one. This line blamed simulators
      # unconditionally for 186 occurrences since 08-27, including the morning it
      # read: combined 17.20 GB against a 16 GB cap, agents net of sims 9.45 GB,
      # sims about 1.15 GB -- and Docker at 6.6 GB against a 4 GB budget, because
      # the ceiling had never been applied. Tier 3 could have reclaimed every
      # simulator on the machine and it would still have been over the cap. The
      # line promised a reclaim that could not happen and never named the actual
      # cause, so the one action that would have fixed it (memcap docker apply)
      # was never suggested. Attribute the overage instead, and say only what the
      # attributed part can actually do.
      #
      # The arithmetic identity that makes this exhaustive: cap is
      # agents_budget + docker_budget by construction, and this branch runs only
      # when agent_net <= agents_budget, so
      #   overage = combined - cap <= sim_gb + max(0, docker_gb - docker_budget).
      # One of the two components therefore always covers it, or both contribute.
      overage=$(awk -v c="$combined_gb" -v cap="$cap" 'BEGIN{printf "%.2f", c - cap}')
      sim_gb=$(mc_gb "$SIM_KB")
      docker_excess=$(awk -v d="$docker_gb" -v b="$docker_budget" 'BEGIN{e = d - b; if (e < 0) e = 0; printf "%.2f", e}')
      docker_covers=$(awk -v e="$docker_excess" -v o="$overage" 'BEGIN{print (e >= o) ? 1 : 0}')
      docker_none=$(awk -v e="$docker_excess" 'BEGIN{print (e <= 0) ? 1 : 0}')
      # DOCKER_BUDGET_GB=0 is "memcap is not managing Docker", not "Docker may use
      # zero" -- status.sh renders it as "no ceiling (unmanaged)" for the same
      # reason. Telling that user to run `memcap docker apply` would be telling
      # them to apply a 0 GB ceiling, so the remedy names the missing setting
      # first. Both strings are built once and shared by the two Docker variants.
      if [ "$docker_budget" -gt 0 ]; then
        docker_against="against its ${docker_budget} GB budget"
        docker_over="Docker is ${docker_excess} GB over its ${docker_budget} GB budget"
        docker_remedy="memcap docker apply"
      else
        docker_against="with no ceiling asked for (DOCKER_BUDGET_GB=0)"
        docker_over="Docker is holding ${docker_gb} GB with no ceiling asked for (DOCKER_BUDGET_GB=0)"
        docker_remedy="set DOCKER_BUDGET_GB in memcap.conf, then: memcap docker apply"
      fi
      # The drift, when it is known, belongs on the Docker variants specifically:
      # "Docker is over its budget" and "the ceiling in your config was never
      # applied" are the same sentence read from two ends, and a user seeing the
      # first without the second has no way to get from one to the other.
      # `${drift:-}`, not `$drift`: the block that sets it is guarded on docker.sh
      # having been sourced at all, which `bin/memcap watch` always does and a
      # test sourcing the modules by hand may not.
      docker_note=""
      [ -n "${drift:-}" ] && docker_note=" -- ${drift}"
      if [ "$over" = "1" ]; then
        mc_log_throttled "combined-over-cap" "watch: combined ${combined_gb} GB exceeds the ${cap} GB cap -- agents net ${agent_net_gb} GB / ${agents_budget} GB, Docker ${docker_gb} GB / ${docker_budget} GB, simulators/browser ${sim_gb} GB; protected or active work may prevent reclaim${docker_note}"
        [ "$MC_DRY_RUN" = "1" ] || mc_notify "Over your ${cap} GB budget: combined ${combined_gb} GB. Protected or active work may prevent cleanup; inspect memcap status and pressure snapshots."
      elif [ "$docker_covers" = "1" ]; then
        mc_log_throttled "combined-over-cap" "watch: combined ${combined_gb} GB exceeds the ${cap} GB cap -- the excess is Docker's: ${docker_gb} GB ${docker_against}. No tier reclaims Docker memory; enforce the ceiling with: ${docker_remedy}${docker_note}"
        [ "$MC_DRY_RUN" = "1" ] || mc_notify "Over your ${cap} GB combined budget: Docker is holding ${docker_gb} GB ${docker_against}. No tier can reclaim Docker memory -- ${docker_remedy}"
      # Docker being within its budget is decisive on its own, whether or not
      # sim_covers agrees. The identity above says the simulators cover the
      # overage when Docker does not -- but agent_gb, sim_gb and agent_net_gb are
      # each rounded to two decimals independently and overage comes from the
      # rounded combined figure, so "0.99 >= 1.00" can be false by a rounding
      # penny while the arithmetic it stands for is true. Requiring sim_covers
      # here sent exactly that fixture to the variant below, which then read
      # "Docker is 0.00 GB over its 4 GB budget ... memcap docker apply": a false
      # Docker blame from the change meant to end misattribution.
      elif [ "$docker_none" = "1" ]; then
        mc_log_throttled "combined-over-cap" "watch: combined ${combined_gb} GB exceeds the ${cap} GB cap, but agents net of sims are ${agent_net_gb} GB / ${agents_budget} GB budget -- the excess is simulator/browser memory tier 2 cannot reclaim by killing a dev server; tier 3 will reclaim it once it has been idle past its grace"
        [ "$MC_DRY_RUN" = "1" ] || mc_notify "Over your ${cap} GB combined budget from simulator/browser memory -- tier 2 won't kill a dev server for it, and tier 3 will reclaim it once it has been idle long enough."
      else
        mc_log_throttled "combined-over-cap" "watch: combined ${combined_gb} GB exceeds the ${cap} GB cap -- ${docker_over} and ${sim_gb} GB is simulator/browser memory; tier 3 can reclaim at most the latter, and only once it has been idle past its grace; the Docker part needs: ${docker_remedy}${docker_note}"
        [ "$MC_DRY_RUN" = "1" ] || mc_notify "Over your ${cap} GB combined budget: ${docker_over}, and ${sim_gb} GB is simulator/browser memory. Tier 3 can reclaim only the simulators -- the Docker part needs ${docker_remedy}"
      fi
    else
      mc_log_throttle_clear "combined-over-cap"
    fi
  }

  # Preserve any fault from either sample; later healthy data cannot retroactively
  # make a decision based on an earlier unreliable sample trustworthy.
  if [ "$MC_DRY_RUN" = "1" ]; then
    # Reported ahead of a degraded measurement deliberately: "nothing was
    # enforced at all" is the more complete description of the pass, and a real
    # service pass never sets MC_DRY_RUN.
    outcome=dry-run
  elif [ "$initial_fault" = "1" ] || [ "${MC_MEASURE_FAULT:-0}" = "1" ]; then
    # FAULT, not DEGRADED (C4 as amended). DEGRADED is also set by a deliberate
    # MC_NO_TOP=1, which is a documented escape hatch -- keying the outcome on it
    # would turn the user's own choice into a refusal to enforce and make
    # `status` shout about a setting they typed themselves. FAULT means the
    # fallback to ps RSS was not asked for.
    #
    # The wording comes from mc_measure_summary rather than being composed here,
    # so a fault's description stays with the code that can produce it.
    if command -v mc_measure_summary >/dev/null 2>&1; then
      mc_log_throttled "measure-fault" "watch: one or more footprint snapshots were unreliable; tier 2 withheld (latest: $(mc_measure_summary))"
    fi
    outcome=degraded-measurement
  else
    mc_log_throttle_clear "measure-fault"
    if [ "$MC_STATE_WRITE_FAILED" = 1 ]; then outcome=state-error
    elif [ "${gross_over:-0}" = 1 ]; then outcome="over-budget"
    elif [ "${MC_HOST_PRESSURE:-0}" = 1 ]; then outcome=host-pressure
    else outcome=enforced; fi
  fi
  mc_finish_pass "$outcome"
  return 0
}
