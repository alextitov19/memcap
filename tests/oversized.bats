# Globals and stubs are consumed by sourced production modules.
# shellcheck disable=SC2034,SC2329,SC2030,SC2031
load helper
setup() {
  setup_common
  export MC_DRY_RUN=1
  for module in common config measure classify status enforce; do
    # shellcheck source=/dev/null
    source "$MEMCAP_ROOT/libexec/$module.sh"
  done
  if [ -f "$MEMCAP_ROOT/libexec/jobs.sh" ]; then
    # shellcheck source=/dev/null
    source "$MEMCAP_ROOT/libexec/jobs.sh"
  fi
  AGENTPIDS='10'; PROTECTEDPIDS='10 11 12'; SIMPIDS=''
  fixture_table='10 1 /usr/local/bin/claude
11 10 /bin/zsh
12 11 /project/.venv/bin/python /project/.venv/bin/pytest patients/tests
30 1 /usr/bin/python manual.py'
  fixture_sample='10 1 100000 /usr/local/bin/claude
11 10 1000 /bin/zsh
12 11 75497472 /project/.venv/bin/python /project/.venv/bin/pytest patients/tests
30 1 90000000 /usr/bin/python manual.py'
  fixture_kb=75497472
  kill() { [ "$1" = -0 ]; }
  ps() { printf '%s\n' "$fixture_table"; }
  mc_pid_identity() { printf 'start-identity-%s' "$1"; }
  mc_self_ancestry() { printf ' 99 '; }
  mc_job_footprint_kb() { printf '%s' "$fixture_kb"; }
  mc_job_cwd() { printf '%s' "$BATS_TEST_TMPDIR/project"; }
  mkdir -p "$BATS_TEST_TMPDIR/project"
  MC_VETO_EVIDENCE_CACHE=''
}

@test "JOBS: oversized live Claude child reaches the dry-run kill choke point" {
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_contains "$output" 'would kill (oversized-job):  12'
  assert_not_contains "$output" 'oversized-job):  30'
  [ ! -d "$MEMCAP_STATE_HOME/memcap/job-feedback" ]
}

@test "JOBS: Codex descendants are eligible too" {
  fixture_table="${fixture_table/claude/codex}"
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_contains "$output" 'oversized-job):  12'
}

@test "JOBS: below-limit and explicitly disabled jobs are spared" {
  fixture_kb=100
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_not_contains "$output" 'would kill'
  fixture_kb=75497472
  AGENT_JOB_MAX_GB=0
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_not_contains "$output" 'would kill'
}

@test "JOBS: pause and broken config prevent the new tier acting directly" {
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  touch "$MEMCAP_STATE_HOME/memcap/paused"
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_not_contains "$output" 'would kill'
  rm "$MEMCAP_STATE_HOME/memcap/paused"
  MC_CONFIG_BROKEN=1
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_not_contains "$output" 'would kill'
}

@test "JOBS: mobile veto and unrelated missing measurements cannot shelter a confirmed huge job" {
  MC_ACTIVE_MOBILE_TOOLING=1; MC_HANDS_ON_MOBILE=1; MC_MEASURE_FAULT=1
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_contains "$output" 'oversized-job):  12'
}

@test "JOBS: fresh footprint failure refuses rather than using RSS" {
  mc_job_footprint_kb() { return 1; }
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_not_contains "$output" 'would kill'
  assert_contains "$(cat "$MEMCAP_STATE_HOME/memcap/actions.log")" 'cannot confirm footprint'
}

@test "JOBS: agent CLI and memcap ancestry are always protected" {
  fixture_sample='10 1 75497472 /usr/local/bin/claude'
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_not_contains "$output" 'would kill'
  fixture_sample='12 11 75497472 /project/python'
  mc_self_ancestry() { printf ' 99 12 '; }
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_not_contains "$output" 'would kill'
}

@test "JOBS: a command argument mentioning Claude proves no ownership" {
  fixture_table='10 1 /usr/bin/rg claude
11 10 /bin/zsh
12 11 /project/python'
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_not_contains "$output" 'would kill'
}

@test "JOBS: unknown cyclic and non-Claude/Codex ancestry are ineligible" {
  fixture_table='12 11 /project/python
11 12 /bin/zsh'
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_not_contains "$output" 'would kill'
  fixture_table='12 500 /project/python'
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_not_contains "$output" 'would kill'
  fixture_table='10 1 /bin/aider
12 10 /project/python'
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_not_contains "$output" 'would kill'
}

@test "JOBS: PID identity change during measurement prevents killing" {
  mc_job_footprint_kb() { touch "$BATS_TEST_TMPDIR/reused"; printf '75497472'; }
  mc_pid_identity() {
    if [ "$1" = 12 ] && [ -f "$BATS_TEST_TMPDIR/reused" ]; then echo changed; else echo original; fi
  }
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_not_contains "$output" 'would kill'
}

@test "JOBS: Docker and simulator processes remain under their own policies" {
  fixture_table='10 1 /bin/codex
12 10 /Applications/Docker.app/Contents/MacOS/com.docker.backend'
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_not_contains "$output" 'would kill'
  fixture_table='10 1 /bin/codex
12 10 /Library/Developer/CoreSimulator/launchd_sim'
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]
  assert_not_contains "$output" 'would kill'
}

@test "JOBS: the new protection scope cannot be invoked without a validated target" {
  run mc_filter_protected '12 30' oversized
  [ "$status" = 0 ]
  [ -z "$output" ]
  run mc_filter_protected '12' full
  [ -z "$output" ]
}

@test "JOBS: native footprint parser never falls back to RSS" {
  unset -f mc_job_footprint_kb
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/jobs.sh"
  top() { printf 'PID MEM\n12 5G\n'; }
  run mc_job_footprint_kb 12
  [ "$status" = 0 ]; [ "$output" = 5242880 ]
  top() { printf 'PID MEM\n12 nonsense\n'; }
  run mc_job_footprint_kb 12
  [ "$status" != 0 ]
  top() { return 1; }
  run mc_job_footprint_kb 12
  [ "$status" != 0 ]
}

@test "JOBS: foreign ownership and an agent that changed identity are spared" {
  kill() { return 1; }
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]; assert_not_contains "$output" 'would kill'
  kill() { [ "$1" = -0 ]; }
  mc_job_footprint_kb() { touch "$BATS_TEST_TMPDIR/owner-reused"; printf '75497472'; }
  mc_pid_identity() {
    if [ "$1" = 10 ] && [ -f "$BATS_TEST_TMPDIR/owner-reused" ]; then echo changed; else echo original; fi
  }
  run mc_reap_oversized "$fixture_sample"
  [ "$status" = 0 ]; assert_not_contains "$output" 'would kill'
}

@test "JOBS: feedback exists before mocked TERM and escalation uses the same choke point" {
  # No real signal can be sent: kill is a shell function capturing every call.
  kill() {
    [ "$1" = -0 ] && return 0
    [ -f "$BATS_TEST_TMPDIR/feedback" ] || return 1
    printf '%s\n' "$*" >> "$BATS_TEST_TMPDIR/signals"
  }
  mc_job_feedback() { touch "$BATS_TEST_TMPDIR/feedback"; }
  mc_pid_alive() { return 0; }
  sleep() { :; }
  MC_DRY_RUN=0 mc_reap_oversized "$fixture_sample"
  assert_contains "$(cat "$BATS_TEST_TMPDIR/signals")" '-TERM 12'
  assert_contains "$(cat "$BATS_TEST_TMPDIR/signals")" '-KILL 12'
}

@test "JOBS: PID reuse while feedback is written prevents TERM" {
  kill() {
    [ "$1" = -0 ] && return 0
    printf '%s\n' "$*" >> "$BATS_TEST_TMPDIR/signals"
  }
  mc_job_feedback() { touch "$BATS_TEST_TMPDIR/reused"; }
  mc_pid_identity() {
    if [ "$1" = 12 ] && [ -f "$BATS_TEST_TMPDIR/reused" ]; then echo changed; else echo original; fi
  }
  MC_DRY_RUN=0 mc_reap_oversized "$fixture_sample"
  [ ! -f "$BATS_TEST_TMPDIR/signals" ]
}
