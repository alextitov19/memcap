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

@test "the docker VM is counted as docker, not as an agent" {
  eval "$(fixture mixed.txt | mc_classify)"
  [ "$DOCKER_KB" -gt 9000000 ]
}

@test "pid lists are quoted so eval cannot execute a pid" {
  run bash -c "$(fixture mixed.txt | mc_classify | head -20); echo ok"
  [ "$status" -eq 0 ]
  [[ "$output" == *ok* ]]
}
