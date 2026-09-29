# shellcheck disable=SC2034,SC2329
load helper
setup() {
  setup_common
  export MC_DRY_RUN=1
  for module in common config enforce orphan_recovery; do
    # shellcheck source=/dev/null
    source "$MEMCAP_ROOT/libexec/$module.sh"
  done
}

@test "orphan recovery: synthetic lifecycle and reservation suite" {
  run python3 "$MEMCAP_ROOT/tests/test_orphan_recovery.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "open issues 241-257: native inspection and remote helper behavior" {
  run python3 "$MEMCAP_ROOT/tests/test_issue_241_257.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "orphan recovery: pause and missing authorization forbid signals" {
  mc_is_paused() { return 0; }
  run mc_orphan_prepare
  [ "$status" = 1 ]
  AGENTPIDS='10'; PROTECTEDPIDS='10 20'
  mc_self_ancestry() { echo '99'; }
  run mc_kill_pids '20' fixture orphan-recovery
  [ "$status" = 1 ]
  assert_not_contains "$output" 'would kill'
}

@test "orphan recovery: authorized group still excludes agent and self ancestry" {
  AGENTPIDS='10'; PROTECTEDPIDS='10 20'
  mc_self_ancestry() { echo '99'; }
  mc_orphan_prepare() { MC_ORPHAN_ALLOWED='10 20'; }
  run mc_kill_pids '10 20' fixture orphan-recovery
  [ "$status" = 1 ]
  assert_not_contains "$output" 'would kill'
  mc_orphan_prepare() { MC_ORPHAN_ALLOWED='20 99'; }
  run mc_kill_pids '20 99' fixture orphan-recovery
  [ "$status" = 1 ]
  assert_not_contains "$output" 'would kill'
  mc_orphan_prepare() { MC_ORPHAN_ALLOWED='20'; }
  run mc_kill_pids '20' fixture orphan-recovery
  [ "$status" = 1 ]
  assert_contains "$output" 'would kill (fixture):  20'
}
