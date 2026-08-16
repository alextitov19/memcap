load helper

setup() {
  setup_common
}

@test "uninstall removes state but keeps the config" {
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  echo "fake log line" > "$MEMCAP_STATE_HOME/memcap/actions.log"
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  echo "TOTAL_BUDGET_GB=16" > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"

  run "$MEMCAP_ROOT/bin/memcap" uninstall
  [ "$status" -eq 0 ]
  [ ! -d "$MEMCAP_STATE_HOME/memcap" ]
  [ -f "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" ]
  run cat "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  assert_contains "$output" "TOTAL_BUDGET_GB=16"
}

@test "uninstall tells the user where the kept config lives" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  echo "TOTAL_BUDGET_GB=16" > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"

  run "$MEMCAP_ROOT/bin/memcap" uninstall
  assert_contains "$output" "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
}

# A typo or stray flag must not be able to reach the rm -rf. This is the same lesson
# as the docker-apply argument validation: the dispatcher must reject before acting,
# not after.
@test "uninstall rejects a stray argument and never touches state" {
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  touch "$MEMCAP_STATE_HOME/memcap/paused"

  run "$MEMCAP_ROOT/bin/memcap" uninstall now
  [ "$status" -eq 2 ]
  assert_contains "$output" "usage: memcap uninstall"
  [ -f "$MEMCAP_STATE_HOME/memcap/paused" ]
}

@test "state is recreated on the next command that needs it" {
  "$MEMCAP_ROOT/bin/memcap" uninstall
  [ ! -d "$MEMCAP_STATE_HOME/memcap" ]
  "$MEMCAP_ROOT/bin/memcap" off
  [ -d "$MEMCAP_STATE_HOME/memcap" ]
  [ -f "$MEMCAP_STATE_HOME/memcap/paused" ]
  "$MEMCAP_ROOT/bin/memcap" on
}

@test "uninstall is a no-op on state that was already gone" {
  run "$MEMCAP_ROOT/bin/memcap" uninstall
  [ "$status" -eq 0 ]
  [ ! -d "$MEMCAP_STATE_HOME/memcap" ]
}
