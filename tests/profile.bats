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

# --- Final review, residual: a stat failure must not default to 644 ----------
# `stat -f '%Lp' "$conf" 2>/dev/null || echo 644` treated a stat failure as "the
# file is 644", which could WIDEN a config the user deliberately set to 600.
# There is no safe guess here; a stat failure must be fatal instead.
@test "a stat failure on the config mode is fatal, not a silent 644 default" {
  # DOCKER_BUDGET_GB must already be present: only the sed/mv (rewrite) branch
  # reads the mode at all -- the append branch below it never does -- so an
  # absent key would take that branch instead and never reach mc_stat_mode.
  printf 'TOTAL_BUDGET_GB=16\nDOCKER_BUDGET_GB=6\n' > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  chmod 600 "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"

  # mc_profile_set calls /usr/bin/stat directly (not bare `stat`), to sidestep
  # GNU coreutils shadowing it on PATH -- which also means a PATH-based stub can
  # no longer intercept it. mc_stat_mode exists specifically so a test can
  # override the FUNCTION instead, the same pattern docker.bats already uses
  # for mc_docker_runtime.
  run bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/budget.sh'
    source '$MEMCAP_ROOT/libexec/detect.sh'
    source '$MEMCAP_ROOT/libexec/profile.sh'
    mc_stat_mode() { return 1; }
    mc_profile_set stacks
  "
  [ "$status" -ne 0 ]

  # The config itself must be untouched: still 600, still its original content --
  # not rewritten under a guessed mode.
  run /usr/bin/stat -f '%Lp' "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  [ "$output" = "600" ]
  run cat "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  assert_contains "$output" "TOTAL_BUDGET_GB=16"
  # Original value, unchanged -- not rewritten to whatever `stacks` would have
  # set DOCKER_BUDGET_GB to.
  assert_contains "$output" "DOCKER_BUDGET_GB=6"
}
