# Globals/stubs are consumed by the sourced enforcement module.
# shellcheck disable=SC2034,SC2329
load helper
setup() {
  setup_common
  export MC_DRY_RUN=1
  for module in common config enforce idle_gc; do
    # shellcheck source=/dev/null
    source "$MEMCAP_ROOT/libexec/$module.sh"
  done
}

@test "GC: lifecycle, ownership, idleness and authorization behavioral suite" {
  run python3 "$MEMCAP_ROOT/tests/test_idle_gc.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "GC: no authorization cannot bypass a live agent tree" {
  AGENTPIDS='10'; PROTECTEDPIDS='10 12'
  mc_self_ancestry() { echo '99'; }
  mc_gc_prepare() { return 0; }
  mc_gc_allowed() { return 1; }
  run mc_kill_pids '10 12 99' 'fixture GC' idle-gc
  [ "$status" = 1 ]
  assert_not_contains "$output" 'would kill'
}

@test "GC: even authorized helpers cannot expose agent CLI or ancestry" {
  AGENTPIDS='10'; PROTECTEDPIDS='10 12'
  mc_self_ancestry() { echo '99'; }
  mc_gc_prepare() { return 0; }
  mc_gc_allowed() { return 0; }
  run mc_kill_pids '10 12 99' 'fixture GC' idle-gc
  [ "$status" = 1 ]
  assert_contains "$output" 'would kill (fixture GC):  12'
  assert_not_contains "$output" '10'
  assert_not_contains "$output" '99'
}

@test "GC: default observes, pause and off prohibit authorizations" {
  run mc_gc_prepare 12
  [ "$status" = 1 ]
  GC_MODE=on
  mc_is_paused() { return 0; }
  run mc_gc_prepare 12
  [ "$status" = 1 ]
}

@test "GC: generated lifecycle hooks include completion and child activity" {
  run "$MEMCAP_ROOT/bin/memcap" agent-hooks claude --queue
  [ "$status" = 0 ]
  assert_contains "$output" '"Stop"'
  assert_contains "$output" '"SubagentStart"'
  assert_contains "$output" '"SubagentStop"'
}
