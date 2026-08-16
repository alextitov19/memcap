load helper
setup() {
  setup_common
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  printf 'TOTAL_BUDGET_GB=16\nDOCKER_BUDGET_GB=6\nDOCKER_CPUS=8\nTIER2_MIN_AGE_SEC=999\n' > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
}

@test "listing shows the three profiles" {
  run "$MEMCAP_ROOT/bin/memcap" profile
  assert_contains "$output" "balanced"
  assert_contains "$output" "stacks"
  assert_contains "$output" "mobile"
}

@test "switching to stacks raises the docker slice" {
  "$MEMCAP_ROOT/bin/memcap" profile stacks
  run grep DOCKER_BUDGET_GB "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  assert_contains "$output" "=10"
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
  assert_contains "$output" "unknown profile"
}

@test "switching adds the key when the config lacks it" {
  printf 'TOTAL_BUDGET_GB=16\nDOCKER_CPUS=8\n' > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  "$MEMCAP_ROOT/bin/memcap" profile stacks
  run grep '^DOCKER_BUDGET_GB=' "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  [ "$status" -eq 0 ]
  [ "$output" = "DOCKER_BUDGET_GB=10" ]
}

# --- Final review, I5: profile.sh must not hardcode a 16 GB fallback ----------
# status.sh and enforce.sh both fall back to mc_cap_gb "$(mc_total_ram_gb)" when
# TOTAL_BUDGET_GB is absent; profile.sh used a literal 16. On a machine that is not
# ~16 GB, `memcap profile stacks` with no TOTAL_BUDGET_GB in the config would write a
# DOCKER_BUDGET_GB sized for the wrong cap -- on a small machine, large enough to
# leave `watch` refusing to act (agents_budget < 1) forever.
@test "profile list uses the computed cap when TOTAL_BUDGET_GB is absent, not a hardcoded 16" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  printf 'DOCKER_CPUS=8\n' > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  run bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/profile.sh'
    mc_total_ram_gb() { echo 32; }
    mc_profile_list
  "
  # cap for 32 GB is 21 (budget.bats); stacks is 65% -> 14 GB docker / 7 GB agents.
  # The old hardcoded fallback would show 16's split (10 GB / 6 GB) instead.
  # assert_matches, not assert_contains: the original ordered-substring check
  # (14 GB appearing before 7 GB on the row) is preserved via `.*` between them.
  assert_matches "$(echo "$output" | grep stacks)" "14 GB.*7 GB"
}

@test "profile set writes a DOCKER_BUDGET_GB sized for the computed cap, not 16" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  printf 'DOCKER_CPUS=8\n' > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  run bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/profile.sh'
    mc_total_ram_gb() { echo 32; }
    mc_profile_set stacks
  "
  run grep '^DOCKER_BUDGET_GB=' "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  [ "$output" = "DOCKER_BUDGET_GB=14" ]
}

@test "switching adds an active key when the existing one is commented out" {
  printf 'TOTAL_BUDGET_GB=16\n# DOCKER_BUDGET_GB=6\n' > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  "$MEMCAP_ROOT/bin/memcap" profile stacks
  run grep '^DOCKER_BUDGET_GB=' "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  [ "$status" -eq 0 ]
  [ "$output" = "DOCKER_BUDGET_GB=10" ]
}
