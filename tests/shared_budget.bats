# Globals/stubs are consumed by production shell functions.
# shellcheck disable=SC2034,SC2329
load helper
setup() {
  setup_common
  export MC_DRY_RUN=1
  unset BUDGET_MODE
  for module in common config budget detect measure classify roots status enforce; do
    # shellcheck source=/dev/null
    source "$MEMCAP_ROOT/libexec/$module.sh"
  done
  TOTAL_BUDGET_GB=20
  DOCKER_BUDGET_GB=18
  FIX_AGENT=8388608 FIX_DOCKER=4194304 FIX_SIM=0
  mc_snapshot_capture() { MC_CAPTURE_SNAPSHOT='fixture'; MC_MEASURE_FAULT=0; }
  mc_ps_snapshot() { echo fixture; }
  mc_classify() { echo "AGENT_KB=$FIX_AGENT DOCKER_KB=$FIX_DOCKER SIM_KB=$FIX_SIM ORPHAN_KB=0 AGENTPIDS='' PROTECTEDPIDS='' SIMPIDS='' ORPHANS='' DEVPIDS=''"; }
  mc_record_roots() { :; }
  mc_reap_orphans() { :; }
  mc_reap_sims() { :; }
  mc_kill_over_budget() { echo TIER2_FIRED; }
  mc_free_pct() { echo 30; }
}

@test "SHARED: an 18 GB Docker ceiling does not leave idle agents a fixed 2 GB slice" {
  run mc_watch
  [ "$status" = 0 ]
  assert_not_contains "$output" TIER2_FIRED
}

@test "SHARED: measured Docker plus net agents crossing 20 GB reaches guarded cleanup" {
  FIX_DOCKER=13631488
  run mc_watch
  [ "$status" = 0 ]
  assert_contains "$output" TIER2_FIRED
}

@test "SHARED: simulator-only excess does not trigger unrelated dev-server cleanup" {
  FIX_DOCKER=13631488 FIX_AGENT=8388608 FIX_SIM=6291456
  run mc_watch
  [ "$status" = 0 ]
  assert_not_contains "$output" TIER2_FIRED
  assert_contains "$(cat "$MEMCAP_STATE_HOME/memcap/actions.log")" 'shared'
}

@test "SHARED: Docker alone exceeding total does not blame agent cleanup" {
  FIX_DOCKER=22020096
  run mc_watch
  [ "$status" = 0 ]
  assert_not_contains "$output" TIER2_FIRED
}

@test "SHARED: status describes actual-use sharing and never adds configured ceilings" {
  run mc_render_status
  [ "$status" = 0 ]
  assert_contains "$output" 'shared'
  assert_not_contains "$output" '2 GB budget'
}

@test "SHARED: legacy split remains explicit and invalid modes refuse enforcement" {
  BUDGET_MODE="split"
  run mc_watch
  assert_contains "$output" TIER2_FIRED
  BUDGET_MODE=bogus
  run mc_watch
  [ "$status" = 1 ]
  assert_not_contains "$output" TIER2_FIRED
}

@test "SHARED: profile listing names ceilings separately from the shared total" {
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/profile.sh"
  run mc_profile_list
  [ "$status" = 0 ]
  assert_contains "$output" 'shared total'
  assert_matches "$output" 'stacks.*13 GB.*20 GB'
  BUDGET_MODE="split"
  run mc_profile_list
  assert_matches "$output" 'stacks.*13 GB.*7 GB'
}
