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
  local pid keep="" root real cmd matched key pid_cwd pid_cwd_real pid_cwd_tried
  for pid in $ORPHANS; do
    matched=0
    # Both of this pid's identifying facts are resolved ONCE, out here, not per
    # root: neither depends on $root, and both cost a process spawn. Inside the
    # loop they would run orphans x roots times -- 388 orphans against this
    # machine's 12 recorded roots is 4,656 spawns, and at lsof's measured 35.8ms
    # that is a 167-second pass against a 60-second service interval. memcap
    # would be at its slowest in exactly the leak it exists to clean up.
    cmd=" $(ps -o command= -p "$pid" 2>/dev/null) "
    # cwd is resolved lazily -- only the first time a root's argv match has
    # failed -- so the common case (argv matches, usually on the first root)
    # never pays for lsof at all.
    pid_cwd_real=""
    pid_cwd_tried=0
    # `while read`, not `for root in $(mc_sweep_roots)`: word-splitting the
    # command substitution breaks a root containing a space into fragments, and
    # a fragment like `/Users/x/dev/my` (from `/Users/x/dev/my project`) can
    # independently pass both mc_canonicalize and mc_root_is_safe and then
    # match -- for the wrong reason, since it was never the recorded root. This
    # also makes mc_canonicalize's control-character rejection the belt it was
    # meant to be: a root can't reach it pre-mangled by IFS first.
    while IFS= read -r root; do
      # Throttled, per-root: this is the self-healing path (mc_record_roots
      # re-registers live agents' cwds every pass, so a root that fails here
      # once typically starts matching again on its own), but it was previously
      # invisible while it happened. Keyed on the root itself (slashes swapped
      # for underscores -- it becomes a filename) so a different root's skip
      # doesn't share, or get suppressed by, this one's throttle window.
      key=$(printf '%s' "$root" | tr '/' '_')
      real=$(mc_canonicalize "$root") || {
        mc_log_throttled "root-skip-canon-$key" "tier1: skipping sweep root $root -- no longer resolves"
        continue
      }
      if [ "$real" != "$root" ]; then
        mc_log_throttled "root-skip-redirect-$key" "tier1: skipping sweep root $root -- now resolves to $real, not what was recorded (TOCTOU)"
        continue
      fi
      if ! mc_root_is_safe "$root"; then
        mc_log_throttled "root-skip-unsafe-$key" "tier1: skipping sweep root $root -- no longer resolves somewhere safe"
        continue
      fi
      # Padded with spaces so a root that is the process's ENTIRE command (no
      # trailing path segment) still matches at the boundary, the same trick
      # mc_self_ancestry uses. Matching "$root/" or "$root " -- not a bare
      # substring -- keeps root `~/dev/foo` from also matching `~/dev/foobar`,
      # or a process that merely names the root somewhere in an argument with
      # no separator after it.
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
      # one: cwd is the kernel's own record of where the process actually
      # lives, not text the process chose to pass itself, so it cannot draw in
      # something that merely NAMES the root without living under it -- and it
      # only runs at all for a root that already passed the canonical-match and
      # safety gates above, for a pid that classify.sh already restricted to
      # ppid==1 plus the dev-server pattern.
      if [ "$matched" != "1" ]; then
        if [ "$pid_cwd_tried" != "1" ]; then
          pid_cwd_tried=1
          pid_cwd=$(mc_pid_cwd "$pid")
          if [ -n "$pid_cwd" ]; then
            pid_cwd_real=$(mc_canonicalize "$pid_cwd") || pid_cwd_real=""
          fi
        fi
        if [ -n "$pid_cwd_real" ]; then
          case "$pid_cwd_real" in
            "$root"|"$root"/*) matched=1 ;;
          esac
        fi
      fi
      [ "$matched" = "1" ] && break
    done < <(mc_sweep_roots)
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

# `ps -o time=` (accumulated CPU) is NOT the same shape as `ps -o etime=`
# (mc_etime_secs above), despite looking similar at a glance -- confirmed
# empirically, not assumed: a process at 6 days' uptime showed `time` as
# "2960:52.31", never rolling into an hour or day segment the way `etime`
# does ("06-06:50:15" for the same process, same instant). `time` is plain
# MINUTES:SECONDS(.hundredths), with minutes growing unbounded. The
# fractional part is dropped -- sub-second precision doesn't matter against a
# multi-minute grace window.
mc_cputime_secs() {
  local raw="${1//[[:space:]]/}" m s
  [ -z "$raw" ] && return 1
  case "$raw" in
    *:*) m="${raw%%:*}"; s="${raw#*:}" ;;
    *)   return 1 ;;
  esac
  s="${s%%.*}"
  case "$m$s" in *[!0-9]*) return 1 ;; esac
  echo $(( (10#$m * 60) + 10#$s ))
}

# Tier 3: reclaims idle simulators/browsers. Vetoed by hands-on mobile work
# (below, unchanged) and by active mobile tooling actually driving a
# simulator -- xcodebuild and detox are distinctive process names on their
# own (matched on argv[0]/comm via `pgrep -x`, the same "argv[0], not the
# whole command line" rule as everywhere else in this file); expo and
# react-native are commonly launched through node, so argv[0] alone ("node")
# would be too broad, matched instead on the tool name followed by a
# subcommand a casual mention cannot produce; maestro runs as a JVM process,
# identified by the same narrow argv pattern MC_SIM_ARG-equivalent matching
# already uses for it elsewhere.
#
# Escape hatches, same pattern as MC_DOCKER_RUNTIME (docker.sh): both checks
# depend entirely on what's running on the host, with no way to make them
# deterministic in an environment that happens to have one of these processes
# alive for an unrelated reason. Discovered empirically, not hypothetically:
# this development machine runs a maestro MCP server in the background (`java
# ... maestro.cli.AppKt mcp`, unrelated to any simulator use), which matched
# the maestro pattern below and made tier 3's own test suite non-deterministic
# depending on which MCP servers or IDEs happen to be running wherever `bats
# tests/` executes -- the same class of portability bug as an unavailable
# Docker Desktop or a symlinked $HOME in earlier rounds. A plain environment
# variable survives into any subprocess normally, unlike a function-level
# stub, which a real `bin/memcap` invocation's own re-sourcing would clobber.
mc_active_mobile_tooling() {
  if [ -n "${MC_ACTIVE_MOBILE_TOOLING:-}" ]; then
    [ "$MC_ACTIVE_MOBILE_TOOLING" = "1" ]
    return
  fi
  pgrep -qx xcodebuild 2>/dev/null && return 0
  pgrep -qx detox 2>/dev/null && return 0
  pgrep -qf '(^|/| )expo[[:space:]]+(start|run:ios|run:android)' 2>/dev/null && return 0
  pgrep -qf '(^|/| )react-native[[:space:]]+(run-ios|run-android|start)' 2>/dev/null && return 0
  pgrep -qf '\.maestro/lib|maestro\.cli' 2>/dev/null && return 0
  return 1
}

mc_hands_on_mobile() {
  if [ -n "${MC_HANDS_ON_MOBILE:-}" ]; then
    [ "$MC_HANDS_ON_MOBILE" = "1" ]
    return
  fi
  pgrep -qf '/Xcode\.app/Contents/MacOS/Xcode' 2>/dev/null && return 0
  pgrep -qf 'Android Studio\.app/Contents/MacOS' 2>/dev/null && return 0
  pgrep -qf '/Simulator\.app/Contents/MacOS/Simulator' 2>/dev/null && return 0
  return 1
}

# Restores the pre-v0.3.0 behavior for TIER3_REQUIRE_NO_SESSION=1: tier 3
# never reaps while any agent session is alive, full stop. That was the
# original design -- a conservative stand-in chosen because sims can't be
# attributed to a session by process tree (CoreSimulatorService owns them),
# not a deliberate "only when idle AND alone" rule. In 1,643 real passes on a
# machine that always has a session open it fired zero times: tier 3 was both
# dead and load-bearing for the sim memory the budget keeps counting. Kept as
# an opt-in for anyone who wants the old, maximally conservative posture.
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
  local pid targets="" dir stamp first cpu_baseline cpu_now cpu_delta now grace active_cpu_sec all_ready=1

  dir="$(mc_sims_idle_dir)"

  # Prune stamps for pids no longer alive or no longer sim-classified, so a reused
  # pid number cannot inherit a stale idle clock and an exited sim does not leave a
  # stray file behind forever. Runs unconditionally, ahead of every veto below --
  # pure garbage collection, unrelated to whether a reap is currently permitted.
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

  # Idle/CPU bookkeeping runs BEFORE the veto checks below (when there is
  # anything to track at all), and no veto ever deletes it -- contrast the old
  # `rm -rf "$dir"` on every decline, which reset every sim's clock to zero on
  # every single pass under continuous agent use, so tier 3 could not fire
  # even in principle. A veto still blocks the kill a few lines down; it just
  # no longer erases the evidence that a sim has been idle while the veto was
  # in effect, so idle time keeps accumulating honestly regardless of which
  # gate currently blocks the actual reap. A no-op when SIMPIDS is empty --
  # the vetoes below are still checked and logged in that case, matching the
  # pre-existing behavior of logging why tier 3 declined even on a pass with
  # nothing currently tracked.
  if [ -n "${SIMPIDS// /}" ]; then
    mkdir -p "$dir"
    now=$(date +%s)
    grace="${SIM_IDLE_GRACE_SEC:-600}"
    active_cpu_sec="${SIM_ACTIVE_CPU_SEC:-2}"
    for pid in $SIMPIDS; do
      stamp="$(mc_sims_idle_stamp "$pid")"
      # Reset explicitly before the read, not just defaulted after: a `read
      # ... < "$stamp"` against a stamp that doesn't exist yet fails the
      # REDIRECTION itself (before `read` ever runs), which both prints a
      # "No such file or directory" straight past a trailing `2>/dev/null`
      # (confirmed empirically -- that redirects read's own stderr, not the
      # shell's redirection-setup failure) AND, worse, leaves $first and
      # $cpu_baseline holding whatever the PREVIOUS pid in this loop set them
      # to, since a command that never ran cannot have assigned anything.
      # Without this reset, a freshly-seen pid silently inherited an earlier
      # pid's clock instead of starting its own.
      first=""; cpu_baseline=""
      if [ -f "$stamp" ]; then
        # shellcheck disable=SC2162
        read -r first cpu_baseline < "$stamp"
      fi
      first="${first:-0}"
      cpu_now=$(mc_cputime_secs "$(ps -o time= -p "$pid" 2>/dev/null)") || cpu_now=""

      if [ "$first" = "0" ]; then
        # Never tracked before: start the clock now. Nothing to compare CPU
        # against yet on this very pass, so the starting baseline is simply
        # whatever it already has.
        first="$now"
        cpu_baseline="${cpu_now:-0}"
        printf '%s %s\n' "$first" "$cpu_baseline" > "$stamp"
      elif [ -z "$cpu_now" ]; then
        # Could not sample CPU this pass (pid raced between snapshot and
        # check, ps failed). A measurement gap is not evidence of activity,
        # but it is not evidence of idleness either -- hold the reap without
        # resetting the clock over it.
        all_ready=0
      else
        cpu_delta=$(( cpu_now - ${cpu_baseline:-0} ))
        if [ "$cpu_delta" -ge "$active_cpu_sec" ]; then
          # Real CPU work happened since the clock started -- this is what
          # "in use" actually looks like, unlike merely having a session
          # open. A booted-but-unused simulator burns approximately zero
          # CPU; this is the signal that replaces the blanket session veto.
          first="$now"
          cpu_baseline="$cpu_now"
          printf '%s %s\n' "$first" "$cpu_baseline" > "$stamp"
        fi
      fi
      [ $((now - first)) -lt "$grace" ] && all_ready=0
    done
  fi

  # Vetoes, checked after the bookkeeping above so idle/CPU evidence keeps
  # accumulating even while one is in effect. Throttled, not mc_log: each can
  # hold for as long as the tool's normal operating state does, and unthrottled
  # they drowned the actual kill records -- see mc_log_throttled in common.sh.
  # Each reason's key clears the moment its own condition stops holding, so a
  # state change still gets its own line rather than reusing a stale window.
  if [ "${TIER3_REQUIRE_NO_SESSION:-0}" = "1" ] && ! mc_no_live_session; then
    mc_log_throttled "tier3-agent-alive" "tier3: declining -- an agent session is alive (TIER3_REQUIRE_NO_SESSION=1)"
    return 0
  fi
  mc_log_throttle_clear "tier3-agent-alive"
  if mc_active_mobile_tooling; then
    mc_log_throttled "tier3-active-tooling" "tier3: declining -- active mobile tooling detected (maestro, xcodebuild, expo, react-native, or detox)"
    return 0
  fi
  mc_log_throttle_clear "tier3-active-tooling"
  if mc_hands_on_mobile; then
    mc_log_throttled "tier3-hands-on-mobile" "tier3: declining -- hands-on mobile work detected (Xcode, Android Studio, or Simulator.app open)"
    return 0
  fi
  mc_log_throttle_clear "tier3-hands-on-mobile"

  [ "$all_ready" = "0" ] && return 0

  if [ "$MC_DRY_RUN" != "1" ] && xcrun simctl list devices booted 2>/dev/null | grep -q Booted; then
    mc_log "tier3: xcrun simctl shutdown all"
    xcrun simctl shutdown all >/dev/null 2>&1
  fi
  for pid in $SIMPIDS; do
    ps -o command= -p "$pid" 2>/dev/null | grep -Eq "$MC_SIM_KILL_PATTERN" || continue
    targets="$targets $pid"
    # Audit detail beyond mc_kill_pids' own log line: which pid, why it was
    # judged idle, and how long it had been flat -- what makes a reclaim
    # reviewable after the fact rather than just a bare kill record.
    stamp="$(mc_sims_idle_stamp "$pid")"
    first=""; cpu_baseline=""
    if [ -f "$stamp" ]; then
      # shellcheck disable=SC2162
      read -r first cpu_baseline < "$stamp"
    fi
    [ -z "$first" ] && first="$now"
    mc_log "tier3: reclaiming pid $pid -- idle $((now - first))s, CPU flat at ~${cpu_baseline:-0}s accumulated"
  done
  [ -n "${targets// /}" ] && mc_kill_pids "$targets" "tier3 idle simulator"
  return 0
}

mc_watch() {
  if mc_is_paused; then echo "memcap is paused (memcap on to resume)"; mc_stamp_heartbeat; return 0; fi
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
    mc_stamp_heartbeat
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
  mc_stamp_heartbeat
  return 0
}
