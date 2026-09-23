# Globals and stubs are consumed by sourced production modules.
# shellcheck disable=SC2034,SC2329,SC2016
load helper
setup() {
  setup_common
  export MC_DRY_RUN=1
  for module in common config status diagnostics feedback; do
    # shellcheck source=/dev/null
    source "$MEMCAP_ROOT/libexec/$module.sh"
  done
  mkdir -p "$BATS_TEST_TMPDIR/project/backend" "$BATS_TEST_TMPDIR/project-other"
  MC_JOB_PID=12; MC_JOB_OWNER=10; MC_JOB_KB=75497472; MC_JOB_LIMIT=4
  MC_JOB_CWD=$(cd "$BATS_TEST_TMPDIR/project/backend" && pwd -P)
  MC_JOB_IDENTITY='Thu Sep 10 python pytest patients/tests --token secret-value'
  hook_cwd=$(cd "$BATS_TEST_TMPDIR/project" && pwd -P)
}

hook_input() {
  jq -n --arg cwd "$hook_cwd" --arg session "${hook_session:-test-session}" \
    --arg event "${hook_event:-PostToolUse}" '{cwd:$cwd,session_id:$session,hook_event_name:$event}'
}

@test "FEEDBACK: agent sees job limit and batching instructions after tool failure" {
  mc_job_feedback
  hook_event=PostToolUseFailure
  # Exercise the dispatcher, not a function override lost in a subprocess.
  hook_input > "$BATS_TEST_TMPDIR/input"
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]
  assert_contains "$output" 'PostToolUseFailure'
  assert_contains "$output" '72 GB footprint'
  assert_contains "$output" '4 GB per-process limit'
  assert_contains "$output" 'smaller batches'
  assert_not_contains "$output" 'secret-value'
  assert_contains "$output" '[redacted]'
}

@test "FEEDBACK: deduplicated per session without hiding it from another agent" {
  mc_job_feedback
  hook_input > "$BATS_TEST_TMPDIR/input"
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]; assert_contains "$output" 'requested termination'
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]; [ -z "$output" ]
  hook_session=other-session
  hook_input > "$BATS_TEST_TMPDIR/input"
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]; assert_contains "$output" 'requested termination'
}

@test "FEEDBACK: adjacent projects and unknown cwd receive no notice" {
  mc_job_feedback
  hook_cwd="$BATS_TEST_TMPDIR/project-other"
  hook_input > "$BATS_TEST_TMPDIR/input"
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]; [ -z "$output" ]
  MC_JOB_CWD=''
  mc_job_feedback
  hook_cwd="$BATS_TEST_TMPDIR/project"
  hook_input > "$BATS_TEST_TMPDIR/input"
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]; [ -z "$output" ]
}

@test "FEEDBACK: hook cwd symlinks resolve to the recorded canonical project" {
  mc_job_feedback
  ln -s "$BATS_TEST_TMPDIR/project" "$BATS_TEST_TMPDIR/link"
  hook_cwd="$BATS_TEST_TMPDIR/link"
  hook_input > "$BATS_TEST_TMPDIR/input"
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]; assert_contains "$output" 'requested termination'
}

@test "FEEDBACK: event retention is bounded and records are private" {
  for MC_JOB_PID in {1..35}; do mc_job_feedback; done
  count=$(find "$MEMCAP_STATE_HOME/memcap/job-feedback" -name '*.json' | wc -l | tr -d ' ')
  [ "$count" = 32 ]
  [ "$(stat -f '%Lp' "$MEMCAP_STATE_HOME/memcap/job-feedback")" = 700 ]
  [ "$(stat -f '%Lp' "$MEMCAP_STATE_HOME/memcap/job-feedback/35.json")" = 600 ]
}

@test "FEEDBACK: hook config uses each agent supported events" {
  run "$MEMCAP_ROOT/bin/memcap" agent-hooks codex
  [ "$status" = 0 ]; assert_contains "$output" 'PostToolUse'
  assert_not_contains "$output" 'PostToolUseFailure'
  assert_contains "$output" 'PreToolUse'
  run "$MEMCAP_ROOT/bin/memcap" agent-hooks claude
  [ "$status" = 0 ]; assert_contains "$output" 'PostToolUseFailure'
}

@test "FEEDBACK: a new session gets operational guidance without a termination record" {
  hook_event=SessionStart
  hook_input > "$BATS_TEST_TMPDIR/input"
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]
  assert_contains "$output" 'TaskOutput'
  assert_contains "$output" 'independent work'
  assert_contains "$output" 'not proof of memory starvation'
  assert_not_contains "$output" 'permissionDecision'
}

@test "FEEDBACK: diagnostic behavioral suite" {
  run python3 "$MEMCAP_ROOT/tests/test_agent_diagnostics.py"
  [ "$status" = 0 ]
  assert_contains "$output" OK
}

@test "FEEDBACK: only Stop waits locally and has the matching hook timeout" {
  run "$MEMCAP_ROOT/bin/memcap" agent-hooks claude
  [ "$status" = 0 ]
  config="$output"
  run jq -er '.hooks.Stop[0].hooks[0] | select(.timeout == 75) | .command | select(endswith(" feedback --wait"))' <<< "$config"
  [ "$status" = 0 ]
  run jq -e '[.hooks | to_entries[] | select(.key != "Stop") | .value[].hooks[] | select(.timeout != 5 or (.command | endswith(" --wait")))] | length == 0' <<< "$config"
  [ "$status" = 0 ]
}

@test "FEEDBACK: local Stop wait releases the lifecycle marker before waiting" {
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/idle_gc.sh"
  cat > "$BATS_TEST_TMPDIR/hook-python" <<'SCRIPT'
#!/bin/bash
if [ "$2" = event ]; then
  printf '{"decision":"block"}'
else
  count=$(find "$MEMCAP_STATE_HOME/memcap/gc-activity-pending" -type f | wc -l)
  [ "$count" -eq 0 ] || exit 1
  printf 'wait-without-marker'
fi
SCRIPT
  chmod +x "$BATS_TEST_TMPDIR/hook-python"
  mc_gc_config() { MC_GC_PYTHON="$BATS_TEST_TMPDIR/hook-python"; }
  LIB="$MEMCAP_ROOT/libexec"
  MC_FEEDBACK_WAIT=1
  run mc_gc_event '{"hook_event_name":"Stop"}'
  [ "$status" = 0 ]
  assert_contains "$output" 'wait-without-marker'
}

@test "FEEDBACK: existing session receives updated guidance once at its next tool" {
  hook_event=PreToolUse
  hook_input > "$BATS_TEST_TMPDIR/input"
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]
  assert_contains "$output" 'memcap wait --session'
  assert_contains "$output" '--context'
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]; [ -z "$output" ]
  # Simulate a session which last saw guidance from a previous installation.
  for receipt in "$MEMCAP_STATE_HOME"/memcap/job-feedback/guidance-*.receipt; do
    printf '%s\n' old-version > "$receipt"
  done
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]
  assert_contains "$output" 'memcap wait --session'
}

@test "FEEDBACK: pause and resume refresh guidance once without a version change" {
  hook_event=PreToolUse
  hook_input > "$BATS_TEST_TMPDIR/input"
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]
  assert_contains "$output" 'enforcement: active'
  touch "$MEMCAP_STATE_HOME/memcap/paused"
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]
  assert_contains "$output" 'enforcement: paused'
  assert_contains "$output" 'native tool'
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]; [ -z "$output" ]
  rm "$MEMCAP_STATE_HOME/memcap/paused"
  run bash -c '"$MEMCAP_ROOT/bin/memcap" feedback < "$BATS_TEST_TMPDIR/input"'
  [ "$status" = 0 ]
  assert_contains "$output" 'enforcement: active'
}
