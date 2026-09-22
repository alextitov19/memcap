# Globals/stubs are used by sourced production functions.
# shellcheck disable=SC2034,SC2329,SC2016
load helper
setup() {
  setup_common
  export MC_DRY_RUN=1
  for module in common config classify status enforce feedback boot_timeout; do
    # shellcheck source=/dev/null
    source "$MEMCAP_ROOT/libexec/$module.sh"
  done
  AGENTPIDS='10'; PROTECTEDPIDS='10 30 31 32'; SIMPIDS='90'
  mc_self_ancestry() { echo '99'; }
}

@test "BOOT: owned process identity and timeout behavioral suite" {
  run python3 "$MEMCAP_ROOT/tests/test_boot_timeout.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "BOOT: no fresh authorization cannot bypass tree protection" {
  run mc_kill_pids '30 31 32' 'simulator boot timeout' boot-timeout
  [ "$status" = 1 ]
  assert_not_contains "$output" 'would kill'
}

@test "BOOT: dry run reaches choke point without notice or real signals" {
  mc_boot_prepare() { MC_BOOT_ALLOWED='30 31 32'; }
  run mc_kill_pids '30 31 32 90' 'simulator boot timeout' boot-timeout
  [ "$status" = 1 ]
  assert_contains "$output" 'would kill (simulator boot timeout):  30 31 32'
  assert_not_contains "$output" '32 90'
  [ ! -d "$MEMCAP_STATE_HOME/memcap/job-feedback" ]
}

@test "BOOT: an agent or own ancestor in the plan vetoes the whole cancellation" {
  mc_boot_prepare() { MC_BOOT_ALLOWED='10 30 31'; }
  run mc_kill_pids '10 30 31' 'simulator boot timeout' boot-timeout
  [ "$status" = 1 ]; assert_not_contains "$output" 'would kill'
  mc_boot_prepare() { MC_BOOT_ALLOWED='99 30 31'; }
  run mc_kill_pids '99 30 31' 'simulator boot timeout' boot-timeout
  [ "$status" = 1 ]; assert_not_contains "$output" 'would kill'
}

@test "BOOT: pause and bad configuration prevent authorization" {
  mc_is_paused() { return 0; }
  run mc_boot_prepare
  [ "$status" = 1 ]
  mc_is_paused() { return 1; }
  MC_CONFIG_BROKEN=1
  run mc_boot_prepare
  [ "$status" = 1 ]
}

@test "BOOT: timeout feedback names preparation and never claims tests passed" {
  mkdir -p "$BATS_TEST_TMPDIR/project"
  project=$(cd "$BATS_TEST_TMPDIR/project" && pwd -P)
  MC_BOOT_PLAN=$(jq -n --arg cwd "$project" '{group:30,job_id:"test-job",cwd:$cwd}')
  mc_boot_notice
  jq -n --arg cwd "$project" '{hook_event_name:"PostToolUseFailure",session_id:"test",cwd:$cwd}' > "$BATS_TEST_TMPDIR/input"
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]
  assert_contains "$output" 'simulator boot timed out'
  assert_contains "$output" 'reservation remains until'
  assert_not_contains "$output" 'tests passed'
}
