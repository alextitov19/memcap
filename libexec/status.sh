#!/usr/bin/env bash
# Rendering `memcap status`.
#
# The audit's central finding was about this file: a permanently non-enforcing
# memcap was indistinguishable from a healthy one. Three separate states -- a
# budget so misconfigured that `watch` refuses on every pass, a config with a
# stray quote that made every knob a default, and a `top` failure that
# understated every total by 42% -- all rendered as a clean report with a fresh
# heartbeat. The heartbeat added to catch a stopped daemon was what certified
# them fine, because it answers "did a pass happen", which is a different
# question from "did that pass enforce anything" and from "is the LaunchAgent
# even loaded".
#
# So the rule this file now follows: `status` renders the OUTCOME, not the
# activity, and anything it cannot establish is said out loud rather than left
# blank. Sourced after common.sh, config.sh, budget.sh, detect.sh, measure.sh
# and classify.sh (bin/memcap does this); service.sh is optional and only
# sharpens the LaunchAgent row from "unknown" into a real answer.
set -uo pipefail

mc_gb() { awk -v k="$1" 'BEGIN{printf "%.2f", k/1024/1024}'; }

# One column layout for every row, so a line added later cannot quietly land a
# character out from the ones around it.
mc_status_row() { printf '  %-33s%s\n' "$1" "$2"; }

# Names the first NAME=VALUE pair whose VALUE is empty, or fails if all are set.
#
# The guard for the defect that made every other guard here optional: this file
# reads all of its values inside command substitutions, and `set -u` inside
# `$( )` kills only that subshell. An unbound AGENT_KB therefore stopped
# nothing -- it produced an empty string, and `status` printed a
# plausible-looking report with blank numbers and exited 0. That is `set -u`
# defeated in exactly the place where values are rendered rather than compared.
mc_status_blank() {
  local pair
  for pair in "$@"; do
    case "$pair" in
      *=) printf '%s' "${pair%=}"; return 0 ;;
    esac
  done
  return 1
}

# Is memcap's LaunchAgent actually loaded? `status` never asked: only `memcap
# service status` did, and nothing runs it. So a fresh heartbeat proved a pass
# had happened, not that the SERVICE had run one -- a manual `memcap watch`
# stamps the identical file.
#
# Four answers, not two, because the remedies differ: not installed and
# installed-but-unloaded both want `memcap service install`, while "launchctl
# could not be asked" must not be reported as either. Same reasoning as
# mc_pid_alive -- never turn "could not determine" into "no".
#
# Echoes: loaded | not-loaded | not-installed | unavailable
mc_launchagent_state() {
  local bin out
  command -v mc_launchagent_plist >/dev/null 2>&1 || { printf 'unavailable'; return 0; }
  [ -f "$(mc_launchagent_plist)" ] || { printf 'not-installed'; return 0; }
  bin="$(mc_launchctl_bin)"
  command -v "$bin" >/dev/null 2>&1 || { printf 'unavailable'; return 0; }
  out=$("$bin" list 2>/dev/null) || { printf 'unavailable'; return 0; }
  case "$out" in
    *"$(mc_launchagent_label)"*) printf 'loaded' ;;
    *) printf 'not-loaded' ;;
  esac
}

# Appends a loud line to the block mc_render_status prints last. Assigns to its
# CALLER's local (bash is dynamically scoped), which is why mc_render_status
# declares MC_STATUS_WARNINGS local: warnings from one render cannot leak into
# the next, and nothing global is left behind.
mc_status_warn() {
  MC_STATUS_WARNINGS="${MC_STATUS_WARNINGS}  $1
"
}

# C5. The heartbeat says a pass happened; this says what that pass DID.
# Everything other than `enforced` earns a loud remedy line, because each of
# these states used to render as a clean report: `watch` printed its refusal to
# stdout -- which nothing reads under launchd -- stamped the heartbeat, and
# returned without enforcing. Called from mc_render_status so its warnings land
# in that function's block.
mc_render_outcome() {
  local outcome outcome_label conf
  conf="$(mc_config_file)"
  outcome=$(head -1 "$(mc_state_dir)/last-outcome" 2>/dev/null | tr -d '[:space:]') || outcome=""
  case "$outcome" in
    enforced)
      outcome_label="enforced"
      ;;
    paused)
      # The ENFORCEMENT PAUSED line below already shouts, and says how long for.
      outcome_label="paused -- no tier acted"
      ;;
    dry-run)
      outcome_label="DRY RUN -- nothing was killed"
      mc_status_warn "MEMCAP IS IN DRY-RUN MODE -- it reports what it would kill and kills nothing (MC_DRY_RUN=1 in the service's environment)."
      ;;
    refused-misconfig)
      # Only when `status` has not already reached the same conclusion from the
      # config in front of it. Two lines describing one broken setting is how a
      # report teaches people to skim past the loud lines.
      outcome_label="REFUSED -- budget is unsatisfiable"
      [ "${MC_STATUS_SAW_MISCONFIG:-0}" = "1" ] ||
        mc_status_warn "THE LAST PASS DID NOT ENFORCE -- DOCKER_BUDGET_GB left no room in TOTAL_BUDGET_GB, so every pass refused and returned. Fix $conf"
      ;;
    refused-badconfig)
      outcome_label="REFUSED -- config does not parse"
      [ "${MC_STATUS_SAW_BADCONFIG:-0}" = "1" ] ||
        mc_status_warn "THE LAST PASS DID NOT ENFORCE -- $conf did not parse. Check it with: bash -n $conf"
      ;;
    degraded-measurement)
      outcome_label="REFUSED -- measurement was unreliable"
      mc_status_warn "THE LAST PASS DID NOT ENFORCE -- the memory measurement fell back to ps RSS, which understates the real totals by ~42%. memcap will not kill anything on numbers it does not trust."
      ;;
    '')
      # Deliberately not read as `enforced`: an absent record looks exactly like
      # every silent failure in this audit, and guessing in the tool's own
      # favour is what produced them. It is only worth SHOUTING about once a
      # pass has actually happened, though -- on a fresh install the heartbeat
      # row above is already saying the same thing in plainer words.
      outcome_label="not recorded"
      [ "${MC_STATUS_HEARTBEAT_SEEN:-0}" = "1" ] &&
        mc_status_warn "THE LAST PASS RECORDED NO OUTCOME -- memcap cannot confirm it enforced anything. Upgrade memcap if this persists, then: memcap service install"
      ;;
    *)
      outcome_label="unrecognised ($outcome)"
      mc_status_warn "THE LAST PASS RECORDED AN OUTCOME THIS VERSION DOES NOT KNOW ($outcome) -- treat it as unenforced until it is explained."
      ;;
  esac
  mc_status_row "last pass outcome" "$outcome_label"
}

# The LaunchAgent row, for the same reason: without it, an uninstalled or
# unloaded agent was invisible to the one command a person runs to check on
# memcap, however fresh the heartbeat above it looked.
mc_render_launchagent() {
  case "$(mc_launchagent_state)" in
    loaded)        mc_status_row "background service" "LaunchAgent loaded" ;;
    not-loaded)
      mc_status_row "background service" "LaunchAgent NOT LOADED"
      mc_status_warn "MEMCAP'S LAUNCHAGENT IS NOT LOADED -- the plist exists but launchd is not running it, so nothing schedules a pass: memcap service install"
      ;;
    not-installed)
      mc_status_row "background service" "no LaunchAgent installed"
      mc_status_warn "MEMCAP'S LAUNCHAGENT IS NOT INSTALLED -- nothing is scheduled to run, whatever the heartbeat above says: memcap service install"
      ;;
    *)
      mc_status_row "background service" "unknown (launchctl could not be asked)"
      ;;
  esac
}

# The heartbeat row, judged in AWAKE seconds wherever the stamp allows it.
# Wall-clock age after a lid-close reads "8h" for a service that is perfectly
# healthy and self-heals within ~60s of wake; shouting MEMCAP IS PROBABLY NOT
# RUNNING every morning is how the one real occurrence gets ignored.
mc_render_heartbeat() {
  local now stale_sec heartbeat_ts age age_stale age_basis note awake_now awake_stamp wake
  now=$(date +%s)
  stale_sec=$(mc_num "${STALE_PASS_SEC:-300}" 300 STALE_PASS_SEC)
  heartbeat_ts=$(cat "$(mc_state_dir)/last-pass" 2>/dev/null) || heartbeat_ts=""

  case "$heartbeat_ts" in
    ''|*[!0-9]*)
      mc_status_row "last enforcement pass" "NEVER"
      mc_status_warn "MEMCAP HAS NOT RUN SINCE INSTALL -- memcap service install"
      return 0
      ;;
  esac
  # Read by mc_render_outcome, whose "no outcome recorded" warning is only
  # meaningful once a pass has actually happened. Its scope is mc_render_status's
  # local, same as MC_STATUS_WARNINGS.
  MC_STATUS_HEARTBEAT_SEEN=1

  age=$((now - heartbeat_ts))
  # A clock moved backward (NTP correction, a manual adjustment) would print a
  # nonsensical negative duration; treat it as fresh rather than alarm over
  # something that is not evidence of anything.
  [ "$age" -lt 0 ] && age=0
  age_stale="$age"
  age_basis=wall
  note=""

  awake_stamp=$(cat "$(mc_state_dir)/last-pass-awake" 2>/dev/null) || awake_stamp=""
  case "$awake_stamp" in
    ''|*[!0-9]*) awake_stamp="" ;;
  esac
  if [ -n "$awake_stamp" ] && awake_now=$(mc_awake_secs); then
    # A stamp ABOVE the current reading is from a previous boot -- the awake
    # clock restarts at zero -- so it says nothing about this one. Fall back to
    # wall time rather than compute a negative age.
    if [ "$awake_now" -ge "$awake_stamp" ]; then
      age_stale=$((awake_now - awake_stamp))
      age_basis=awake
      # Only worth mentioning when the two clocks actually disagree.
      [ $((age - age_stale)) -ge 60 ] &&
        note=" ($(mc_format_age "$age_stale") of it awake -- the machine slept for the rest)"
    fi
  fi

  # No awake stamp (a heartbeat written by an older memcap): fall back to the
  # coarse signal. A pass that predates the last wake, when that wake was
  # recent, is a pass that has not had its chance to run yet.
  if [ "$age_stale" -gt "$stale_sec" ] && [ "$age_basis" = wall ] && wake=$(mc_last_wake); then
    if [ "$heartbeat_ts" -le "$wake" ] && [ $((now - wake)) -le "$stale_sec" ]; then
      age_stale=$((now - wake))
      note=" (the machine woke $(mc_format_age $((now - wake))) ago -- the next pass is due within 60s)"
    fi
  fi

  mc_status_row "last enforcement pass" "$(mc_format_age "$age") ago${note}"
  [ "$age_stale" -gt "$stale_sec" ] &&
    mc_status_warn "MEMCAP IS PROBABLY NOT RUNNING -- memcap service install"
  return 0
}

mc_render_status() {
  local total default_cap cap default_docker docker_budget agents_budget
  local agent_gb agent_net_gb docker_gb combined free free_marker free_label
  local docker_ceiling_label agents_budget_label missing measure_label
  local paused_since now conf
  # Locals rather than globals, and read by the mc_render_* helpers below
  # through bash's dynamic scoping: nothing survives one render into the next.
  local MC_STATUS_WARNINGS="" MC_STATUS_HEARTBEAT_SEEN=0
  local MC_STATUS_SAW_MISCONFIG=0 MC_STATUS_SAW_BADCONFIG=0

  conf="$(mc_config_file)"

  # A config that does not parse is not a cosmetic problem: keys before the
  # error applied and keys after it did not, which on the author's machine left
  # an 87 GB agent budget on a 24 GB machine -- no tier could fire again,
  # `watch` exited 0, and `status` was green. mc_load_config now refuses to
  # apply such a file at all; this is where a person finds that out.
  if [ "${MC_CONFIG_BROKEN:-0}" = "1" ]; then
    MC_STATUS_SAW_BADCONFIG=1
    mc_status_warn "MEMCAP IS NOT ENFORCING -- $conf does not parse, so none of it is applied. Check it with: bash -n $conf"
  fi

  total=$(mc_total_ram_gb) || total=""
  case "$total" in
    ''|*[!0-9]*) total=0 ;;
  esac
  # Fail closed rather than render a budget derived from a machine size of zero:
  # that computes a 0 GB cap and then reports the machine as infinitely over it.
  if [ "$total" -le 0 ]; then
    echo "memcap: cannot read this machine's total RAM (sysctl -n hw.memsize) -- refusing to print a budget computed from nothing." >&2
    return 4
  fi

  # Through mc_num (contract C1) rather than `${VAR:-N}`: both reach `-lt` and
  # arithmetic below, where a knob written as "16 " is a vanished guard and one
  # written as "016" is a silently octal budget.
  default_cap=$(mc_cap_gb "$total")
  cap=$(mc_num "${TOTAL_BUDGET_GB:-$default_cap}" "$default_cap" TOTAL_BUDGET_GB)
  default_docker=$(mc_docker_gb "$cap")
  docker_budget=$(mc_num "${DOCKER_BUDGET_GB:-$default_docker}" "$default_docker" DOCKER_BUDGET_GB)
  agents_budget=$((cap - docker_budget))

  eval "$(mc_ps_snapshot | mc_classify)"
  # C4 (amended): the MC_MEASURE_* globals cannot survive the pipeline inside
  # the command substitution above, so they are loaded back here, in this shell.
  mc_measure_status_load
  measure_label=$(mc_measure_summary)

  if missing=$(mc_status_blank "AGENT_KB=${AGENT_KB-}" "DOCKER_KB=${DOCKER_KB-}" \
                               "SIM_KB=${SIM_KB-}" "ORPHAN_KB=${ORPHAN_KB-}"); then
    echo "memcap: the process classifier produced no $missing -- refusing to print a report with blank numbers in it." >&2
    return 4
  fi

  agent_gb=$(mc_gb "$AGENT_KB"); docker_gb=$(mc_gb "$DOCKER_KB")
  agent_net_gb=$(mc_gb "$(mc_agent_net_kb "$AGENT_KB" "$SIM_KB")")
  combined=$(awk -v a="$agent_gb" -v d="$docker_gb" 'BEGIN{printf "%.2f", a+d}')

  # mc_free_pct clears or writes its own marker as a side effect of this call,
  # so the marker is read AFTER it: what gets reported is this run's own
  # measurement, not a leftover from an earlier one.
  free=$(mc_free_pct)
  free_marker=$(mc_measure_free_marker)
  if [ -n "$free_marker" ] && [ -f "$free_marker" ]; then
    # The old mc_free_pct returned a hardcoded 100 here, which made tier 1's
    # low-memory trigger permanently unreachable. It returns 0 now and says so
    # -- but only if something surfaces the marker, which is this.
    free_label="UNMEASURABLE (reported as 0% so tier 1 fails closed)"
    mc_status_warn "SYSTEM MEMORY CANNOT BE MEASURED -- sysctl/vm_stat gave no usable numbers, so the low-memory trigger is running blind (it assumes 0% free now rather than a confident 100%). Check that /usr/sbin is on the daemon's PATH."
  else
    free_label="${free}%"
  fi

  if [ "${MC_MEASURE_FAULT:-0}" = "1" ]; then
    # FAULT, not DEGRADED: a deliberate MC_NO_TOP=1 sets DEGRADED without FAULT,
    # and a warning that fires on the configuration someone chose is one they
    # learn to ignore before the real fault ever arrives.
    mc_status_warn "MEASUREMENT IS FAULTY -- $measure_label. Every total above is understated (42% low in the measured case), so none of them are comparable to the budget."
  fi

  # A 0 ceiling means Docker is unmanaged, not that it has a zero-GB allowance;
  # rendering it as "6.41 GB / 0 GB ceiling" reads as catastrophically over
  # budget. It does not mean EXCLUDED either -- the same Docker GB is still
  # summed into `combined` two rows down -- so the label says which it is.
  if [ "$docker_budget" -le 0 ]; then docker_ceiling_label="no ceiling (unmanaged, but still counted in combined below)"
  else docker_ceiling_label="${docker_budget} GB ceiling"; fi

  # The same guard on the line directly ABOVE the Docker one, where it was
  # missing: DOCKER_BUDGET_GB >= TOTAL_BUDGET_GB rendered "6.19 GB / 0 GB
  # budget", the exact nonsense the Docker line is careful about. `watch`
  # refuses to act at all in this state, so `status` says so rather than
  # printing an unsatisfiable budget as though it were a live one.
  if [ "$agents_budget" -lt 1 ]; then
    agents_budget_label="NO BUDGET LEFT -- see below"
    MC_STATUS_SAW_MISCONFIG=1
    mc_status_warn "MEMCAP IS NOT ENFORCING -- DOCKER_BUDGET_GB ($docker_budget) leaves nothing of TOTAL_BUDGET_GB ($cap) for agents, so every pass refuses and returns. Fix $conf"
  else
    agents_budget_label="${agents_budget} GB budget"
  fi

  printf 'memcap — %s\n\n' "$conf"
  mc_status_row "agents + everything they spawn" "${agent_gb} GB / ${agents_budget_label}"
  mc_status_row "  of which leaked/orphaned" "$(mc_gb "$ORPHAN_KB") GB"
  mc_status_row "  of which sims/playwright" "$(mc_gb "$SIM_KB") GB"
  mc_status_row "  net of sims (drives tier 2)" "${agent_net_gb} GB"
  mc_status_row "docker VM + helpers" "${docker_gb} GB / ${docker_ceiling_label}"
  echo "  ---------------------------------------------------------"
  mc_status_row "combined" "${combined} GB / ${cap} GB budget"
  mc_status_row "system memory available" "$free_label"
  mc_status_row "memory measured by" "$measure_label"
  mc_render_heartbeat
  mc_render_outcome
  mc_render_launchagent

  # `memcap off` used to write nothing anywhere, so a paused week and a dead
  # week were indistinguishable after the fact. mc_pause dates the marker now,
  # and this reports how long it has held -- a pause someone forgot about is
  # exactly as unenforced as a crash.
  if mc_is_paused; then
    if paused_since=$(mc_paused_since); then
      now=$(date +%s)
      if [ "$now" -ge "$paused_since" ]; then
        mc_status_warn "ENFORCEMENT PAUSED for $(mc_format_age $((now - paused_since))) (memcap on to resume)"
      else
        mc_status_warn "ENFORCEMENT PAUSED (memcap on to resume)"
      fi
    else
      mc_status_warn "ENFORCEMENT PAUSED (memcap on to resume)"
    fi
  fi

  [ -n "$MC_STATUS_WARNINGS" ] && printf '%s' "$MC_STATUS_WARNINGS"
  return 0
}
