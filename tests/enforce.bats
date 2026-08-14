load helper
setup() {
  setup_common
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/roots.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/enforce.sh"
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
  output=$(mc_kill_pids "$victim" "test")
  kill -0 "$victim"
  kill "$victim" 2>/dev/null
  [ -z "$output" ]
}

@test "mc_kill_pids filters out memcap's own pid" {
  MC_DRY_RUN=1
  output=$(mc_kill_pids "$$" "test")
  [ -z "$output" ]
}

@test "mc_kill_pids filters out its own parent, not just itself" {
  parent=$(ps -o ppid= -p $$ | tr -d ' ')
  MC_DRY_RUN=1
  output=$(mc_kill_pids "$parent" "test")
  [ -z "$output" ]
}

@test "mc_kill_pids does not filter out an ordinary, unrelated pid" {
  sleep 600 & victim=$!
  AGENTPIDS=""
  MC_DRY_RUN=1
  output=$(mc_kill_pids "$victim" "test")
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
