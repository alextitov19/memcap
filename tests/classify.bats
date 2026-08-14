load helper

setup() { setup_common;
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/classify.sh"; }

fixture() { cat "$MEMCAP_ROOT/tests/fixtures/$1"; }

@test "orphaned dev servers are counted as orphans" {
  eval "$(fixture orphan-storm.txt | mc_classify)"
  [[ "$ORPHANS" == *8506* ]]
  [[ "$ORPHANS" == *8507* ]]
}

@test "an esbuild child with a live parent is NOT an orphan" {
  eval "$(fixture orphan-storm.txt | mc_classify)"
  [[ "$ORPHANS" != *8524* ]]
}

@test "the agent process itself is found and is never an orphan" {
  eval "$(fixture orphan-storm.txt | mc_classify)"
  [[ "$AGENTPIDS" == *91633* ]]
  [[ "$ORPHANS" != *91633* ]]
}

@test "REGRESSION: 'rg ms-playwright' is not a simulator" {
  eval "$(fixture mixed.txt | mc_classify)"
  [[ "$SIMPIDS" != *12589* ]]
}

@test "REGRESSION: a real playwright browser IS a simulator" {
  eval "$(fixture mixed.txt | mc_classify)"
  [[ "$SIMPIDS" == *12600* ]]
}

@test "REGRESSION: Maestro's JVM IS a simulator" {
  eval "$(fixture mixed.txt | mc_classify)"
  [[ "$SIMPIDS" == *91650* ]]
}

@test "REGRESSION: grepping for a maestro/playwright marker is not itself a simulator (MC_SIM_SKIP)" {
  # Without MC_SIM_SKIP excluding /grep as an exe, this line's argument
  # ("maestro.cli") matches MC_SIM_ARG and the process would land in SIMPIDS --
  # tier 3's kill-candidate list -- for having merely searched for the string.
  eval "$(printf '%s\n' '12700 91633 4976 /opt/homebrew/bin/grep -r maestro.cli /Users/x/proj' | mc_classify)"
  [[ "$SIMPIDS" != *12700* ]]
}

@test "EXTRA_AGENTS from the config extends the agent list" {
  # shellcheck disable=SC2034  # consumed by mc_classify via awk -v
  EXTRA_AGENTS="mycustomagent"
  eval "$(printf '%s\n' '  999     1  50000 /usr/local/bin/mycustomagent --run' | mc_classify)"
  [[ "$AGENTPIDS" == *999* ]]
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
  [[ "$AGENTPIDS" == *998* ]]
  [[ "$AGENTPIDS" == *999* ]]
}

@test "the docker VM is counted as docker, not as an agent" {
  eval "$(fixture mixed.txt | mc_classify)"
  [ "$DOCKER_KB" -gt 9000000 ]
}

@test "pid lists are quoted so eval cannot execute a pid" {
  run bash -c "$(fixture mixed.txt | mc_classify | head -20); echo ok"
  [ "$status" -eq 0 ]
  [[ "$output" == *ok* ]]
}
