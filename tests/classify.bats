load helper

setup() { setup_common;
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/classify.sh"; }

fixture() { cat "$MEMCAP_ROOT/tests/fixtures/$1"; }

@test "orphaned dev servers are counted as orphans" {
  eval "$(fixture orphan-storm.txt | mc_classify)"
  assert_contains "$ORPHANS" "8506"
  assert_contains "$ORPHANS" "8507"
}

@test "an esbuild child with a live parent is NOT an orphan" {
  eval "$(fixture orphan-storm.txt | mc_classify)"
  assert_not_contains "$ORPHANS" "8524"
}

@test "the agent process itself is found and is never an orphan" {
  eval "$(fixture orphan-storm.txt | mc_classify)"
  assert_contains "$AGENTPIDS" "91633"
  assert_not_contains "$ORPHANS" "91633"
}

@test "REGRESSION: 'rg ms-playwright' is not a simulator" {
  eval "$(fixture mixed.txt | mc_classify)"
  assert_not_contains "$SIMPIDS" "12589"
}

@test "REGRESSION: a real playwright browser IS a simulator" {
  eval "$(fixture mixed.txt | mc_classify)"
  assert_contains "$SIMPIDS" "12600"
}

@test "REGRESSION: Maestro's JVM IS a simulator" {
  eval "$(fixture mixed.txt | mc_classify)"
  assert_contains "$SIMPIDS" "91650"
}

@test "REGRESSION: grepping for a maestro/playwright marker is not itself a simulator (MC_SIM_SKIP)" {
  # Without MC_SIM_SKIP excluding /grep as an exe, this line's argument
  # ("maestro.cli") matches MC_SIM_ARG and the process would land in SIMPIDS --
  # tier 3's kill-candidate list -- for having merely searched for the string.
  eval "$(printf '%s\n' '12700 91633 4976 /opt/homebrew/bin/grep -r maestro.cli /Users/x/proj' | mc_classify)"
  assert_not_contains "$SIMPIDS" "12700"
}

@test "EXTRA_AGENTS from the config extends the agent list" {
  # shellcheck disable=SC2034  # consumed by mc_classify via awk -v
  EXTRA_AGENTS="mycustomagent"
  eval "$(printf '%s\n' '  999     1  50000 /usr/local/bin/mycustomagent --run' | mc_classify)"
  assert_contains "$AGENTPIDS" "999"
}

# --- Final review, C2: EXTRA_AGENTS with more than one name -------------------
# detect.sh word-splits EXTRA_AGENTS; classify.sh spliced the raw string into the
# awk ERE as a single alternation branch, so a two-name value only matched the
# literal string "myagent otheragent" -- which no real command line produces --
# and silently voided mc_filter_protected's protection for both names.
@test "EXTRA_AGENTS with more than one name protects every name, not just a literal match" {
  # shellcheck disable=SC2034  # consumed by mc_classify via awk -v
  EXTRA_AGENTS="myagent otheragent"
  eval "$(printf '%s\n%s\n' '998 1 50000 /usr/local/bin/myagent --run' '999 1 50000 /usr/local/bin/otheragent --run' | mc_classify)"
  assert_contains "$AGENTPIDS" "998"
  assert_contains "$AGENTPIDS" "999"
}

@test "the docker VM is counted as docker, not as an agent" {
  eval "$(fixture mixed.txt | mc_classify)"
  [ "$DOCKER_KB" -gt 9000000 ]
  # T3 gap the final review flagged: this asserted DOCKER_KB only, never that the
  # VM's own pid (70001 in mixed.txt) is excluded from AGENTPIDS. classify.sh's
  # `if (dock[p]) { dtot += R[p]; continue }` makes that structural, but the test
  # should say so.
  assert_not_contains "$AGENTPIDS" "70001"
}

@test "pid lists are quoted so eval cannot execute a pid" {
  run bash -c "$(fixture mixed.txt | mc_classify | head -20); echo ok"
  [ "$status" -eq 0 ]
  assert_contains "$output" "ok"
}

# --- Final review, residual: EXTRA_AGENTS must not be exposed to globbing -----
# `for a in ${EXTRA_AGENTS:-}` word-splits unquoted -- deliberate, it's a
# space-separated list -- but that also exposes it to pathname expansion. A
# glob character in the value (the README asks users to avoid one; nothing
# enforces it) would expand against whatever the current directory happens to
# contain instead of being used literally.
@test "EXTRA_AGENTS containing a glob character is not expanded against the cwd" {
  globdir="$BATS_TEST_TMPDIR/globtest"
  mkdir -p "$globdir"
  touch "$globdir/aa" "$globdir/ab"
  run bash -c "cd '$globdir' && source '$MEMCAP_ROOT/libexec/classify.sh' && EXTRA_AGENTS='a*' mc_extra_agent_pattern"
  # Unexpanded: one alternation branch for the literal "a*". Expanded (the
  # pre-fix bug), the loop would iterate "aa" and "ab" as two separate words.
  assert_contains "$output" "(^|/)a*("
  assert_not_contains "$output" "aa"
  assert_not_contains "$output" "ab"
}
