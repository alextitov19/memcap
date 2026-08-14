load helper
setup() { setup_common; }

@test "status runs and reports a budget line" {
  run "$MEMCAP_ROOT/bin/memcap" status
  [ "$status" -eq 0 ]
  [[ "$output" == *budget* ]]
}

@test "status names the config file it used" {
  run "$MEMCAP_ROOT/bin/memcap" status
  [[ "$output" == *memcap.conf* ]]
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
  [[ "$output" == *"unmanaged"* ]]
  [[ "$output" != *"0 GB ceiling"* ]]
}
