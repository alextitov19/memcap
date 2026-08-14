load helper
setup() {
  setup_common
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/roots.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/enforce.sh"
  # Belt: no test in this file may ever send a real kill, whatever else goes wrong.
  # `mc_watch` records real sweep roots from live agent cwds and, if this machine is
  # over budget, runs tier 1/2/3 for real against real pids -- exactly the scenario
  # this belt exists to prevent, mirroring docker.bats's MC_DRY_RUN=1 for the same
  # reason. Individual tests below still set MC_DRY_RUN=1 explicitly before calling
  # enforcement functions directly; this covers the ones that shell out to
  # `bin/memcap watch`/`clean` and would otherwise inherit no override at all.
  export MC_DRY_RUN=1
}

@test "dry run reports without killing" {
  sleep 600 & victim=$!
  run env MC_DRY_RUN=1 "$MEMCAP_ROOT/bin/memcap" clean
  kill -0 $victim
  kill $victim 2>/dev/null || true
}

@test "paused state blocks all enforcement" {
  "$MEMCAP_ROOT/bin/memcap" off
  run "$MEMCAP_ROOT/bin/memcap" watch
  [[ "$output" == *paused* ]]
  "$MEMCAP_ROOT/bin/memcap" on
}

@test "watch exits zero when there is nothing to do" {
  run "$MEMCAP_ROOT/bin/memcap" watch
  [ "$status" -eq 0 ]
}

@test "init writes a config with a cap matching this machine" {
  run bash -c "yes '' | '$MEMCAP_ROOT/bin/memcap' init --no-service --no-docker"
  [ "$status" -eq 0 ]
  run cat "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  [[ "$output" == *TOTAL_BUDGET_GB=* ]]
}

# --- Carried finding 1: TOCTOU on sweep roots ---------------------------------
# A root recorded while safe can be replaced by a symlink before tier 1 acts on it.
# mc_reap_orphans must re-validate with mc_root_is_safe immediately before using a
# root to match a kill candidate, not trust the state file blindly.
@test "TOCTOU: a root that turned unsafe since being recorded is not used to match orphans" {
  mkdir -p "$HOME/.mc-toctou-$$/proj"
  mc_record_root "$HOME/.mc-toctou-$$/proj"
  rm -rf "$HOME/.mc-toctou-$$/proj"
  ln -sfn /etc "$HOME/.mc-toctou-$$/proj"

  # Victim's command line literally contains the now-unsafe root string, so the
  # OLD (pre-fix) code would textually match it via grep -F and mark it a kill
  # target. perl keeps the argument visible in `ps -o command=`.
  perl -e 'sleep 600' "$HOME/.mc-toctou-$$/proj" &
  victim=$!
  sleep 0.2

  # shellcheck disable=SC2034  # consumed by mc_reap_orphans, sourced from enforce.sh
  ORPHANS="$victim"
  MC_DRY_RUN=1
  run mc_reap_orphans

  kill "$victim" 2>/dev/null
  rm -rf "$HOME/.mc-toctou-$$"

  [[ "$output" != *"would kill"* ]]
}

# --- Carried finding 2: DEVPIDS has zero test coverage ------------------------
# DEVPIDS is tier 2's input. Both branches need a real, non-empty list: candidates
# that are too young to touch, and a candidate old enough to be selected.
@test "tier2: DEVPIDS candidates younger than TIER2_MIN_AGE_SEC are left alone" {
  sleep 600 & victim=$!
  sleep 0.2
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  MC_DRY_RUN=1
  run mc_kill_over_budget

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  [[ "$output" == *"not touching active work"* ]]
  [[ "$output" != *"would kill"* ]]
}

@test "tier2: an old-enough DEVPIDS candidate is selected as the kill target" {
  sleep 600 & victim=$!
  sleep 0.2
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  run mc_kill_over_budget

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  [[ "$output" == *"would kill"* ]]
  [[ "$output" == *"$victim"* ]]
}

# --- Carried finding 5: a negative agent budget must not be actionable --------
# DOCKER_BUDGET_GB >= TOTAL_BUDGET_GB (e.g. from a hand-edited config) makes
# agents_budget negative or zero. init must refuse to write such a config, and
# watch must refuse to act on one if it exists anyway.
@test "init refuses a hand-typed Docker ceiling that would leave agents no budget" {
  run bash -c "printf '10\n20\nno\n' | '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -eq 0 ]
  run cat "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  [[ "$output" == *"TOTAL_BUDGET_GB=10"* ]]
  [[ "$output" != *"DOCKER_BUDGET_GB=20"* ]]
  [[ "$output" == *"DOCKER_BUDGET_GB=4"* ]]
}

# --- mc_etime_secs: macOS `ps` has no `etimes`, only formatted `etime` -------
@test "mc_etime_secs parses mm:ss" {
  run mc_etime_secs "01:30"
  [ "$output" = "90" ]
}

@test "mc_etime_secs parses hh:mm:ss" {
  run mc_etime_secs "01:02:03"
  [ "$output" = "3723" ]
}

@test "mc_etime_secs parses dd-hh:mm:ss" {
  run mc_etime_secs "2-01:02:03"
  [ "$output" = "176523" ]
}

@test "mc_etime_secs rejects non-numeric input instead of miscomparing" {
  run mc_etime_secs "keyword not found"
  [ "$status" -ne 0 ]
}

# --- Final review, C1: tier 2 must trigger on agents NET of simulators -------
# classify.sh folds sim footprint into AGENT_KB too, but only tier 3 can reclaim it,
# and tier 3 declines outright whenever an agent session is alive -- which is the
# tool's entire premise. Left unguarded, tier 2 fires on an overage it structurally
# cannot fix and kills a dev server every pass without converging. mc_watch must
# trigger tier 2 (and tier 1's soft trigger) on AGENT_KB - SIM_KB, not the gross
# figure. Driven through the real `bin/memcap watch` wiring -- common/budget/
# detect/measure/classify/roots/status/enforce sourced in the same order bin/memcap
# uses -- with mc_ps_snapshot stubbed for a deterministic fixture and
# mc_kill_over_budget stubbed to record whether tier 2 was reached, since the actual
# kill path is exercised elsewhere. MC_DRY_RUN=1 throughout per this machine's
# safety rules, though nothing here reaches a real kill or a real `xcrun simctl` call.
@test "C1: tier2 does not fire when the overage is entirely sim-attributable" {
  run env TOTAL_BUDGET_GB=10 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    mc_ps_snapshot() { printf '9001 1 3000000 /usr/local/bin/claude\n9002 1 9000000 /path/ms-playwright/chromium/chrome\n'; }
    mc_kill_over_budget() { echo TIER2_FIRED; }
    mc_record_roots() { :; }
    mc_watch
  "
  [[ "$output" != *TIER2_FIRED* ]]
}

@test "C1: tier2 still fires when agents are genuinely over budget on their own" {
  run env TOTAL_BUDGET_GB=5 DOCKER_BUDGET_GB=0 MC_DRY_RUN=1 bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/measure.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    source '$MEMCAP_ROOT/libexec/roots.sh'
    source '$MEMCAP_ROOT/libexec/status.sh'
    source '$MEMCAP_ROOT/libexec/enforce.sh'
    mc_ps_snapshot() { printf '9002 1 9000000 /usr/local/bin/claude\n'; }
    mc_kill_over_budget() { echo TIER2_FIRED; }
    mc_record_roots() { :; }
    mc_watch
  "
  [[ "$output" == *TIER2_FIRED* ]]
}

@test "watch refuses to act when DOCKER_BUDGET_GB leaves no room for agents" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  cat > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" <<-'EOF'
	TOTAL_BUDGET_GB=8
	DOCKER_BUDGET_GB=10
	EOF
  run "$MEMCAP_ROOT/bin/memcap" watch
  [ "$status" -ne 0 ]
  [[ "$output" == *misconfigured* ]]
}

# --- Review round 1, Finding 1 (CRITICAL): never kill an agent CLI via a subtree
# walk. mc_kill_pids is the single choke point every tier's kill list passes
# through, so the protection filter is tested there directly, plus once more at
# the tier-2 level to prove a real subtree gets it right end to end.
@test "mc_kill_pids filters out a pid present in AGENTPIDS" {
  sleep 600 & victim=$!
  # shellcheck disable=SC2034  # consumed by mc_filter_protected
  AGENTPIDS="$victim"
  MC_DRY_RUN=1
  # run, not a bare `output=$(...)`: mc_kill_pids now returns non-zero (I2) whenever
  # it kills nothing, including this filtered-empty case, and a bare assignment's
  # exit status trips bats' own error trap before the cleanup `kill` below can run.
  run mc_kill_pids "$victim" "test"
  kill -0 "$victim"
  kill "$victim" 2>/dev/null
  [ -z "$output" ]
}

@test "mc_kill_pids filters out memcap's own pid" {
  MC_DRY_RUN=1
  run mc_kill_pids "$$" "test"
  [ -z "$output" ]
}

@test "mc_kill_pids filters out its own parent, not just itself" {
  parent=$(ps -o ppid= -p $$ | tr -d ' ')
  MC_DRY_RUN=1
  run mc_kill_pids "$parent" "test"
  [ -z "$output" ]
}

@test "mc_kill_pids does not filter out an ordinary, unrelated pid" {
  sleep 600 & victim=$!
  AGENTPIDS=""
  MC_DRY_RUN=1
  # Dry run is also a non-kill outcome (I2), so this now returns non-zero too --
  # `run` again, for the same reason as above.
  run mc_kill_pids "$victim" "test"
  kill -0 "$victim"
  kill "$victim" 2>/dev/null
  [[ "$output" == *"would kill"* ]]
  [[ "$output" == *"$victim"* ]]
}

@test "tier2: a subtree containing an agent pid kills the server but spares the agent" {
  bash -c 'sleep 600 & wait' >/dev/null 2>&1 & server=$!
  sleep 0.3
  agent_child=$(pgrep -P "$server" | head -1)
  [ -n "$agent_child" ]

  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$server"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # AGENTPIDS marks the child as an agent CLI living beneath the dev server --
  # an ordinary shape in agentic workflows (e.g. `npm run dev` shelling out to one).
  # shellcheck disable=SC2034  # consumed by mc_filter_protected
  AGENTPIDS="$agent_child"
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  run mc_kill_over_budget

  kill "$agent_child" 2>/dev/null
  kill "$server" 2>/dev/null

  [[ "$output" == *"would kill"* ]]
  [[ "$output" == *"$server"* ]]
  [[ "$output" != *"$agent_child"* ]]
}

# --- Review round 1, Finding 3: `clean` must honour the pause file too. `memcap
# off` is a kill switch -- it should stop manual sweeps, not just the watch loop.
@test "clean refuses while paused" {
  "$MEMCAP_ROOT/bin/memcap" off
  run "$MEMCAP_ROOT/bin/memcap" clean
  [[ "$output" == *paused* ]]
  "$MEMCAP_ROOT/bin/memcap" on
}

# --- Follow-up: tier 3 must honour SIM_IDLE_GRACE_SEC ------------------------
# SIM_IDLE_GRACE_SEC is written by init but was read by no code, so a hand-booted
# simulator (Simulator.app or `simctl` run directly, no Xcode, no agent session) was
# reaped on the very first poll. mc_reap_sims must stamp the first idle sighting and
# wait out the grace before reaping, and clear the stamp whenever a session reappears.
@test "tier3: first idle poll stamps the grace period and kills nothing" {
  sleep 600 & victim=$!
  AGENTPIDS=""
  SIMPIDS="$victim"
  MC_DRY_RUN=1
  run mc_reap_sims

  kill -0 "$victim"
  kill "$victim" 2>/dev/null

  [ -z "$output" ]
  [ -f "$(mc_sims_idle_stamp "$victim")" ]
}

@test "tier3: once the idle stamp is past SIM_IDLE_GRACE_SEC the reap proceeds" {
  # A real sim-pattern match is needed once the reap actually proceeds -- a plain
  # sleep would never be selected by mc_reap_sims's own command-line filter. perl
  # keeps the marker argument visible in `ps -o command=`.
  perl -e 'sleep 600' "ms-playwright-fixture" &
  victim=$!
  sleep 0.2
  AGENTPIDS=""
  SIMPIDS="$victim"
  MC_DRY_RUN=1

  run mc_reap_sims
  [ -z "$output" ]
  stamp="$(mc_sims_idle_stamp "$victim")"
  [ -f "$stamp" ]

  # Grace set to 0 rather than sleeping for real: time has moved forward at least
  # zero seconds since the first poll, so the gate opens on this second poll.
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIM_IDLE_GRACE_SEC=0
  run mc_reap_sims

  kill "$victim" 2>/dev/null

  [[ "$output" == *"would kill"* ]]
  [[ "$output" == *"$victim"* ]]
  [ ! -f "$stamp" ]
}

# --- Final review, I6: the idle grace stamp must be per-pid, not machine-wide -
# One shared `sims-idle` stamp meant a hand-booted simulator inherited whichever
# timestamp an unrelated, already-idle sim process had accumulated, and could be
# reaped with none of its own grace. Each tracked sim pid now gets its own stamp,
# and a pid seen idle for the first time this pass blocks the WHOLE reap -- not just
# itself -- until it too clears the grace, rather than being swept in in with an
# older pid's head start.
@test "I6: a freshly-tracked sim pid blocks the reap even though another tracked sim already cleared its own grace" {
  perl -e 'sleep 600' "ms-playwright-fixture" & old=$!
  sleep 600 & fresh=$!
  sleep 0.2
  AGENTPIDS=""
  SIMPIDS="$old $fresh"
  MC_DRY_RUN=1
  # $old gets a stamp far enough in the past to have cleared any real-world grace on
  # its own -- mirroring a sim that has genuinely been idle a while. $fresh gets none:
  # it is tracked for the first time on this very pass, exactly like a simulator just
  # booted by hand. Under the old shared-stamp design this single old timestamp would
  # have applied to $fresh too, and $old (a real kill-pattern match) would have been
  # reaped immediately -- taking $fresh's grace away from it in the process.
  mkdir -p "$(mc_sims_idle_dir)"
  echo 1 > "$(mc_sims_idle_stamp "$old")"

  run mc_reap_sims

  kill "$old" "$fresh" 2>/dev/null

  [[ "$output" != *"would kill"* ]]
  [ -f "$(mc_sims_idle_stamp "$fresh")" ]
}

@test "I6: once every tracked sim pid has individually cleared the grace, the reap proceeds" {
  perl -e 'sleep 600' "ms-playwright-fixture" & old=$!
  sleep 600 & fresh=$!
  sleep 0.2
  AGENTPIDS=""
  SIMPIDS="$old $fresh"
  MC_DRY_RUN=1
  mkdir -p "$(mc_sims_idle_dir)"
  echo 1 > "$(mc_sims_idle_stamp "$old")"
  run mc_reap_sims
  [[ "$output" != *"would kill"* ]]

  # Grace set to 0 rather than sleeping for real, same trick as the single-pid test
  # above: $fresh's own stamp (written on the poll just above) now also counts as
  # cleared, so both pids are individually ready and the pass proceeds.
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIM_IDLE_GRACE_SEC=0
  run mc_reap_sims

  kill "$old" "$fresh" 2>/dev/null

  [[ "$output" == *"would kill"* ]]
  [[ "$output" == *"$old"* ]]
}

@test "I6: mc_hands_on_mobile also treats Simulator.app itself as hands-on" {
  perl -e 'sleep 600' "/Applications/Xcode.app/Contents/Developer/Applications/Simulator.app/Contents/MacOS/Simulator" &
  victim=$!
  sleep 0.2
  run mc_hands_on_mobile
  kill "$victim" 2>/dev/null
  [ "$status" -eq 0 ]
}

# --- Final review, I2: dry run must not notify that a kill happened ---------
# mc_notify was called unconditionally after mc_kill_pids, so MC_DRY_RUN=1 -- and a
# real pass where mc_filter_protected removed every candidate -- told the user a dev
# server was killed when nothing was, contradicting the dry-run guarantee and burning
# the 5-minute notification rate limit. osascript is stubbed so this cannot fire a
# real desktop notification, matching scaffold.bats's stub_osascript pattern.
@test "I2: a dry-run tier2 pass does not send the 'killed a leaked dev server' notification" {
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  capture="$BATS_TEST_TMPDIR/osascript-arg"
  cat > "$fakebin/osascript" <<SCRIPT
#!/usr/bin/env bash
printf '%s' "\$2" >> "$capture"
SCRIPT
  chmod +x "$fakebin/osascript"

  sleep 600 & victim=$!
  sleep 0.2
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  PATH="$fakebin:$PATH" run mc_kill_over_budget

  kill "$victim" 2>/dev/null

  [[ "$output" == *"would kill"* ]]
  [ ! -f "$capture" ]
}

@test "I2: a tier2 pass where protection removed every candidate does not notify either" {
  fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  capture="$BATS_TEST_TMPDIR/osascript-arg"
  cat > "$fakebin/osascript" <<SCRIPT
#!/usr/bin/env bash
printf '%s' "\$2" >> "$capture"
SCRIPT
  chmod +x "$fakebin/osascript"

  sleep 600 & victim=$!
  sleep 0.2
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget, sourced from enforce.sh
  DEVPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_over_budget's age gate
  TIER2_MIN_AGE_SEC=0
  # The candidate is itself in AGENTPIDS, so mc_filter_protected removes it before
  # mc_kill_pids ever reaches the MC_DRY_RUN check -- this must not claim a kill
  # happened regardless of dry-run status. MC_DRY_RUN=1 kept per this machine's
  # safety rules; it is not what this test is exercising.
  # shellcheck disable=SC2034  # consumed by mc_filter_protected
  AGENTPIDS="$victim"
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  PATH="$fakebin:$PATH" run mc_kill_over_budget

  kill "$victim" 2>/dev/null

  [ ! -f "$capture" ]
}

@test "tier3: a live agent session clears every existing idle stamp" {
  dir="$(mc_sims_idle_dir)"
  mkdir -p "$dir"
  echo "1" > "$(mc_sims_idle_stamp 99999)"

  sleep 600 & agent=$!
  # shellcheck disable=SC2034  # consumed by mc_no_live_session, sourced from enforce.sh
  AGENTPIDS="$agent"
  # shellcheck disable=SC2034  # consumed by mc_reap_sims, sourced from enforce.sh
  SIMPIDS=""
  # shellcheck disable=SC2034  # consumed by mc_kill_pids, sourced from enforce.sh
  MC_DRY_RUN=1
  run mc_reap_sims

  kill "$agent" 2>/dev/null

  [ ! -d "$dir" ]
}
