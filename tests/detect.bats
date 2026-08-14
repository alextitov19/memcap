load helper
setup() { setup_common;
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/detect.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/measure.sh"; }

@test "total ram is a plausible integer" {
  run mc_total_ram_gb
  [ "$output" -ge 4 ]
  [ "$output" -le 1024 ]
}

@test "cpu count is a plausible integer" {
  run mc_cpu_count
  [ "$output" -ge 1 ]
}

@test "snapshot emits pid ppid kb command lines" {
  run bash -c "source '$MEMCAP_ROOT/libexec/measure.sh'; mc_ps_snapshot | head -1"
  [[ "$output" =~ ^[[:space:]]*[0-9]+[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+ ]]
}

# Merging two data sources must not silently drop processes: a lost row is a process
# that escapes the budget entirely. Assert that against a process we KNOW is alive for
# the whole test, rather than comparing two `ps` counts taken moments apart -- the
# snapshot takes ~0.4s to build, and process churn during it makes a count comparison
# racy by construction.
@test "snapshot includes a process known to be alive throughout" {
  sleep 30 &
  marker=$!
  run bash -c "source '$MEMCAP_ROOT/libexec/measure.sh'; mc_ps_snapshot | awk -v p=$marker '\$1==p'"
  kill "$marker" 2>/dev/null || true
  [ -n "$output" ]
}

# Proportional, not absolute: churn scales with how busy the machine is, so a fixed
# slack of N processes is a flake waiting for a loaded CI runner.
@test "snapshot row count tracks ps within normal churn" {
  psn=$(ps -Ao pid= | wc -l | tr -d ' ')
  snapn=$(bash -c "source '$MEMCAP_ROOT/libexec/measure.sh'; mc_ps_snapshot | wc -l" | tr -d ' ')
  [ "$snapn" -ge $((psn * 90 / 100)) ]
}

@test "snapshot falls back to ps RSS when top is disabled" {
  run bash -c "source '$MEMCAP_ROOT/libexec/measure.sh'; MC_NO_TOP=1 mc_ps_snapshot | head -1"
  [ "$status" -eq 0 ]
  [[ "$output" =~ ^[[:space:]]*[0-9]+[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+ ]]
}

@test "free percentage is between 0 and 100" {
  run mc_free_pct
  [ "$output" -ge 0 ]
  [ "$output" -le 100 ]
}
