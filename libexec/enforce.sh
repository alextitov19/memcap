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

# Return status signals whether a real kill happened: 0 only when pids were actually
# sent a signal. Dry run and "nothing left after protection" both return 1, so a
# caller that gates a "killed" notification on this cannot claim one that never
# happened -- see mc_kill_over_budget below.
mc_kill_pids() {
  local pids reason="$2" p alive
  pids=$(mc_filter_protected "$1")
  [ -z "${pids// /}" ] && return 1
  if [ "$MC_DRY_RUN" = "1" ]; then
    echo "would kill ($reason): $pids"; return 1
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
# A root recorded in the state file was safe AT RECORD TIME. It is re-canonicalized
# here, immediately before it is used to select a kill target, and matched against
# the stored string EXACTLY -- not just re-validated as "still resolves somewhere
# safe" -- because the directory it names could have been replaced by a symlink in
# the meantime (TOCTOU). mc_root_is_safe alone cannot catch every case: a root
# swapped to point at a DIFFERENT directory that also happens to be 2+ levels under
# HOME would still pass that check, letting a since-redirected root match against,
# and kill, a process it should never have been allowed to touch. Comparing the
# fresh canonical form to what was recorded catches a redirect either way, not just
# the ones that happen to land somewhere unsafe.
mc_reap_orphans() {
  local pid keep="" root real cmd matched
  for pid in $ORPHANS; do
    matched=0
    for root in $(mc_sweep_roots); do
      real=$(mc_canonicalize "$root") || continue
      [ "$real" = "$root" ] || continue
      mc_root_is_safe "$root" || continue
      # Padded with spaces so a root that is the process's ENTIRE command (no
      # trailing path segment) still matches at the boundary, the same trick
      # mc_self_ancestry uses. Matching "$root/" or "$root " -- not a bare
      # substring -- keeps root `~/dev/foo` from also matching `~/dev/foobar`,
      # or a process that merely names the root somewhere in an argument with
      # no separator after it.
      cmd=" $(ps -o command= -p "$pid" 2>/dev/null) "
      case "$cmd" in
        *"$root/"*|*"$root "*) matched=1 ;;
      esac
      [ "$matched" = "1" ] && break
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
    # mc_footprint_kb, not ps RSS: the trigger that got tier 2 reached ranks
    # AGENT_KB by physical footprint (measure.sh), which summed RSS over-counts
    # by ~2.5x for a process tree sharing pages. Ranking victims by a different
    # metric than the one that made the decision could select the wrong one.
    kb=$(mc_footprint_kb "$pid")
    # A candidate that exits between the age check and this read leaves kb empty,
    # turning the ranked line into " $pid" instead of "$kb $pid". sort -rn would
    # then parse that bare pid as the sort key -- and a pid number routinely
    # exceeds a real kb value -- so the exited candidate's line could sort ABOVE a
    # legitimate, still-alive one, and awk '{print $2}' extracts nothing from the
    # resulting single-field line. Skip it instead of feeding sort a malformed row.
    [ -n "$kb" ] || continue
    ranked="$ranked$kb $pid
"
  done
  if [ -z "${ranked// /}" ]; then
    mc_log "tier2: over budget but every candidate is younger than ${TIER2_MIN_AGE_SEC:-300}s -- not touching active work"
    mc_notify "Agents over budget. Only fresh builds running, so nothing was killed."
    return 0
  fi
  pid=$(printf '%s' "$ranked" | sort -rn | head -1 | awk '{print $2}')
  # Gated on mc_kill_pids actually killing something: a dry run, or a real pass where
  # mc_filter_protected removed the only candidate, must not tell the user a dev
  # server was killed -- that both contradicts MC_DRY_RUN's contract and burns the
  # 5-minute notification rate limit on a notification that lied.
  mc_kill_pids "$(mc_descendants "$pid" | tr '\n' ' ')" "tier2 over-budget dev server" &&
    mc_notify "memcap killed a leaked dev server to stay inside your budget."
  return 0
}

# Tier 3: only when no agent session is alive, and never during hands-on mobile work.
mc_hands_on_mobile() {
  pgrep -qf '/Xcode\.app/Contents/MacOS/Xcode' 2>/dev/null && return 0
  pgrep -qf 'Android Studio\.app/Contents/MacOS' 2>/dev/null && return 0
  pgrep -qf '/Simulator\.app/Contents/MacOS/Simulator' 2>/dev/null && return 0
  return 1
}

mc_no_live_session() {
  local pid
  for pid in $AGENTPIDS; do kill -0 "$pid" 2>/dev/null && return 1; done
  return 0
}

MC_SIM_KILL_PATTERN='qemu-system|/emulator( |$)|emulator64|ms-playwright|headless_shell|\.maestro/lib'

# SIM_IDLE_GRACE_SEC gives a hand-booted simulator a reprieve. Without it, someone
# running Simulator.app or `simctl` directly -- no Xcode open, no agent session -- has
# their simulator killed within one poll. The prototype this tool generalizes stamps
# when sims are first seen idle and waits out the grace before reaping; memcap must too.
#
# One stamp PER TRACKED PID, not one shared file: a single machine-wide stamp meant a
# simulator booted by hand inherited whatever timestamp an unrelated, already-idle sim
# process (a stale Playwright browser, say) had accumulated, and could be reaped with
# none of its own grace. Each pid earns its own clock starting the moment it is first
# seen idle, and the whole reap -- not just that pid -- waits until EVERY currently
# tracked pid has individually cleared the grace. That is more conservative than
# reaping each pid the instant its own clock allows (a genuinely-idle process now
# waits on a newer sibling too), but it is what actually protects the newer one, and
# tier 3 is the soft, best-effort tier to begin with.
mc_sims_idle_dir() { printf '%s/sims-idle\n' "$(mc_state_dir)"; }
mc_sims_idle_stamp() { printf '%s/%s\n' "$(mc_sims_idle_dir)" "$1"; }

mc_reap_sims() {
  local pid targets="" dir stamp first now grace all_ready=1
  dir="$(mc_sims_idle_dir)"

  # Throttled, not mc_log: this fires on every single pass for as long as the
  # tool's normal operating state holds (an agent session alive is the premise
  # the whole tool runs under), and unthrottled it drowned the actual kill
  # records -- see mc_log_throttled in common.sh. Each reason's key is cleared
  # the moment its own condition stops holding, so a state change -- the agent
  # session ending and a later one starting, say -- still gets its own line
  # rather than silently reusing a window left over from the last occurrence.
  if ! mc_no_live_session; then
    mc_log_throttled "tier3-agent-alive" "tier3: declining -- an agent session is alive"
    rm -rf "$dir"
    return 0
  fi
  mc_log_throttle_clear "tier3-agent-alive"
  if mc_hands_on_mobile; then
    mc_log_throttled "tier3-hands-on-mobile" "tier3: declining -- hands-on mobile work detected (Xcode, Android Studio, or Simulator.app open)"
    rm -rf "$dir"
    return 0
  fi
  mc_log_throttle_clear "tier3-hands-on-mobile"

  # Prune stamps for pids no longer alive or no longer sim-classified, so a reused
  # pid number cannot inherit a stale idle clock and an exited sim does not leave a
  # stray file behind forever.
  if [ -d "$dir" ]; then
    for stamp in "$dir"/*; do
      [ -e "$stamp" ] || continue
      pid="${stamp##*/}"
      case " $SIMPIDS " in
        *" $pid "*) kill -0 "$pid" 2>/dev/null && continue ;;
      esac
      rm -f "$stamp"
    done
  fi

  if [ -z "${SIMPIDS// /}" ]; then
    return 0
  fi

  mkdir -p "$dir"
  now=$(date +%s)
  grace="${SIM_IDLE_GRACE_SEC:-600}"
  for pid in $SIMPIDS; do
    stamp="$(mc_sims_idle_stamp "$pid")"
    first=$(cat "$stamp" 2>/dev/null || echo 0)
    if [ "$first" = "0" ]; then
      first="$now"
      echo "$first" > "$stamp"
    fi
    [ $((now - first)) -lt "$grace" ] && all_ready=0
  done
  [ "$all_ready" = "0" ] && return 0

  if [ "$MC_DRY_RUN" != "1" ] && xcrun simctl list devices booted 2>/dev/null | grep -q Booted; then
    mc_log "tier3: xcrun simctl shutdown all"
    xcrun simctl shutdown all >/dev/null 2>&1
  fi
  for pid in $SIMPIDS; do
    ps -o command= -p "$pid" 2>/dev/null | grep -Eq "$MC_SIM_KILL_PATTERN" || continue
    targets="$targets $pid"
  done
  [ -n "${targets// /}" ] && mc_kill_pids "$targets" "tier3 idle simulator"
  rm -rf "$dir"
  return 0
}

mc_watch() {
  if mc_is_paused; then echo "memcap is paused (memcap on to resume)"; return 0; fi
  local total cap docker_budget agents_budget agent_net_gb over free
  local agent_gb docker_gb combined_gb gross_over
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
  # Net of sims, not the gross AGENT_KB: sims still count toward the combined cap and
  # are still reclaimed by tier 3, but tier 1's soft trigger and tier 2's kill decision
  # must not fire on an overage that belongs to a simulator neither tier can touch.
  agent_net_gb=$(mc_gb "$(mc_agent_net_kb "$AGENT_KB" "$SIM_KB")")
  free=$(mc_free_pct)

  over=$(awk -v a="$agent_net_gb" -v b="$agents_budget" -v t="${SOFT_TRIGGER:-0.80}" 'BEGIN{print (a > b*t) ? 1 : 0}')
  if [ "$over" = "1" ] || [ "$free" -lt "${MIN_FREE_PCT:-15}" ]; then
    mc_reap_orphans
    eval "$(mc_ps_snapshot | mc_classify)"
    agent_net_gb=$(mc_gb "$(mc_agent_net_kb "$AGENT_KB" "$SIM_KB")")
  fi

  mc_reap_sims

  over=$(awk -v a="$agent_net_gb" -v b="$agents_budget" 'BEGIN{print (a > b) ? 1 : 0}')
  if [ "$over" = "1" ]; then
    mc_kill_over_budget
  else
    # Net is fine, so tier 2 correctly declines -- but the machine can still sit
    # over its COMBINED cap when the excess is simulator/browser memory, which
    # only tier 3 (not tier 2) can reclaim. Pre-C1 this state was loud and wrong
    # (tier 2 killed a dev server that could never fix it, every pass); post-C1 it
    # is correct but was entirely silent -- no log line, no notification -- which
    # reads as broken for the one state this tool exists to handle. Log it
    # throttled -- unthrottled, this line alone was 47% of a day's real
    # actions.log, burying the kill records the file exists for -- and notify
    # once, gated on MC_DRY_RUN the same way I2 gated tier 2's "killed"
    # notification; mc_notify's own 5-minute rate limiter is separate machinery
    # and untouched. Clearing the key the moment the machine drops back under the
    # combined cap means a later re-entry logs immediately rather than being
    # swallowed by a window left over from the last time.
    agent_gb=$(mc_gb "$AGENT_KB")
    docker_gb=$(mc_gb "$DOCKER_KB")
    combined_gb=$(awk -v a="$agent_gb" -v d="$docker_gb" 'BEGIN{printf "%.2f", a+d}')
    gross_over=$(awk -v c="$combined_gb" -v cap="$cap" 'BEGIN{print (c > cap) ? 1 : 0}')
    if [ "$gross_over" = "1" ]; then
      mc_log_throttled "combined-over-cap" "watch: combined ${combined_gb} GB exceeds the ${cap} GB cap, but agents net of sims are ${agent_net_gb} GB / ${agents_budget} GB budget -- the excess is simulator/browser memory tier 2 cannot reclaim by killing a dev server; tier 3 will reclaim it once no agent session is alive"
      [ "$MC_DRY_RUN" = "1" ] || mc_notify "Over your ${cap} GB combined budget from simulator/browser memory -- tier 2 won't kill a dev server for it, and tier 3 will reclaim it once no agent session is alive."
    else
      mc_log_throttle_clear "combined-over-cap"
    fi
  fi
  return 0
}
