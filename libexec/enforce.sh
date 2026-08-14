#!/usr/bin/env bash
set -uo pipefail

MC_DRY_RUN="${MC_DRY_RUN:-0}"

# Every pid memcap kills passes through here, so the protection filter lives here too
# rather than in one tier. A tier-2 subtree walk is role-blind: if a dev server has an
# agent CLI anywhere beneath it -- an ordinary shape in agentic workflows -- a naive
# walk would kill the agent along with the server.
mc_self_ancestry() {
  local p="$$" out=" $$ " i=0
  while [ "$i" -lt 8 ]; do
    p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
    [ -z "$p" ] && break
    [ "$p" = "0" ] && break
    [ "$p" = "1" ] && break
    out="$out$p "
    i=$((i + 1))
  done
  printf '%s' "$out"
}

mc_filter_protected() {
  local pid out="" self
  self=$(mc_self_ancestry)
  for pid in $1; do
    case " ${AGENTPIDS:-} " in *" $pid "*) continue ;; esac
    case "$self" in *" $pid "*) continue ;; esac
    out="$out $pid"
  done
  printf '%s' "$out"
}

mc_kill_pids() {
  local pids reason="$2" p alive
  pids=$(mc_filter_protected "$1")
  [ -z "${pids// /}" ] && return 0
  if [ "$MC_DRY_RUN" = "1" ]; then
    echo "would kill ($reason): $pids"; return 0
  fi
  for p in $pids; do
    mc_log "$reason: $(ps -o pid=,rss=,command= -p "$p" 2>/dev/null | cut -c1-160)"
  done
  # shellcheck disable=SC2086
  kill -TERM $pids 2>/dev/null
  sleep 2
  alive=""
  for p in $pids; do kill -0 "$p" 2>/dev/null && alive="$alive $p"; done
  # shellcheck disable=SC2086
  [ -n "${alive// /}" ] && kill -KILL $alive 2>/dev/null
  return 0
}

mc_descendants() {
  local pid="$1" c
  printf '%s\n' "$pid"
  for c in $(pgrep -P "$pid" 2>/dev/null); do mc_descendants "$c"; done
}

# macOS ps has no `etimes` (elapsed seconds) keyword -- that's Linux/procps only.
# `ps -o etimes=` fails on macOS with "keyword not found" and dumps the valid-keyword
# list to stdout instead, which is not a number. Asking `[ "$age" -lt N ]` to compare
# that against TIER2_MIN_AGE_SEC does not fail closed: `[` errors on the non-integer
# and the age gate that is supposed to protect a fresh build ends up letting it
# through. macOS only gives `etime`, formatted `[[dd-]hh:]mm:ss` (e.g. "00:04",
# "01:02:03", "2-01:02:03"); this converts that string to plain seconds.
mc_etime_secs() {
  local raw="${1//[[:space:]]/}" days=0 rest h=0 m s
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
  case "$days$h$m$s" in *[!0-9]*) return 1 ;; esac
  echo $(( (10#$days * 86400) + (10#$h * 3600) + (10#$m * 60) + 10#$s ))
}

# Tier 1: parent is dead, so no live session and no terminal owns it. Provably safe.
#
# A root recorded in the state file was safe AT RECORD TIME. It is re-validated with
# mc_root_is_safe here, immediately before it is used to select a kill target, because
# the directory it names could have been replaced by a symlink in the meantime (TOCTOU).
# Trusting the state file blindly would let a since-swapped root match against, and
# kill, a process it should never have been allowed to touch.
mc_reap_orphans() {
  local pid keep="" root matched
  for pid in $ORPHANS; do
    matched=0
    for root in $(mc_sweep_roots); do
      mc_root_is_safe "$root" || continue
      ps -o command= -p "$pid" 2>/dev/null | grep -qF "$root" && matched=1 && break
    done
    [ "$matched" = "1" ] && keep="$keep $pid"
  done
  [ -n "${keep// /}" ] && mc_kill_pids "$keep" "tier1 orphan"
  return 0
}

# Tier 2: long-lived dev servers only. A dev server runs for hours; a build runs for
# seconds. The age gate is what stops a build from ever being the victim.
mc_kill_over_budget() {
  local pid age_raw age ranked="" kb
  for pid in $DEVPIDS; do
    age_raw=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')
    age=$(mc_etime_secs "$age_raw") || continue
    [ "$age" -lt "${TIER2_MIN_AGE_SEC:-300}" ] && continue
    kb=$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ')
    ranked="$ranked$kb $pid
"
  done
  if [ -z "${ranked// /}" ]; then
    mc_log "tier2: over budget but every candidate is younger than ${TIER2_MIN_AGE_SEC:-300}s -- not touching active work"
    mc_notify "Agents over budget. Only fresh builds running, so nothing was killed."
    return 0
  fi
  pid=$(printf '%s' "$ranked" | sort -rn | head -1 | awk '{print $2}')
  mc_kill_pids "$(mc_descendants "$pid" | tr '\n' ' ')" "tier2 over-budget dev server"
  mc_notify "memcap killed a leaked dev server to stay inside your budget."
  return 0
}

# Tier 3: only when no agent session is alive, and never during hands-on mobile work.
mc_hands_on_mobile() {
  pgrep -qf '/Xcode\.app/Contents/MacOS/Xcode' 2>/dev/null && return 0
  pgrep -qf 'Android Studio\.app/Contents/MacOS' 2>/dev/null && return 0
  return 1
}

mc_no_live_session() {
  local pid
  for pid in $AGENTPIDS; do kill -0 "$pid" 2>/dev/null && return 1; done
  return 0
}

mc_reap_sims() {
  local pid targets=""
  mc_no_live_session || return 0
  mc_hands_on_mobile && return 0
  if [ "$MC_DRY_RUN" != "1" ] && xcrun simctl list devices booted 2>/dev/null | grep -q Booted; then
    mc_log "tier3: xcrun simctl shutdown all"
    xcrun simctl shutdown all >/dev/null 2>&1
  fi
  for pid in $SIMPIDS; do
    ps -o command= -p "$pid" 2>/dev/null |
      grep -Eq 'qemu-system|/emulator( |$)|emulator64|ms-playwright|headless_shell|\.maestro/lib' || continue
    targets="$targets $pid"
  done
  [ -n "${targets// /}" ] && mc_kill_pids "$targets" "tier3 idle simulator"
  return 0
}

mc_watch() {
  if mc_is_paused; then echo "memcap is paused (memcap on to resume)"; return 0; fi
  local total cap docker_budget agents_budget agent_gb over free
  eval "$(mc_ps_snapshot | mc_classify)"
  mc_record_roots "$AGENTPIDS"

  total=$(mc_total_ram_gb)
  cap="${TOTAL_BUDGET_GB:-$(mc_cap_gb "$total")}"
  docker_budget="${DOCKER_BUDGET_GB:-$(mc_docker_gb "$cap")}"
  agents_budget=$((cap - docker_budget))
  # A hand-edited config can set DOCKER_BUDGET_GB >= TOTAL_BUDGET_GB. That makes
  # agents_budget zero or negative, which would make every pass below believe agents
  # are permanently over budget and fire tier 2 forever. Refuse to act at all rather
  # than enforce against a budget that cannot be satisfied.
  if [ "$agents_budget" -lt 1 ]; then
    mc_log "watch: refusing to act -- DOCKER_BUDGET_GB ($docker_budget) leaves no room in TOTAL_BUDGET_GB ($cap)"
    echo "memcap.conf is misconfigured: DOCKER_BUDGET_GB ($docker_budget) >= TOTAL_BUDGET_GB ($cap). Fix memcap.conf; not enforcing."
    return 1
  fi
  agent_gb=$(mc_gb "$AGENT_KB")
  free=$(mc_free_pct)

  over=$(awk -v a="$agent_gb" -v b="$agents_budget" -v t="${SOFT_TRIGGER:-0.80}" 'BEGIN{print (a > b*t) ? 1 : 0}')
  if [ "$over" = "1" ] || [ "$free" -lt "${MIN_FREE_PCT:-15}" ]; then
    mc_reap_orphans
    eval "$(mc_ps_snapshot | mc_classify)"
    agent_gb=$(mc_gb "$AGENT_KB")
  fi

  mc_reap_sims

  over=$(awk -v a="$agent_gb" -v b="$agents_budget" 'BEGIN{print (a > b) ? 1 : 0}')
  [ "$over" = "1" ] && mc_kill_over_budget
  return 0
}
