load helper
setup() { setup_common; export MC_DRY_RUN=1; }

@test "REPORT: opt-in, privacy, deduplication and transport behavior" {
  run python3 "$MEMCAP_ROOT/tests/test_report.py"
  [ "$status" = 0 ]
  assert_contains "$output" 'OK'
}

@test "REPORT: machine metrics and queue evidence stay aggregate and read-only" {
  run python3 "$MEMCAP_ROOT/tests/test_report_metrics.py"
  [ "$status" = 0 ]
  assert_contains "$output" 'OK'
}

@test "REPORT: CLI defaults to local drafts and never queues reporting" {
  run "$MEMCAP_ROOT/bin/memcap" report queue-lock
  [ "$status" = 0 ]
  assert_contains "$output" 'draft'
  [ ! -d "$MEMCAP_STATE_HOME/memcap/queue" ]
  run "$MEMCAP_ROOT/bin/memcap" report enable
  [ "$status" = 0 ]
  run "$MEMCAP_ROOT/bin/memcap" report status
  assert_contains "$output" 'enabled'
  run "$MEMCAP_ROOT/bin/memcap" report disable
  [ "$status" = 0 ]
  run "$MEMCAP_ROOT/bin/memcap" report status
  assert_contains "$output" 'disabled'
}

@test "REPORT: setup requires explicit opt-in and remembers the decision" {
  mkdir -p "$BATS_TEST_TMPDIR/home"
  run env HOME="$BATS_TEST_TMPDIR/home" "$MEMCAP_ROOT/bin/memcap" init --no-service --no-docker --no-integrations <<< $'\nn\ny\n'
  [ "$status" = 0 ]
  run "$MEMCAP_ROOT/bin/memcap" report status
  assert_contains "$output" 'enabled'
  run env HOME="$BATS_TEST_TMPDIR/home" "$MEMCAP_ROOT/bin/memcap" init --no-service --no-docker --no-integrations <<< $'\nn\n'
  [ "$status" = 0 ]
  run "$MEMCAP_ROOT/bin/memcap" report status
  assert_contains "$output" 'enabled'
}

@test "REPORT: unattended setup does not opt in" {
  mkdir -p "$BATS_TEST_TMPDIR/home"
  run env HOME="$BATS_TEST_TMPDIR/home" "$MEMCAP_ROOT/bin/memcap" init --no-service --no-docker --no-integrations </dev/null
  [ "$status" = 0 ]
  run "$MEMCAP_ROOT/bin/memcap" report status
  assert_contains "$output" 'disabled'
}

@test "REPORT: dry-run cannot accidentally enable publishing" {
  run "$MEMCAP_ROOT/bin/memcap" report enable --dry-run
  [ "$status" = 2 ]
  run "$MEMCAP_ROOT/bin/memcap" report status
  assert_contains "$output" 'disabled'
}
