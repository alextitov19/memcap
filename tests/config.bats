load helper

# Every test here runs against a sandboxed config/state home (setup_common) and,
# for the init tests, a sandboxed HOME as well -- init reads
# ~/.claude/agent-budget.conf, and this machine has a real, live, enforcing
# memcap install whose settings must not leak into a test's expectations.
setup() {
  setup_common
  export MC_DRY_RUN=1
  HOME_SANDBOX="$BATS_TEST_TMPDIR/home"
  mkdir -p "$HOME_SANDBOX"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/config.sh"
}

log_lines() { grep -c "$1" "$(mc_state_dir)/actions.log" 2>/dev/null || echo 0; }

# --- C1: mc_num ---------------------------------------------------------------
# `[` returns status 2 (not 1) when an operand is not an integer, and every
# numeric gate in memcap sits to the LEFT of an `&&`. A knob set to "5m" did not
# make its guard fail -- it made the guard evaporate.

@test "mc_num passes a plain integer through" {
  run mc_num 300 60 TIER2_MIN_AGE_SEC
  [ "$status" -eq 0 ]
  [ "$output" = "300" ]
}

@test "mc_num accepts zero as a real value" {
  run mc_num 0 60 TIER3_REQUIRE_NO_SESSION
  [ "$output" = "0" ]
}

@test "mc_num falls back to the default on a duration string" {
  run mc_num "5m" 300 TIER2_MIN_AGE_SEC
  [ "$status" -eq 0 ]
  [ "$output" = "300" ]
}

@test "mc_num falls back on an empty value" {
  run mc_num "" 600 SIM_IDLE_GRACE_SEC
  [ "$output" = "600" ]
}

@test "mc_num falls back on a negative number" {
  run mc_num "-5" 15 MIN_FREE_PCT
  [ "$output" = "15" ]
}

@test "mc_num falls back on a decimal" {
  run mc_num "2.5" 2 SIM_ACTIVE_CPU_SEC
  [ "$output" = "2" ]
}

@test "mc_num falls back on a value with whitespace in it" {
  run mc_num "300 " 60 TIER2_MIN_AGE_SEC
  [ "$output" = "60" ]
}

# Reproduction: `TOTAL_BUDGET_GB=016` -- a natural way to write an aligned column
# of values -- was read as octal 14, a silently 20% tighter budget.
@test "mc_num strips leading zeros instead of reading them as octal" {
  run mc_num "016" 10 TOTAL_BUDGET_GB
  [ "$output" = "16" ]
}

# Reproduction: `08` is not merely wrong under octal, it is fatal --
# "value too great for base" kills the arithmetic outright.
@test "mc_num accepts 08, which bash arithmetic dies on" {
  run mc_num "08" 10 TOTAL_BUDGET_GB
  [ "$output" = "8" ]
}

@test "mc_num collapses an all-zero value to a single zero" {
  run mc_num "000" 5 SOME_KNOB
  [ "$output" = "0" ]
}

# 19 digits overflows bash's signed 64-bit arithmetic, so a caller doing math on
# it would get a NEGATIVE number out of a value the user wrote as enormous.
@test "mc_num refuses a number long enough to wrap 64-bit arithmetic" {
  run mc_num "99999999999999999999" 300 TIER2_MIN_AGE_SEC
  [ "$output" = "300" ]
}

@test "mc_num accepts 18 digits, the largest width that cannot wrap" {
  run mc_num "999999999999999999" 300 TIER2_MIN_AGE_SEC
  [ "$output" = "999999999999999999" ]
}

@test "mc_num logs the rejected value and names the knob" {
  mc_num "5m" 300 TIER2_MIN_AGE_SEC >/dev/null
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "TIER2_MIN_AGE_SEC is not a whole number ('5m') -- using 300"
}

@test "mc_num throttles a repeated bad knob to one log line" {
  mc_num "5m" 300 TIER2_MIN_AGE_SEC >/dev/null
  mc_num "5m" 300 TIER2_MIN_AGE_SEC >/dev/null
  mc_num "5m" 300 TIER2_MIN_AGE_SEC >/dev/null
  run log_lines "TIER2_MIN_AGE_SEC is not a whole number"
  [ "$output" = "1" ]
}

@test "mc_num throttles each knob independently" {
  mc_num "5m" 300 TIER2_MIN_AGE_SEC >/dev/null
  mc_num "10m" 600 SIM_IDLE_GRACE_SEC >/dev/null
  run log_lines "is not a whole number"
  [ "$output" = "2" ]
}

@test "mc_num never writes a throttle stamp outside the state directory" {
  mc_num "5m" 300 "../../escape" >/dev/null
  [ -f "$(mc_state_dir)/log-throttle/config-other" ]
}

# --- C1 reproductions, as the gates that were erased --------------------------

@test "REPRO: mc_num restores the tier-2 minimum-age gate a duration string erased" {
  # `[ 0 -lt "5m" ]` exits 2, so `[ ... ] && continue` never continues and a
  # zero-second-old process is killed. Through mc_num the gate exists again.
  local age=0 min fired=no
  min=$(mc_num "5m" 300 TIER2_MIN_AGE_SEC)
  if [ "$age" -lt "$min" ]; then fired=yes; fi
  [ "$fired" = "yes" ]
}

@test "REPRO: mc_num restores the simulator idle grace a duration string erased" {
  local idle=0 grace reaped=yes
  grace=$(mc_num "10m" 600 SIM_IDLE_GRACE_SEC)
  if [ "$idle" -lt "$grace" ]; then reaped=no; fi
  [ "$reaped" = "no" ]
}

@test "REPRO: mc_num restores the CPU-activity reset that judged a busy sim idle" {
  local cpu_delta=9 threshold looks_busy=no
  threshold=$(mc_num "two" 2 SIM_ACTIVE_CPU_SEC)
  if [ "$cpu_delta" -ge "$threshold" ]; then looks_busy=yes; fi
  [ "$looks_busy" = "yes" ]
}

# --- mc_frac ------------------------------------------------------------------
# SOFT_TRIGGER is the one ratio knob. It reaches awk, not `[`, so it fails open
# the other way: awk coerces garbage to 0 and `agent > budget*0` fires on every
# pass instead of never.

@test "mc_frac passes a ratio through" {
  run mc_frac "0.80" "0.80" SOFT_TRIGGER
  [ "$output" = "0.80" ]
}

@test "mc_frac accepts a bare integer ratio" {
  run mc_frac "1" "0.80" SOFT_TRIGGER
  [ "$output" = "1" ]
}

@test "mc_frac falls back on a percentage sign" {
  run mc_frac "80%" "0.80" SOFT_TRIGGER
  [ "$output" = "0.80" ]
}

@test "mc_frac falls back on a version-like value with two dots" {
  run mc_frac "0.8.0" "0.80" SOFT_TRIGGER
  [ "$output" = "0.80" ]
}

@test "REPRO: mc_frac stops a garbage SOFT_TRIGGER firing the tier on every pass" {
  local over t
  t=$(mc_frac "high" "0.80" SOFT_TRIGGER)
  over=$(awk -v a=1 -v b=10 -v t="$t" 'BEGIN{print (a > b*t) ? 1 : 0}')
  [ "$over" = "0" ]
}

# --- mc_source_checked --------------------------------------------------------

@test "mc_source_checked sources a file that parses" {
  echo 'MC_TEST_KEY=applied' > "$BATS_TEST_TMPDIR/ok.conf"
  mc_source_checked "$BATS_TEST_TMPDIR/ok.conf"
  [ "$?" -eq 0 ]
  [ "$MC_TEST_KEY" = "applied" ]
}

# THE reproduction: one stray quote. `.` aborts the sourcing but not the shell,
# so keys BEFORE the error apply and keys after it do not.
@test "REPRO: a syntax error applies nothing at all, not the keys above it" {
  cat > "$BATS_TEST_TMPDIR/bad.conf" <<-'EOF'
	TOTAL_BUDGET_GB=99
	DOCKER_BUDGET_GB="6
	TIER2_MIN_AGE_SEC=300
	EOF
  rc=0
  mc_source_checked "$BATS_TEST_TMPDIR/bad.conf" || rc=$?
  [ "$rc" -eq 2 ]
  [ -z "${TOTAL_BUDGET_GB:-}" ]
  [ -z "${TIER2_MIN_AGE_SEC:-}" ]
}

@test "mc_source_checked reports a missing file distinctly" {
  rc=0
  mc_source_checked "$BATS_TEST_TMPDIR/nope.conf" || rc=$?
  [ "$rc" -eq 3 ]
}

# In a child shell, not in-process: bats runs each test under `set -eET`, which
# aborts on the failing command INSIDE the sourced file before mc_source_checked
# can return. bin/memcap sets `set -uo pipefail` and no `-e`, so the child here
# is the real shell and bats's is not.
@test "mc_source_checked reports a runtime failure distinctly" {
  printf 'MC_TEST_KEY=ran\nfalse\n' > "$BATS_TEST_TMPDIR/rt.conf"
  run bash -c "set -uo pipefail
    . '$MEMCAP_ROOT/libexec/common.sh'
    . '$MEMCAP_ROOT/libexec/config.sh'
    rc=0
    mc_source_checked '$BATS_TEST_TMPDIR/rt.conf' || rc=\$?
    echo \"rc=\$rc\""
  assert_contains "$output" "rc=1"
}

# --- C2: mc_load_config -------------------------------------------------------

@test "mc_load_config on a machine with no config is not an error" {
  mc_load_config
  [ "$?" -eq 0 ]
  [ "$MC_CONFIG_BROKEN" = "0" ]
}

@test "mc_load_config applies a good config" {
  mkdir -p "$(mc_config_dir)"
  printf 'TOTAL_BUDGET_GB=16\nDOCKER_BUDGET_GB=6\n' > "$(mc_config_file)"
  mc_load_config
  [ "$MC_CONFIG_BROKEN" = "0" ]
  [ "$TOTAL_BUDGET_GB" = "16" ]
  [ "$DOCKER_BUDGET_GB" = "6" ]
}

# The live reproduction: TOTAL_BUDGET_GB=99 applied, DOCKER_BUDGET_GB never set,
# agent budget 87 GB on a 24 GB machine -- nothing ever over budget again, no
# tier ever firing, and the heartbeat certifying it healthy.
@test "REPRO: one stray quote no longer half-applies the config" {
  mkdir -p "$(mc_config_dir)"
  cat > "$(mc_config_file)" <<-'EOF'
	TOTAL_BUDGET_GB=99
	DOCKER_BUDGET_GB="6
	TIER2_MIN_AGE_SEC=300
	EOF
  mc_load_config || true
  [ "$MC_CONFIG_BROKEN" = "1" ]
  [ -z "${TOTAL_BUDGET_GB:-}" ]
  [ -z "${DOCKER_BUDGET_GB:-}" ]
}

@test "mc_load_config returns non-zero on a broken config" {
  mkdir -p "$(mc_config_dir)"
  echo 'DOCKER_BUDGET_GB="6' > "$(mc_config_file)"
  rc=0
  mc_load_config || rc=$?
  [ "$rc" -eq 1 ]
}

@test "mc_load_config logs a broken config unthrottled, on every pass" {
  mkdir -p "$(mc_config_dir)"
  echo 'DOCKER_BUDGET_GB="6' > "$(mc_config_file)"
  mc_load_config || true
  mc_load_config || true
  mc_load_config || true
  run log_lines "REFUSING to enforce"
  [ "$output" = "3" ]
}

@test "mc_load_config warns on stderr as well as the log" {
  mkdir -p "$(mc_config_dir)"
  echo 'DOCKER_BUDGET_GB="6' > "$(mc_config_file)"
  run mc_load_config
  assert_contains "$output" "syntax error"
  assert_contains "$output" "bash -n"
}

# Driven through a child shell rather than in-process: bats runs each test under
# `set -eET`, which aborts the test the moment the failing command inside the
# sourced file runs. bin/memcap sets `set -uo pipefail` and no `-e`, so the child
# here reproduces the real shell, not bats's.
@test "mc_load_config scrubs keys a runtime failure managed to apply" {
  mkdir -p "$(mc_config_dir)"
  printf 'TOTAL_BUDGET_GB=99\nfalse\n' > "$(mc_config_file)"
  run bash -c "set -uo pipefail
    . '$MEMCAP_ROOT/libexec/common.sh'
    . '$MEMCAP_ROOT/libexec/config.sh'
    mc_load_config
    echo \"broken=\$MC_CONFIG_BROKEN total=\${TOTAL_BUDGET_GB:-unset}\""
  assert_contains "$output" "broken=1"
  assert_contains "$output" "total=unset"
}

@test "mc_load_config treats an unreadable config as broken, not as absent" {
  if [ "$(id -u)" = "0" ]; then skip "root can read anything"; fi
  mkdir -p "$(mc_config_dir)"
  echo 'TOTAL_BUDGET_GB=16' > "$(mc_config_file)"
  chmod 000 "$(mc_config_file)"
  mc_load_config || true
  [ "$MC_CONFIG_BROKEN" = "1" ]
  chmod 644 "$(mc_config_file)"
}

@test "mc_load_config clears a stale broken flag once the config is fixed" {
  MC_CONFIG_BROKEN=1
  mkdir -p "$(mc_config_dir)"
  echo 'TOTAL_BUDGET_GB=16' > "$(mc_config_file)"
  mc_load_config
  [ "$MC_CONFIG_BROKEN" = "0" ]
}

# --- mc_refuse_if_broken ------------------------------------------------------

@test "mc_refuse_if_broken is silent and successful on a good config" {
  MC_CONFIG_BROKEN=0
  run mc_refuse_if_broken "sweep"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "mc_refuse_if_broken names the action and the file" {
  MC_CONFIG_BROKEN=1
  run mc_refuse_if_broken "sweep"
  [ "$status" -eq 1 ]
  assert_contains "$output" "refusing to sweep"
  assert_contains "$output" "$(mc_config_file)"
}

# --- bin/memcap wiring --------------------------------------------------------

@test "bin/memcap applies a good config end to end" {
  mkdir -p "$(mc_config_dir)"
  printf 'TOTAL_BUDGET_GB=20\nDOCKER_BUDGET_GB=6\n' > "$(mc_config_file)"
  run "$MEMCAP_ROOT/bin/memcap" profile
  [ "$status" -eq 0 ]
  # The balanced 8/12 split is reachable only from a cap of 20, so seeing it
  # proves the file's value -- not this machine's computed default -- was used.
  assert_contains "$output" "8 GB"
  assert_contains "$output" "12 GB"
}

@test "bin/memcap refuses to sweep on a broken config" {
  mkdir -p "$(mc_config_dir)"
  printf 'TOTAL_BUDGET_GB=99\nDOCKER_BUDGET_GB="6\n' > "$(mc_config_file)"
  run env MC_DRY_RUN=1 "$MEMCAP_ROOT/bin/memcap" clean
  [ "$status" -eq 3 ]
  assert_contains "$output" "refusing to sweep"
}

@test "bin/memcap refuses to rewrite Docker settings on a broken config" {
  mkdir -p "$(mc_config_dir)"
  printf 'DOCKER_BUDGET_GB="6\n' > "$(mc_config_file)"
  run env MC_DRY_RUN=1 "$MEMCAP_ROOT/bin/memcap" docker apply
  [ "$status" -eq 3 ]
  assert_contains "$output" "refusing to apply Docker settings"
}

@test "bin/memcap refuses to rewrite a broken config with a profile" {
  mkdir -p "$(mc_config_dir)"
  printf 'TOTAL_BUDGET_GB=16\nDOCKER_BUDGET_GB="6\n' > "$(mc_config_file)"
  run "$MEMCAP_ROOT/bin/memcap" profile stacks
  [ "$status" -eq 3 ]
  run cat "$(mc_config_file)"
  assert_contains "$output" 'DOCKER_BUDGET_GB="6'
}

# Pausing must never be blocked by a broken config -- it is the one action that
# can only ever make memcap do less.
@test "bin/memcap off still works on a broken config" {
  mkdir -p "$(mc_config_dir)"
  printf 'DOCKER_BUDGET_GB="6\n' > "$(mc_config_file)"
  run "$MEMCAP_ROOT/bin/memcap" off
  [ "$status" -eq 0 ]
  [ -f "$(mc_state_dir)/paused" ]
}

# --- init: the consent bug ----------------------------------------------------
# mc_config_dir is mkdir -p'd by init; mc_state_dir was not. On a fresh install
# the `touch $(mc_state_dir)/paused` failed silently and memcap enforced despite
# the user answering "no" to "Enforce by killing leaked processes?".

@test "REPRO: answering no on a fresh install actually pauses memcap" {
  rm -rf "$MEMCAP_STATE_HOME"
  [ ! -d "$(mc_state_dir)" ]
  run bash -c "printf '16\n6\nno\n' | env HOME='$HOME_SANDBOX' '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -eq 0 ]
  [ -f "$(mc_state_dir)/paused" ]
}

@test "answering n pauses too -- an unrecognised no is still not consent" {
  rm -rf "$MEMCAP_STATE_HOME"
  run bash -c "printf '16\n6\nn\n' | env HOME='$HOME_SANDBOX' '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -eq 0 ]
  [ -f "$(mc_state_dir)/paused" ]
}

@test "an answer that is neither yes nor no does not enable killing" {
  rm -rf "$MEMCAP_STATE_HOME"
  run bash -c "printf '16\n6\nmaybe\n' | env HOME='$HOME_SANDBOX' '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -eq 0 ]
  [ -f "$(mc_state_dir)/paused" ]
}

@test "answering yes leaves memcap enforcing" {
  rm -rf "$MEMCAP_STATE_HOME"
  run bash -c "printf '16\n6\nyes\n' | env HOME='$HOME_SANDBOX' '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -eq 0 ]
  [ ! -f "$(mc_state_dir)/paused" ]
}

@test "init states which enforcement mode it chose" {
  rm -rf "$MEMCAP_STATE_HOME"
  run bash -c "printf '16\n6\nno\n' | env HOME='$HOME_SANDBOX' '$MEMCAP_ROOT/bin/memcap' init --no-service"
  assert_contains "$output" "Enforcement is OFF"
  rm -rf "$MEMCAP_STATE_HOME"
  run bash -c "printf '16\n6\nyes\n' | env HOME='$HOME_SANDBOX' '$MEMCAP_ROOT/bin/memcap' init --no-service"
  assert_contains "$output" "Enforcement is ON"
}

# Re-running init only to change the Docker ceiling must not resume enforcement
# by pressing Enter past a question the user was not really answering.
@test "init does not silently clear a pause the user set with memcap off" {
  "$MEMCAP_ROOT/bin/memcap" off
  run bash -c "printf '16\n6\nyes\n' | env HOME='$HOME_SANDBOX' '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -eq 0 ]
  [ -f "$(mc_state_dir)/paused" ]
  assert_contains "$output" "stays paused"
}

# If the refusal cannot be recorded, saying so is worth more than finishing init.
@test "init fails loudly when it cannot record a refusal to enforce" {
  if [ "$(id -u)" = "0" ]; then skip "root can write anywhere"; fi
  local locked="$BATS_TEST_TMPDIR/locked"
  mkdir -p "$locked"
  chmod 555 "$locked"
  run bash -c "printf '16\n6\nno\n' | env HOME='$HOME_SANDBOX' MEMCAP_STATE_HOME='$locked/state' '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -ne 0 ]
  assert_contains "$output" "WOULD ENFORCE"
  assert_contains "$output" "memcap off"
  chmod 755 "$locked"
}

# --- init: what it writes -----------------------------------------------------

@test "init writes every knob memcap reads, so users can discover they exist" {
  run bash -c "printf '16\n6\nno\n' | env HOME='$HOME_SANDBOX' '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -eq 0 ]
  run cat "$(mc_config_file)"
  for k in TOTAL_BUDGET_GB DOCKER_BUDGET_GB DOCKER_CPUS SOFT_TRIGGER MIN_FREE_PCT \
           TIER2_MIN_AGE_SEC SIM_IDLE_GRACE_SEC SIM_ACTIVE_CPU_SEC \
           MOBILE_TOOLING_IDLE_SEC TIER3_REQUIRE_NO_SESSION STALE_PASS_SEC \
           LOG_THROTTLE_SEC EXTRA_AGENTS; do
    assert_contains "$output" "$k="
  done
}

@test "the config init writes parses cleanly and loads without breaking" {
  bash -c "printf '16\n6\nno\n' | env HOME='$HOME_SANDBOX' '$MEMCAP_ROOT/bin/memcap' init --no-service" >/dev/null
  run bash -n "$(mc_config_file)"
  [ "$status" -eq 0 ]
  mc_load_config
  [ "$MC_CONFIG_BROKEN" = "0" ]
}

@test "init ignores a prototype config that does not parse" {
  mkdir -p "$HOME_SANDBOX/.claude"
  echo 'TOTAL_BUDGET_GB="6' > "$HOME_SANDBOX/.claude/agent-budget.conf"
  run bash -c "printf '16\n6\nno\n' | env HOME='$HOME_SANDBOX' '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -eq 0 ]
  assert_contains "$output" "does not parse"
  run cat "$(mc_config_file)"
  assert_contains "$output" "TOTAL_BUDGET_GB=16"
}

# An imported value becomes the default the user accepts by pressing Enter, so a
# leading zero there is written straight into memcap.conf with their apparent
# blessing -- and read back as octal on every pass afterwards.
@test "init does not carry a prototype leading zero through as octal" {
  mkdir -p "$HOME_SANDBOX/.claude"
  printf 'TOTAL_BUDGET_GB=016\nDOCKER_BUDGET_GB=6\n' > "$HOME_SANDBOX/.claude/agent-budget.conf"
  run bash -c "printf '\n\nno\n' | env HOME='$HOME_SANDBOX' '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -eq 0 ]
  run cat "$(mc_config_file)"
  assert_contains "$output" "TOTAL_BUDGET_GB=16"
  assert_not_contains "$output" "TOTAL_BUDGET_GB=016"
}

@test "init falls back to a sane cap when the machine's RAM cannot be detected" {
  local fakebin="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$fakebin"
  printf '#!/bin/sh\nexit 1\n' > "$fakebin/sysctl"
  chmod +x "$fakebin/sysctl"
  run bash -c "printf '\n\nno\n' | env HOME='$HOME_SANDBOX' PATH='$fakebin:$PATH' '$MEMCAP_ROOT/bin/memcap' init --no-service"
  [ "$status" -eq 0 ]
  run cat "$(mc_config_file)"
  assert_matches "$output" 'TOTAL_BUDGET_GB=[0-9]+'
  assert_not_contains "$output" "DOCKER_CPUS=0"
}
