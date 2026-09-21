# Globals and function stubs are read by sourced production modules.
# shellcheck disable=SC2034,SC2329
load helper

setup() {
  setup_common
  export MC_DRY_RUN=1
}

@test "QUEUE: scheduler behavioral suite" {
  run python3 "$MEMCAP_ROOT/tests/test_scheduler.py"
  [ "$status" = 0 ]
  assert_contains "$output" 'OK'
}

@test "QUEUE: CLI exposes queue and rejects a missing command" {
  run "$MEMCAP_ROOT/bin/memcap" queue
  [ "$status" = 0 ]
  assert_contains "$output" 'No queued or running jobs'
  run "$MEMCAP_ROOT/bin/memcap" run
  [ "$status" -ne 0 ]
}

@test "QUEUE: generated hooks opt in without replacing feedback" {
  run "$MEMCAP_ROOT/bin/memcap" agent-hooks codex --queue
  [ "$status" = 0 ]
  assert_contains "$output" 'queue-hook'
  assert_contains "$output" 'feedback'
  run "$MEMCAP_ROOT/bin/memcap" agent-hooks claude --queue
  [ "$status" = 0 ]
  assert_contains "$output" 'queue-hook'
}

@test "QUEUE: configured pressure policy reaches the runner and rejects red" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  printf 'QUEUE_MAX_PRESSURE=yellow\n' > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  run "$MEMCAP_ROOT/bin/memcap" queue --json
  [ "$status" = 0 ]
  assert_contains "$output" '[]'
  printf 'QUEUE_MAX_PRESSURE=red\n' > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  run "$MEMCAP_ROOT/bin/memcap" queue --json
  [ "$status" = 75 ]
  assert_contains "$output" 'QUEUE_MAX_PRESSURE must be green or yellow'
}

sample_fixture() {
  for module in common config budget detect measure classify status scheduler; do
    # shellcheck source=/dev/null
    source "$MEMCAP_ROOT/libexec/$module.sh"
  done
  mc_snapshot_capture() {
    MC_MEASURE_DEGRADED=0; MC_MEASURE_FAULT=0
    MC_CAPTURE_SNAPSHOT=$'100 1 1024 claude\n101 100 2048 node app.js\n300 999 4096 unrelated\n\n'
  }
  mc_free_pct() { echo 50; }
  sysctl() {
    case "$*" in
      '-n hw.memsize') echo 25769803776 ;;
      '-n kern.memorystatus_vm_pressure_level') echo "${fixture_pressure:-1}" ;;
      *) return 1 ;;
    esac
  }
  TOTAL_BUDGET_GB=16
}

@test "QUEUE: sampler reports footprint, host headroom and tracked membership" {
  sample_fixture
  run mc_queue_sample
  [ "$status" = 0 ]
  assert_contains "$output" '16777216 3072 12582912 1 0'
  assert_contains "$output" '100 1024 1'
  assert_contains "$output" '101 2048 1'
  assert_contains "$output" '300 4096 0'
  [ "${#lines[@]}" = 4 ]
}

@test "QUEUE: unknown pressure is a measurement fault rather than normal" {
  sample_fixture
  fixture_pressure=unknown
  run mc_queue_sample
  [ "$status" = 0 ]
  assert_contains "$output" '16777216 3072 12582912 0 1'
}

@test "QUEUE: bad configuration produces a blocking hook decision" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  printf 'TOTAL_BUDGET_GB="\n' > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  run "$MEMCAP_ROOT/bin/memcap" queue-hook claude
  [ "$status" = 0 ]
  assert_contains "$output" '"permissionDecision":"deny"'
}

@test "QUEUE: scheduled cancellation preserves CLI and ancestry protection" {
  for module in common config measure classify status enforce; do
    # shellcheck source=/dev/null
    source "$MEMCAP_ROOT/libexec/$module.sh"
  done
  AGENTPIDS='10'; PROTECTEDPIDS='10 11 12'
  MC_VETO_EVIDENCE_CACHE=''
  mc_self_ancestry() { echo ' 99 '; }
  mc_scheduled_allowed() { return 0; }
  run mc_kill_pids '10 11 12 99' 'scheduled fixture' scheduled
  [ "$status" = 1 ]
  assert_contains "$output" 'would kill (scheduled fixture):  11 12'
  assert_not_contains "$output" '10'
  assert_not_contains "$output" '99'
}

@test "QUEUE: unregistered cancellation cannot bypass full-tree protection" {
  for module in common config measure classify status enforce; do
    # shellcheck source=/dev/null
    source "$MEMCAP_ROOT/libexec/$module.sh"
  done
  AGENTPIDS='10'; PROTECTEDPIDS='10 11 12'
  MC_VETO_EVIDENCE_CACHE=''
  mc_self_ancestry() { echo ' 99 '; }
  mc_scheduled_allowed() { return 1; }
  run mc_kill_pids '11 12' 'scheduled fixture' scheduled
  [ "$status" = 1 ]
  assert_not_contains "$output" 'would kill'
}
