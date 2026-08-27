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

# --- C5: status renders the OUTCOME of the last pass, not just its existence --
#
# The audit's central finding. A misconfigured budget makes `mc_watch` print a
# refusal to stdout -- which nothing reads under launchd -- stamp the heartbeat
# and return without enforcing; `status` then said "last enforcement pass 1s
# ago" with no warning at all. Permanently not enforcing, and it looked perfect.
# mc_watch writes one word to $(mc_state_dir)/last-outcome next to the
# heartbeat; these tests write that word directly rather than arranging a real
# pass for each of the six.

fresh_pass_with_outcome() {
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  echo "$(( $(date +%s) - 5 ))" > "$MEMCAP_STATE_HOME/memcap/last-pass"
  [ $# -eq 0 ] || printf '%s\n' "$1" > "$MEMCAP_STATE_HOME/memcap/last-outcome"
}

@test "an enforced outcome renders quietly" {
  fresh_pass_with_outcome enforced
  run "$MEMCAP_ROOT/bin/memcap" status
  [ "$status" -eq 0 ]
  assert_contains "$output" "last pass outcome"
  assert_contains "$output" "enforced"
  assert_not_contains "$output" "DID NOT ENFORCE"
  assert_not_contains "$output" "RECORDED NO OUTCOME"
}

@test "a refused-misconfig outcome is loud even with a fresh heartbeat" {
  fresh_pass_with_outcome refused-misconfig
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "THE LAST PASS DID NOT ENFORCE"
  assert_contains "$output" "DOCKER_BUDGET_GB"
  assert_not_contains "$output" "PROBABLY NOT RUNNING"
}

@test "a refused-badconfig outcome names the file and how to check it" {
  fresh_pass_with_outcome refused-badconfig
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "THE LAST PASS DID NOT ENFORCE"
  assert_contains "$output" "bash -n"
  assert_contains "$output" "memcap.conf"
}

@test "a dry-run outcome says nothing is being killed" {
  fresh_pass_with_outcome dry-run
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "DRY-RUN MODE"
}

@test "a degraded-measurement outcome explains why nothing was killed" {
  fresh_pass_with_outcome degraded-measurement
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "THE LAST PASS DID NOT ENFORCE"
  assert_contains "$output" "ps RSS"
}

# A word this version does not know must not be read as success. Fail closed:
# the whole class of defect here was something downstream treating an
# unrecognised state as the healthy one.
@test "an unrecognised outcome is treated as unenforced" {
  fresh_pass_with_outcome banana
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "DOES NOT KNOW"
  assert_contains "$output" "banana"
}

@test "a pass that recorded no outcome does not read as enforced" {
  fresh_pass_with_outcome
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "RECORDED NO OUTCOME"
  assert_not_contains "$output" "outcome                enforced"
}

# On a fresh install the heartbeat row already says memcap has never run; a
# second line saying the same thing in jargon is noise, and noise is what makes
# the real lines ignorable.
@test "a missing outcome is not shouted about before the first pass" {
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "NOT RUN SINCE INSTALL"
  assert_not_contains "$output" "RECORDED NO OUTCOME"
}

# --- The agents line needs the same 0-budget guard as the Docker line ---------
# status.sh carefully avoids rendering "6.41 GB / 0 GB ceiling" for Docker, and
# then rendered exactly that nonsense on the line directly above it.

@test "an unsatisfiable agent budget renders as such, not as 0 GB" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  cat > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" <<-'EOF'
	TOTAL_BUDGET_GB=8
	DOCKER_BUDGET_GB=8
	EOF
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_not_contains "$output" "/ 0 GB budget"
  assert_contains "$output" "NO BUDGET LEFT"
  assert_contains "$output" "MEMCAP IS NOT ENFORCING"
}

@test "a negative agent budget renders as unsatisfiable too" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  cat > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" <<-'EOF'
	TOTAL_BUDGET_GB=8
	DOCKER_BUDGET_GB=12
	EOF
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_not_contains "$output" "-4 GB budget"
  assert_contains "$output" "NO BUDGET LEFT"
}

# The unmanaged Docker ceiling is not the same as an excluded one: that same
# Docker figure is still summed into `combined` two rows below.
@test "an unmanaged Docker ceiling says it is still counted in combined" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  cat > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" <<-'EOF'
	TOTAL_BUDGET_GB=16
	DOCKER_BUDGET_GB=0
	EOF
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "still counted in combined"
}

# --- A config that does not parse ---------------------------------------------
# One stray quote applied every key before it and none after: an 87 GB agent
# budget on a 24 GB machine, no tier able to fire again, `watch` exiting 0 and
# `status` green. mc_load_config refuses to apply it now; this is where a person
# finds out.

@test "status says so when the config does not parse" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  cat > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" <<-'EOF'
	TOTAL_BUDGET_GB=99
	DOCKER_BUDGET_GB="6
	EOF
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "MEMCAP IS NOT ENFORCING"
  assert_contains "$output" "does not parse"
  assert_contains "$output" "bash -n"
}

# --- The heartbeat proves a pass happened, not that the SERVICE ran one -------
# `status` never consulted launchctl; only `memcap service status` did, and
# nobody runs it. Any manual `memcap watch` stamps the same heartbeat file, so a
# fresh stamp certified an uninstalled LaunchAgent as healthy.

@test "status reports an uninstalled LaunchAgent even with a fresh heartbeat" {
  fresh_pass_with_outcome enforced
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "no LaunchAgent installed"
  assert_contains "$output" "LAUNCHAGENT IS NOT INSTALLED"
  # Distinct from "no pass has happened recently" -- the remedies differ.
  assert_not_contains "$output" "PROBABLY NOT RUNNING"
}

@test "status distinguishes an installed-but-unloaded LaunchAgent" {
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/service.sh"
  mkdir -p "$(mc_launchagent_dir)"
  : > "$(mc_launchagent_plist)"
  # The fake launchctl lists whatever this file holds; empty means not loaded.
  : > "$FAKE_LAUNCHCTL_LIST_OUTPUT"
  fresh_pass_with_outcome enforced
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "LaunchAgent NOT LOADED"
  assert_contains "$output" "LAUNCHAGENT IS NOT LOADED"
  assert_not_contains "$output" "NOT INSTALLED"
}

@test "a loaded LaunchAgent renders quietly" {
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/service.sh"
  mkdir -p "$(mc_launchagent_dir)"
  : > "$(mc_launchagent_plist)"
  printf '0\t0\t%s\n' "$(mc_launchagent_label)" > "$FAKE_LAUNCHCTL_LIST_OUTPUT"
  fresh_pass_with_outcome enforced
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "LaunchAgent loaded"
  assert_not_contains "$output" "NOT LOADED"
  assert_not_contains "$output" "NOT INSTALLED"
}

# Permission-independent reasoning, the mc_pid_alive lesson: "could not be
# asked" must never be rendered as "no". An unreachable launchctl is not
# evidence that the agent is unloaded.
@test "status says the LaunchAgent state is unknown when launchctl cannot be asked" {
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/service.sh"
  mkdir -p "$(mc_launchagent_dir)"
  : > "$(mc_launchagent_plist)"
  MC_LAUNCHCTL_BIN="$BATS_TEST_TMPDIR/no-such-launchctl"
  export MC_LAUNCHCTL_BIN
  fresh_pass_with_outcome enforced
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "launchctl could not be asked"
  assert_not_contains "$output" "LAUNCHAGENT IS NOT LOADED"
}

# --- C4: a measurement fault must not render as a healthy report -------------
# A `top` failure silently drops combined from 12.60 GB to 7.28 GB -- a 42%
# under-measurement, previously with no trace anywhere.

@test "status shouts when the memory measurement fell back to ps RSS unasked" {
  local stub="$BATS_TEST_TMPDIR/stub"
  mkdir -p "$stub"
  printf '#!/bin/sh\nexit 1\n' > "$stub/top"
  chmod +x "$stub/top"
  PATH="$stub:$PATH" run "$MEMCAP_ROOT/bin/memcap" status
  [ "$status" -eq 0 ]
  assert_contains "$output" "MEASUREMENT IS FAULTY"
  assert_contains "$output" "ps RSS"
}

# MC_NO_TOP=1 sets DEGRADED without FAULT: it is a documented escape hatch, and
# a warning that fires on a configuration someone deliberately chose is one they
# learn to ignore before the real fault ever arrives.
@test "a deliberate MC_NO_TOP is reported without being called a fault" {
  MC_NO_TOP=1 run "$MEMCAP_ROOT/bin/memcap" status
  [ "$status" -eq 0 ]
  assert_contains "$output" "MC_NO_TOP=1 is set"
  assert_not_contains "$output" "MEASUREMENT IS FAULTY"
}

# mc_free_pct used to return a hardcoded 100 when it could not measure, which
# made tier 1's low-memory trigger permanently unreachable. It returns 0 and
# drops a marker now -- worth nothing unless something surfaces it.
@test "status surfaces an unmeasurable free-memory reading" {
  local stub="$BATS_TEST_TMPDIR/stub"
  mkdir -p "$stub"
  printf '#!/bin/sh\nexit 1\n' > "$stub/vm_stat"
  chmod +x "$stub/vm_stat"
  PATH="$stub:$PATH" run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "UNMEASURABLE"
  assert_contains "$output" "SYSTEM MEMORY CANNOT BE MEASURED"
  assert_not_contains "$output" "available                        100%"
}

# --- Staleness is judged in awake seconds ------------------------------------
# STALE_PASS_SEC=300 was not sleep-aware: after a lid-close `status` said
# MEMCAP IS PROBABLY NOT RUNNING about a service that self-heals within ~60s of
# wake. False alarms are how a real one gets ignored.

@test "a heartbeat from before a long sleep does not read as a dead daemon" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap" "$MEMCAP_STATE_HOME/memcap"
  echo "STALE_PASS_SEC=300" >> "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  echo "$(( $(date +%s) - 28800 ))" > "$MEMCAP_STATE_HOME/memcap/last-pass"
  # 8h of wall clock, 30s of it awake: the machine was asleep for the rest.
  echo "99970" > "$MEMCAP_STATE_HOME/memcap/last-pass-awake"
  MC_AWAKE_SECS=100000 run "$MEMCAP_ROOT/bin/memcap" status
  assert_not_contains "$output" "PROBABLY NOT RUNNING"
  assert_contains "$output" "8h ago"
  assert_contains "$output" "the machine slept for the rest"
}

@test "a daemon that really stopped still reads as stopped on the awake clock" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap" "$MEMCAP_STATE_HOME/memcap"
  echo "STALE_PASS_SEC=300" >> "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  echo "$(( $(date +%s) - 28800 ))" > "$MEMCAP_STATE_HOME/memcap/last-pass"
  # 8h of wall clock and 8h of it awake: nothing ran, and the machine was up.
  echo "71200" > "$MEMCAP_STATE_HOME/memcap/last-pass-awake"
  MC_AWAKE_SECS=100000 run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "PROBABLY NOT RUNNING"
}

# The awake clock restarts at zero on boot, so a stamp above the current reading
# belongs to a previous boot and says nothing about this one.
@test "an awake stamp from a previous boot falls back to the wall clock" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap" "$MEMCAP_STATE_HOME/memcap"
  echo "STALE_PASS_SEC=300" >> "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  echo "$(( $(date +%s) - 28800 ))" > "$MEMCAP_STATE_HOME/memcap/last-pass"
  echo "999999" > "$MEMCAP_STATE_HOME/memcap/last-pass-awake"
  MC_AWAKE_SECS=100 MC_LAST_WAKE=1 run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "PROBABLY NOT RUNNING"
}

# A heartbeat written by an older memcap has no awake stamp beside it. The
# coarse fallback: a pass that predates a RECENT wake has not had its chance to
# run yet.
@test "without an awake stamp, a recent wake explains a stale heartbeat" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap" "$MEMCAP_STATE_HOME/memcap"
  echo "STALE_PASS_SEC=300" >> "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  echo "$(( $(date +%s) - 7200 ))" > "$MEMCAP_STATE_HOME/memcap/last-pass"
  MC_LAST_WAKE=$(( $(date +%s) - 10 )) run "$MEMCAP_ROOT/bin/memcap" status
  assert_not_contains "$output" "PROBABLY NOT RUNNING"
  assert_contains "$output" "the machine woke"
}

@test "a pass that stopped AFTER the last wake still raises the alarm" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap" "$MEMCAP_STATE_HOME/memcap"
  echo "STALE_PASS_SEC=300" >> "$MEMCAP_CONFIG_HOME/memcap/memcap.conf"
  echo "$(( $(date +%s) - 3600 ))" > "$MEMCAP_STATE_HOME/memcap/last-pass"
  MC_LAST_WAKE=$(( $(date +%s) - 7200 )) run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "PROBABLY NOT RUNNING"
}

# --- A pause is exactly as unenforced as a crash -----------------------------

@test "status says how long enforcement has been paused" {
  mkdir -p "$MEMCAP_STATE_HOME/memcap"
  echo "$(( $(date +%s) - 5 ))" > "$MEMCAP_STATE_HOME/memcap/last-pass"
  echo "$(( $(date +%s) - 7200 ))" > "$MEMCAP_STATE_HOME/memcap/paused"
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "ENFORCEMENT PAUSED for 2h"
}

# --- set -u must not be defeated by the command substitutions ----------------
# Every value in this file is read inside `$( )`, where an unbound variable
# kills the subshell and nothing else: `status` printed a plausible report with
# blank fields and exited 0.

@test "status refuses to print a report with blank numbers in it" {
  # shellcheck source=/dev/null
  for m in common config budget detect measure classify status; do
    source "$MEMCAP_ROOT/libexec/$m.sh"
  done
  # A classifier that emits nothing: `eval ""` leaves AGENT_KB and friends unset,
  # which is exactly the state that used to render as a blank-field report.
  mc_classify() { cat >/dev/null; }
  run mc_render_status
  [ "$status" -ne 0 ]
  assert_contains "$output" "refusing to print a report"
  assert_not_contains "$output" "GB budget"
}

# Two loud lines describing one broken setting is how a report teaches people to
# skim past the loud lines. When `status` can see the fault in the config in
# front of it, the outcome word does not repeat it.
@test "a fault status can see for itself is not reported twice" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap" "$MEMCAP_STATE_HOME/memcap"
  cat > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" <<-'EOF'
	TOTAL_BUDGET_GB=8
	DOCKER_BUDGET_GB=8
	EOF
  fresh_pass_with_outcome refused-misconfig
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "MEMCAP IS NOT ENFORCING"
  assert_contains "$output" "REFUSED -- budget is unsatisfiable"
  assert_not_contains "$output" "THE LAST PASS DID NOT ENFORCE"
}

# But a fault that has since been FIXED still has to be reported: the last pass
# really did refuse, and the next one has not happened yet.
@test "a refusal that has since been fixed is still reported" {
  fresh_pass_with_outcome refused-badconfig
  run "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "THE LAST PASS DID NOT ENFORCE"
}

# --- The Docker ceiling row --------------------------------------------------
@test "status says which Docker ceiling is actually being enforced" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  cat > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" <<-'EOF'
	TOTAL_BUDGET_GB=16
	DOCKER_BUDGET_GB=4
	EOF
  # Docker holding a 6 GB ceiling against a config that asks for 4 is the state
  # the author's machine was in for memcap's entire life there. The agent budget
  # on the row above is computed as 16 - 4, so the row cannot keep presenting 4
  # as the ceiling while Docker enforces something else.
  run env MC_DOCKER_CEILING_MIB=6144 "$MEMCAP_ROOT/bin/memcap" status
  [ "$status" -eq 0 ]
  assert_contains "$output" "6 GB ceiling ENFORCED (config asks for 4 GB)"
  assert_contains "$output" "not the 4 GB in your config"
  assert_contains "$output" "memcap docker apply"
}

@test "status leaves the Docker row alone when the ceiling matches" {
  mkdir -p "$MEMCAP_CONFIG_HOME/memcap"
  cat > "$MEMCAP_CONFIG_HOME/memcap/memcap.conf" <<-'EOF'
	TOTAL_BUDGET_GB=16
	DOCKER_BUDGET_GB=6
	EOF
  run env MC_DOCKER_CEILING_MIB=6144 "$MEMCAP_ROOT/bin/memcap" status
  assert_contains "$output" "6 GB ceiling"
  assert_not_contains "$output" "ENFORCED"
  assert_not_contains "$output" "memcap docker apply"
}

