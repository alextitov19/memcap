# Globals and stubs are consumed by sourced production modules.
# shellcheck disable=SC2034,SC2329
load helper
setup() {
  setup_common
  export MC_DRY_RUN=1
  for module in common config budget detect measure classify roots status enforce docker; do
    # shellcheck source=/dev/null
    source "$MEMCAP_ROOT/libexec/$module.sh"
  done
  if [ -f "$MEMCAP_ROOT/libexec/diagnostics.sh" ]; then
    # shellcheck source=/dev/null
    source "$MEMCAP_ROOT/libexec/diagnostics.sh"
  fi
  export MC_DOCKER_RUNTIME=none
  MC_DOCKER_STORE="$BATS_TEST_TMPDIR/no-store"
  df() { printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/test 100000000 80000000 20000000 80%% /\n'; }
  sysctl() { printf 'total = 1024.00M  used = 0.00M  free = 1024.00M (encrypted)\n'; }
}

@test "PRESSURE: exhausted allocated swap is normal when usage is small" {
  sysctl() { printf 'total = 1024.00M used = 1024.00M free = 0.00M (encrypted)\n'; }
  mc_host_pressure
  [ "$MC_HOST_PRESSURE" = 0 ]
  [ "$MC_HOST_SWAP_KB" = 1048576 ]
}

@test "PRESSURE: low disk and high swap are independent triggers" {
  df() { printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/test 100000000 99000000 1000000 99%% /\n'; }
  mc_host_pressure
  [ "$MC_HOST_PRESSURE" = 1 ]
  assert_contains "$MC_HOST_SUMMARY" 'disk'
  df() { printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/test 100000000 10000000 90000000 10%% /\n'; }
  sysctl() { printf 'total = 12.00G used = 9.00G free = 3.00G\n'; }
  mc_host_pressure
  [ "$MC_HOST_PRESSURE" = 1 ]
  [ "$MC_HOST_SWAP_KB" = 9437184 ]
}

@test "PRESSURE: failed probes are unknown rather than healthy zeroes" {
  df() { return 1; }
  sysctl() { printf 'not a swap reading\n'; }
  mc_host_pressure
  [ "$MC_HOST_FAULT" = 1 ]
  [ "$MC_HOST_DISK_KB" = unknown ]
  [ "$MC_HOST_SWAP_KB" = unknown ]
}

@test "PRESSURE: impossible and partial swap readings are rejected" {
  sysctl() { printf 'total = 1.00G used = 9.00G free = 0.00G\n'; }
  mc_host_pressure
  [ "$MC_HOST_SWAP_KB" = unknown ]
  sysctl() { printf 'used = 1.00G\n'; }
  mc_host_pressure
  [ "$MC_HOST_SWAP_KB" = unknown ]
}

@test "STATE: failed replacement preserves the previous heartbeat and reports its path" {
  file="$MEMCAP_STATE_HOME/memcap/last-pass"
  mkdir -p "${file%/*}"
  printf '123\n' > "$file"
  mv() { return 1; }
  run mc_state_write "$file" 456
  [ "$status" -ne 0 ]
  assert_contains "$output" 'state write failed'
  assert_contains "$output" 'last-pass'
  [ "$(cat "$file")" = 123 ]
}

@test "STATE: audit log failures retain the event on timestamped stderr" {
  mkdir -p "$MEMCAP_STATE_HOME/memcap/actions.log"
  run mc_log 'fixture pressure event'
  [ "$status" = 0 ]
  assert_contains "$output" 'fixture pressure event'
  assert_contains "$output" 'audit log unavailable'
  assert_matches "$output" '\[[0-9]{4}-[0-9]{2}-[0-9]{2}'
}

@test "SNAPSHOT: includes unclassified Python and protected workers with bounded private records" {
  AGENTPIDS='1'; PROTECTEDPIDS='1 2'; SIMPIDS=''; ORPHANS=''; DEVPIDS=''
  sample=$(printf '1 0 100 /bin/codex\n2 1 50000000 /bin/python worker.py --token=secret-value\n3 1 25000000 /bin/python detached.py\n')
  mc_pressure_capture "$sample" 'fixture pressure'
  file=$(find "$MEMCAP_STATE_HOME/memcap/pressure" -name '*.log' | head -1)
  [ -n "$file" ]
  assert_contains "$(cat "$file")" 'agent-protected'
  assert_contains "$(cat "$file")" 'unclassified'
  assert_contains "$(cat "$file")" 'detached.py'
  assert_not_contains "$(cat "$file")" 'secret-value'
  [ "$(stat -f '%Lp' "$file")" = 600 ]
  [ "$(stat -f '%Lp' "${file%/*}")" = 700 ]
}

@test "SNAPSHOT: retention and row count stay bounded even with throttling disabled" {
  PRESSURE_SNAPSHOT_SEC=0
  sample=$(awk 'BEGIN { for (i=1;i<=40;i++) print i,1,i*1000,"/bin/python worker.py" }')
  for _ in $(seq 1 15); do mc_pressure_capture "$sample" 'fixture'; done
  [ "$(find "$MEMCAP_STATE_HOME/memcap/pressure" -name '*.log' | wc -l | tr -d ' ')" -le 12 ]
  file=$(find "$MEMCAP_STATE_HOME/memcap/pressure" -name '*.log' | head -1)
  [ "$(awk '/^pid=/ { count++ } END { print count+0 }' "$file")" = 20 ]
}

@test "SNAPSHOT: repeated pressure is throttled and recovery permits immediate recapture" {
  sample='3 1 25000000 /bin/python detached.py'
  mc_pressure_capture "$sample" fixture
  mc_pressure_capture "$sample" fixture
  [ "$(cat "$MEMCAP_STATE_HOME/memcap/pressure/next")" = 1 ]
  mc_pressure_recovered
  mc_pressure_capture "$sample" fixture
  [ "$(cat "$MEMCAP_STATE_HOME/memcap/pressure/next")" = 2 ]
}

watch_fixture() {
  TOTAL_BUDGET_GB=16; DOCKER_BUDGET_GB=4
  mc_total_ram_gb() { echo 24; }
  mc_free_pct() { echo 50; }
  mc_record_roots() { :; }
  mc_ps_snapshot() { printf '9001 1 13631488 /bin/codex\n9002 1 5242880 /path/ms-playwright/chrome\n9003 1 10485760 /Applications/Docker.app/worker\n'; }
  mc_reap_orphans() { :; }
  mc_reap_sims() { :; }
  mc_kill_over_budget() { mc_log 'fixture tier2 declined'; }
}

@test "WATCH: combined warning survives the over-agent-budget branch" {
  watch_fixture
  run mc_watch
  [ "$status" = 0 ]
  assert_contains "$(cat "$MEMCAP_STATE_HOME/memcap/actions.log")" 'combined 28.00 GB'
}

@test "WATCH: pressure captures unclassified workers before cleanup" {
  watch_fixture
  mc_ps_snapshot() { printf '9002 1 52428800 /bin/python worker.py\n'; }
  mc_free_pct() { echo 4; }
  run mc_watch
  [ "$status" = 0 ]
  file=$(find "$MEMCAP_STATE_HOME/memcap/pressure" -name '*.log' | head -1)
  [ -n "$file" ]
  assert_contains "$(cat "$file")" 'worker.py'
}

@test "WATCH: unreliable measurement never reaches tier2" {
  watch_fixture
  mc_ps_snapshot() {
    mc_measure_mark top-failed 1
    mc_measure_persist
    printf '9001 1 20000000 /bin/codex\n'
  }
  mc_kill_over_budget() { echo unsafe-tier2 > "$BATS_TEST_TMPDIR/tier2"; }
  run mc_watch
  [ "$status" = 0 ]
  [ ! -f "$BATS_TEST_TMPDIR/tier2" ]
}

@test "STATUS: over-budget outcome describes protected work without claiming a kill" {
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  printf 'over-budget\n' > "$MEMCAP_STATE_HOME/memcap/last-outcome"
  MC_STATUS_WARNINGS=''
  run mc_render_outcome
  assert_contains "$output" 'OVER BUDGET'
  assert_not_contains "$output" 'unrecognised'
}

@test "WATCH: measurement fault survives an unwritable health record" {
  watch_fixture
  mc_ps_snapshot() {
    mc_measure_mark top-failed 1
    printf '9001 1 20000000 /bin/codex\n'
  }
  mc_kill_over_budget() { echo unsafe > "$BATS_TEST_TMPDIR/tier2"; }
  run mc_watch
  [ "$status" = 0 ]
  [ ! -f "$BATS_TEST_TMPDIR/tier2" ]
}

@test "PRESSURE: an unavailable probe cannot announce recovery" {
  MC_HOST_FAULT=0; MC_HOST_PRESSURE=1; MC_HOST_SUMMARY='low disk'
  mc_host_report
  MC_HOST_FAULT=1; MC_HOST_PRESSURE=0; MC_HOST_SUMMARY='disk unknown'
  mc_host_report
  assert_not_contains "$(cat "$MEMCAP_STATE_HOME/memcap/actions.log")" 'warning cleared'
}

@test "CLI: diagnostics reports an empty history without sampling or creating state" {
  run "$MEMCAP_ROOT/bin/memcap" diagnostics
  [ "$status" = 0 ]
  assert_contains "$output" 'No pressure snapshots'
  [ ! -d "$MEMCAP_STATE_HOME/memcap/pressure" ]
}

@test "CLI: diagnostics reads the latest stored snapshot" {
  mkdir -p "$MEMCAP_STATE_HOME/memcap/pressure"
  printf 'stored forensic fixture\n' > "$MEMCAP_STATE_HOME/memcap/pressure/pressure-0.log"
  run "$MEMCAP_ROOT/bin/memcap" diagnostics
  [ "$status" = 0 ]
  assert_contains "$output" 'stored forensic fixture'
}

@test "STATUS: drift exposes effective allowances without changing agent policy" {
  watch_fixture
  MC_DOCKER_RUNTIME=desktop
  MC_DOCKER_CEILING_MIB=10240
  run mc_render_status
  [ "$status" = 0 ]
  assert_contains "$output" '22 GB / 16 GB target'
  assert_contains "$output" '12 GB budget'
  assert_contains "$output" 'host disk available'
  assert_contains "$output" 'host swap used'
}

@test "SNAPSHOT: ancestry is recorded even when the parent is outside the top twenty" {
  sample=$(awk 'BEGIN { print "100 1 1 /bin/codex"; for(i=101;i<=125;i++) print i,100,1000000,"/bin/python worker.py" }')
  mc_pressure_capture "$sample" fixture
  file=$(find "$MEMCAP_STATE_HOME/memcap/pressure" -name '*.log' | head -1)
  assert_contains "$(cat "$file")" 'ancestry=100:/bin/codex'
}

@test "STATE: a heartbeat failure cannot leave a healthy completed outcome" {
  MC_STATE_WRITE_FAILED=0
  mc_stamp_heartbeat() { MC_STATE_WRITE_FAILED=1; }
  mc_finish_pass enforced
  [ "$(cat "$MEMCAP_STATE_HOME/memcap/last-outcome")" = state-error ]
}
