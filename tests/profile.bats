load helper
setup() {
  setup_common
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  printf 'TOTAL_BUDGET_GB=16\nDOCKER_BUDGET_GB=6\nDOCKER_CPUS=8\nTIER2_MIN_AGE_SEC=999\n' > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
}

@test "listing shows the three profiles" {
  run "$MEMCAP_ROOT/bin/memcap" profile
  [[ "$output" == *balanced* ]]
  [[ "$output" == *stacks* ]]
  [[ "$output" == *mobile* ]]
}

@test "switching to stacks raises the docker slice" {
  "$MEMCAP_ROOT/bin/memcap" profile stacks
  run grep DOCKER_BUDGET_GB "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  [[ "$output" == *=10* ]]
}

@test "switching preserves unrelated settings" {
  "$MEMCAP_ROOT/bin/memcap" profile mobile
  run grep '^TIER2_MIN_AGE_SEC=' "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  [ "$status" -eq 0 ]
  [ "$output" = "TIER2_MIN_AGE_SEC=999" ]
}

@test "appending to a config with no trailing newline does not corrupt the last key" {
  printf 'TOTAL_BUDGET_GB=16\nDOCKER_CPUS=8' > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  "$MEMCAP_ROOT/bin/memcap" profile stacks
  run grep -c '^DOCKER_CPUS=8$' "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  [ "$output" = "1" ]
  run grep '^DOCKER_BUDGET_GB=' "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  [ "$output" = "DOCKER_BUDGET_GB=10" ]
}

@test "a failed write reports failure instead of false success" {
  printf 'TOTAL_BUDGET_GB=16\n' > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  chmod 444 "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  run "$MEMCAP_ROOT/bin/memcap" profile stacks
  chmod 644 "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  [ "$status" -ne 0 ]
}

@test "switching preserves the config file mode" {
  chmod 644 "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  "$MEMCAP_ROOT/bin/memcap" profile mobile
  run stat -f '%Lp' "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  [ "$output" = "644" ]
}

@test "an unknown profile is rejected" {
  run "$MEMCAP_ROOT/bin/memcap" profile bogus
  [ "$status" -ne 0 ]
  [[ "$output" == *"unknown profile"* ]]
}

@test "switching adds the key when the config lacks it" {
  printf 'TOTAL_BUDGET_GB=16\nDOCKER_CPUS=8\n' > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  "$MEMCAP_ROOT/bin/memcap" profile stacks
  run grep '^DOCKER_BUDGET_GB=' "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  [ "$status" -eq 0 ]
  [ "$output" = "DOCKER_BUDGET_GB=10" ]
}

@test "switching adds an active key when the existing one is commented out" {
  printf 'TOTAL_BUDGET_GB=16\n# DOCKER_BUDGET_GB=6\n' > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  "$MEMCAP_ROOT/bin/memcap" profile stacks
  run grep '^DOCKER_BUDGET_GB=' "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  [ "$status" -eq 0 ]
  [ "$output" = "DOCKER_BUDGET_GB=10" ]
}
