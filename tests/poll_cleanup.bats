# Globals/stubs are used by sourced production functions.
# shellcheck disable=SC2034,SC2329,SC2016
load helper
setup() {
  setup_common
  export MC_DRY_RUN=1
  LIB="$MEMCAP_ROOT/libexec"
  for module in common config classify status enforce feedback poll_cleanup; do
    # shellcheck source=/dev/null
    source "$MEMCAP_ROOT/libexec/$module.sh"
  done
  AGENTPIDS='10'; PROTECTEDPIDS='10 30 31 32'; SIMPIDS='90'
  mc_self_ancestry() { echo '99'; }
}

@test "POLL: owned process identity and timeout behavioral suite" {
  run python3 "$MEMCAP_ROOT/tests/test_poll_cleanup.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "POLL: no fresh authorization cannot bypass tree protection" {
  run mc_kill_pids '30 31 32' 'synthetic polling loop cleanup' poll-cleanup
  [ "$status" = 1 ]
  assert_not_contains "$output" 'would kill'
}

@test "POLL: dry run reaches choke point without notice or real signals" {
  mc_poll_prepare() { MC_POLL_ALLOWED='30 31 32'; }
  run mc_kill_pids '30 31 32 90' 'synthetic polling loop cleanup' poll-cleanup
  [ "$status" = 1 ]
  assert_contains "$output" 'would kill (synthetic polling loop cleanup):  30 31 32'
  assert_not_contains "$output" '32 90'
  [ ! -d "$MEMCAP_STATE_HOME/memcap/job-feedback" ]
}

@test "POLL: an agent or own ancestor in the plan vetoes the whole cancellation" {
  mc_poll_prepare() { MC_POLL_ALLOWED='10 30 31'; }
  run mc_kill_pids '10 30 31' 'synthetic polling loop cleanup' poll-cleanup
  [ "$status" = 1 ]; assert_not_contains "$output" 'would kill'
  mc_poll_prepare() { MC_POLL_ALLOWED='99 30 31'; }
  run mc_kill_pids '99 30 31' 'synthetic polling loop cleanup' poll-cleanup
  [ "$status" = 1 ]; assert_not_contains "$output" 'would kill'
}

@test "POLL: pause and bad configuration prevent authorization" {
  mc_is_paused() { return 0; }
  run mc_poll_prepare
  [ "$status" = 1 ]
  mc_is_paused() { return 1; }
  MC_CONFIG_BROKEN=1
  run mc_poll_prepare
  [ "$status" = 1 ]
}

@test "POLL: waiting-only cleanup feedback offers a tool-independent wait" {
  mkdir -p "$BATS_TEST_TMPDIR/project"
  project=$(cd "$BATS_TEST_TMPDIR/project" && pwd -P)
  MC_POLL_PLAN=$(jq -n --arg cwd "$project" '{owner:30,group:0,job_id:"test-job",cwd:$cwd}')
  mc_poll_notice
  jq -n --arg cwd "$project" '{hook_event_name:"PostToolUseFailure",session_id:"test",cwd:$cwd}' > "$BATS_TEST_TMPDIR/input"
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]
  assert_contains "$output" 'memcap wait'
  assert_contains "$output" 'synthetic waiting-only'
  assert_contains "$output" 'Respect explicit user cancellation'
}

@test "POLL: queue contention and fallback behavioral suite" {
  run python3 "$MEMCAP_ROOT/tests/test_queue_storm.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "POLL: watchdog reaper connects dry-run plans to the kill choke point" {
  mkdir -p "$MEMCAP_STATE_HOME/memcap/queue"
  echo '{}' > "$MEMCAP_STATE_HOME/memcap/queue/jobs.json"
  python3() { echo '{"job_id":"fixture"}'; }
  mc_poll_prepare() { MC_POLL_ALLOWED='30'; }
  run mc_reap_poll_loops
  [ "$status" = 0 ]
  assert_contains "$output" 'would kill (synthetic polling loop cleanup):  30'
}
