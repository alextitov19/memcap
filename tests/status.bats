load helper
setup() { setup_common; }

@test "status runs and reports a budget line" {
  run "$MEMCAP_ROOT/bin/memcap" status
  [ "$status" -eq 0 ]
  assert_contains "$output" "budget"
}

@test "status names the config file it used" {
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "memcap.conf"
}

@test "status is read-only: it never logs an enforcement action" {
  run "$MEMCAP_ROOT/bin/memcap" status
  [ "$status" -eq 0 ]
  [ ! -s "$MEMCAP_STATE_HOME/memcap/actions.log" ]
}

# Review round 1, Finding 4: DOCKER_BUDGET_GB=0 means Docker is unmanaged, not that
# it has a zero-GB allowance. Rendering it as "6.41 GB / 0 GB ceiling" reads as
# catastrophically over budget when it means memcap isn't tracking Docker at all.
@test "status renders an unmanaged Docker ceiling instead of a misleading 0 GB" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  cat > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" <<-'EOF'
	TOTAL_BUDGET_GB=16
	DOCKER_BUDGET_GB=0
	EOF
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "unmanaged"
  assert_not_contains "$output" "0 GB ceiling"
}

# --- Heartbeat: a stopped service must not look like a quiet one -------------
# `status` used to print a full budget and exit 0 whether or not the service had
# run in a week -- nothing errored, nothing notified. The author's own machine
# went 28 hours unenforced before this was noticed by chance. mc_watch now
# stamps $(mc_state_dir)/last-pass with epoch seconds on every completed pass;
# these tests write that stamp directly with a controlled epoch rather than
# waiting on a real pass or sleeping out STALE_PASS_SEC.

@test "status reports how long ago the last enforcement pass was" {
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  echo "$(( $(date +%s) - 12 ))" > "$MEMCAP_STATE_HOME/memcap/last-pass"
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_matches "$output" "last enforcement pass +1[0-9]s ago"
  assert_not_contains "$output" "PROBABLY NOT RUNNING"
}

@test "status warns when the last pass is older than STALE_PASS_SEC" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap" "$MEMCAP_STATE_HOME/memcap"
  echo "STALE_PASS_SEC=60" >> "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  echo "$(( $(date +%s) - 120 ))" > "$MEMCAP_STATE_HOME/memcap/last-pass"
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "PROBABLY NOT RUNNING"
  assert_contains "$output" "memcap service install"
}

@test "status does not warn when the last pass is within STALE_PASS_SEC" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap" "$MEMCAP_STATE_HOME/memcap"
  echo "STALE_PASS_SEC=60" >> "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  echo "$(( $(date +%s) - 30 ))" > "$MEMCAP_STATE_HOME/memcap/last-pass"
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_not_contains "$output" "PROBABLY NOT RUNNING"
}

@test "status reports never run when the heartbeat file is absent" {
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "NEVER"
  assert_contains "$output" "NOT RUN SINCE INSTALL"
  assert_contains "$output" "memcap service install"
}

# A paused-but-recently-ticked service is a meaningfully different state from a
# dead one -- mc_watch stamps the heartbeat on its paused early return too, so
# `memcap off` reads as paused, not as the service having died.
@test "a paused service with a fresh heartbeat reads as paused, not dead" {
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  echo "$(( $(date +%s) - 5 ))" > "$MEMCAP_STATE_HOME/memcap/last-pass"
  touch "$MEMCAP_STATE_HOME/memcap/paused"
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "ENFORCEMENT PAUSED"
  assert_not_contains "$output" "PROBABLY NOT RUNNING"
  assert_not_contains "$output" "NEVER"
}

# A clock moved backward (NTP correction, a manual adjustment) would otherwise
# subtract to a negative age and print something absurd ("-500s ago"). Treated
# as fresh instead, since a negative duration isn't evidence of anything.
@test "a heartbeat stamped in the future does not render a negative age" {
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  echo "$(( $(date +%s) + 500 ))" > "$MEMCAP_STATE_HOME/memcap/last-pass"
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_matches "$output" "last enforcement pass +0s ago"
  assert_not_contains "$output" "PROBABLY NOT RUNNING"
}
