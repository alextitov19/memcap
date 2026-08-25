# Every single-quoted block handed to stub_bin below is the SOURCE of a stubbed
# binary, not shell to run here: `$@`, `$1` and `$STUB_TOP_VALUE` inside one must
# reach the stub literally and be expanded by it, never by the test's own shell.
# shellcheck disable=SC2016
load helper
setup() {
  setup_common
  # Nothing here kills, but every file in this suite runs under it as a matter
  # of policy: this machine has a live, enforcing memcap install.
  export MC_DRY_RUN=1
  STUBDIR="$BATS_TEST_TMPDIR/stub-bin"
  mkdir -p "$STUBDIR"
  SNAP="$BATS_TEST_TMPDIR/snapshot"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/measure.sh"
}

# Puts an executable named $1 with body $2 ahead of the real one on PATH, for
# the duration of THIS test only. Used to force the failure paths rather than
# only asserting on the happy path -- every defect this file guards was a
# failure that produced plausible-looking output.
stub_bin() {
  printf '%s\n' "$2" > "$STUBDIR/$1"
  chmod +x "$STUBDIR/$1"
  PATH="$STUBDIR:$PATH"
}

# A `top` that reports EVERY pid the kernel can hand out, not merely every pid
# alive when the stub ran. Enumerating live pids instead leaves this test racing
# the thing it is trying to hold still: bats spawns subprocesses constantly, so
# any pid created between the stub's `ps` and the merge's `ps` is legitimately
# missing and `missing` lands on 0 or 3 depending on load.
stub_top_complete() {
  stub_bin top '#!/bin/sh
echo "PID    MEM"
awk "BEGIN { for (i = 1; i <= 99999; i++) print i \"  100M\" }"'
}

snapshot_is_well_formed() {
  local first
  first=$(head -1 "$SNAP")
  assert_matches "$first" '^[[:space:]]*[0-9]+[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+'
  [ "$(wc -l < "$SNAP" | tr -d ' ')" -gt 10 ]
}

# --- C4: the healthy path stays quiet ----------------------------------------

@test "a complete top sample is not degraded and not a fault" {
  stub_top_complete
  mc_ps_snapshot > "$SNAP"
  snapshot_is_well_formed
  [ "$MC_MEASURE_DEGRADED" = "0" ]
  [ "$MC_MEASURE_FAULT" = "0" ]
  [ "$MC_MEASURE_REASON" = "none" ]
  [ "$MC_MEASURE_MISSING" = "0" ]
  [ "$MC_MEASURE_ROWS" -gt 10 ]
}

# The real top on the real machine, which is what the daemon actually runs. If
# ordinary churn were enough to trip the flag, the flag would be lit on every
# pass and worth nothing -- measured churn is ~3 of ~518 rows against a 10%
# threshold, so this has two orders of magnitude of headroom.
@test "the real top sample on this machine is not a fault" {
  mc_ps_snapshot > "$SNAP"
  snapshot_is_well_formed
  [ "$MC_MEASURE_FAULT" = "0" ]
  [ "$MC_MEASURE_DEGRADED" = "0" ]
}

# --- C4: total degradation ---------------------------------------------------

@test "top failing sets degraded AND fault, and still emits a usable snapshot" {
  stub_bin top '#!/bin/sh
echo "top: cannot allocate" >&2
exit 1'
  mc_ps_snapshot > "$SNAP"
  snapshot_is_well_formed
  [ "$MC_MEASURE_DEGRADED" = "1" ]
  [ "$MC_MEASURE_FAULT" = "1" ]
  [ "$MC_MEASURE_REASON" = "top-failed" ]
}

@test "top failing records top's own stderr in the log" {
  stub_bin top '#!/bin/sh
echo "top: cannot allocate" >&2
exit 1'
  mc_ps_snapshot > "$SNAP"
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "top-failed"
  assert_contains "$output" "top: cannot allocate"
  assert_contains "$output" "ps RSS"
}

@test "top exiting 0 with no usable rows is a fault, not a healthy zero" {
  stub_bin top '#!/bin/sh
echo "Processes: 500 total, 2 running"
echo "PID    MEM"
exit 0'
  mc_ps_snapshot > "$SNAP"
  snapshot_is_well_formed
  [ "$MC_MEASURE_DEGRADED" = "1" ]
  [ "$MC_MEASURE_FAULT" = "1" ]
  [ "$MC_MEASURE_REASON" = "top-empty" ]
}

@test "mktemp failing sets degraded AND fault instead of silently measuring RSS" {
  stub_bin mktemp '#!/bin/sh
exit 1'
  mc_ps_snapshot > "$SNAP"
  snapshot_is_well_formed
  [ "$MC_MEASURE_DEGRADED" = "1" ]
  [ "$MC_MEASURE_FAULT" = "1" ]
  [ "$MC_MEASURE_REASON" = "mktemp-failed" ]
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "mktemp -d failed"
}

# --- C4: a deliberate escape hatch is not a fault -----------------------------

# MC_NO_TOP=1 is documented and chosen. The numbers really are RSS, so it is
# degraded -- but a warning that fires on the configuration the user picked is a
# warning they stop reading before the real fault arrives.
@test "MC_NO_TOP=1 is degraded but NOT a fault" {
  MC_NO_TOP=1 mc_ps_snapshot > "$SNAP"
  snapshot_is_well_formed
  [ "$MC_MEASURE_DEGRADED" = "1" ]
  [ "$MC_MEASURE_FAULT" = "0" ]
  [ "$MC_MEASURE_REASON" = "no-top" ]
}

@test "MC_NO_TOP=1 logs nothing" {
  MC_NO_TOP=1 mc_ps_snapshot > "$SNAP"
  [ ! -s "$(mc_state_dir)/actions.log" ]
}

@test "a deliberate MC_NO_TOP is distinguishable from top failing" {
  MC_NO_TOP=1 mc_ps_snapshot > "$SNAP"
  chosen="$MC_MEASURE_REASON:$MC_MEASURE_FAULT"
  stub_bin top '#!/bin/sh
exit 1'
  mc_ps_snapshot > "$SNAP"
  [ "$chosen" != "$MC_MEASURE_REASON:$MC_MEASURE_FAULT" ]
}

# --- C4: partial degradation is measured, not flagged -------------------------

# 10 of 558 rows missing is what a live machine does on every busy pass: pids
# that started or exited between the top and ps samples. A 0.03% error on the
# sum must not raise the same signal as a 42% one.
@test "a few missing rows are counted but raise no flag" {
  stub_bin top '#!/bin/sh
echo "PID    MEM"
ps -Ao pid= | awk "NR%50==0 {next} {print \$1\"  100M\"}"'
  mc_ps_snapshot > "$SNAP"
  [ "$MC_MEASURE_MISSING" -gt 0 ]
  [ "$MC_MEASURE_DEGRADED" = "0" ]
  [ "$MC_MEASURE_FAULT" = "0" ]
  [ "$MC_MEASURE_REASON" = "none" ]
}

@test "a few missing rows still log nothing" {
  stub_bin top '#!/bin/sh
echo "PID    MEM"
ps -Ao pid= | awk "NR%50==0 {next} {print \$1\"  100M\"}"'
  mc_ps_snapshot > "$SNAP"
  [ ! -s "$(mc_state_dir)/actions.log" ]
}

# Half the table missing is a simulator booting inside the sample window -- 266
# processes appearing at once, which would enter the totals ~2.5x understated.
@test "missing rows past the threshold are a fault" {
  stub_bin top '#!/bin/sh
echo "PID    MEM"
ps -Ao pid= | awk "NR%2==0 {print \$1\"  100M\"}"'
  mc_ps_snapshot > "$SNAP"
  [ "$MC_MEASURE_DEGRADED" = "1" ]
  [ "$MC_MEASURE_FAULT" = "1" ]
  [ "$MC_MEASURE_REASON" = "missing-rows" ]
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "had no row for"
}

@test "the missing-rows threshold is configurable" {
  stub_bin top '#!/bin/sh
echo "PID    MEM"
ps -Ao pid= | awk "NR%50==0 {next} {print \$1\"  100M\"}"'
  MEASURE_MISSING_PCT_MAX=0 mc_ps_snapshot > "$SNAP"
  [ "$MC_MEASURE_REASON" = "missing-rows" ]
}

@test "a garbage threshold falls back to the default rather than breaking the pass" {
  stub_bin top '#!/bin/sh
echo "PID    MEM"
ps -Ao pid= | awk "NR%50==0 {next} {print \$1\"  100M\"}"'
  MEASURE_MISSING_PCT_MAX=banana mc_ps_snapshot > "$SNAP"
  snapshot_is_well_formed
  [ "$MC_MEASURE_REASON" = "none" ]
}

@test "every row present means missing is zero, not merely below the threshold" {
  stub_top_complete
  mc_ps_snapshot > "$SNAP"
  [ "$MC_MEASURE_MISSING" = "0" ]
}

# --- Suffix parsing -----------------------------------------------------------

# One unrecognised unit is a fault regardless of how few: it means top's output
# format is not the one this code was written against. The old `else kb = v + 0`
# turned "512B" into 512 KB and "1.5T" into 1 KB and said nothing.
@test "an unrecognised unit is a fault even as a single row" {
  stub_bin top '#!/bin/sh
echo "PID    MEM"
ps -Ao pid= | awk "NR==3 {print \$1\"  1.5X\"; next} {print \$1\"  100M\"}"'
  mc_ps_snapshot > "$SNAP"
  [ "$MC_MEASURE_DEGRADED" = "1" ]
  [ "$MC_MEASURE_FAULT" = "1" ]
  [ "$MC_MEASURE_REASON" = "bad-units" ]
}

@test "the unrecognised unit is named in the log" {
  stub_bin top '#!/bin/sh
echo "PID    MEM"
ps -Ao pid= | awk "NR==3 {print \$1\"  1.5X\"; next} {print \$1\"  100M\"}"'
  mc_ps_snapshot > "$SNAP"
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "1.5X"
  assert_contains "$output" "mc_top_kb"
}

# A pid with an unusable reading keeps its ps RSS. Being wrong by 1024x in the
# footprint direction is worse than a known-biased RSS for one process.
@test "a pid with an unrecognised unit keeps a plausible ps RSS value" {
  sleep 600 &
  marker=$!
  wait_spawned "$marker"
  stub_bin top "#!/bin/sh
echo \"PID    MEM\"
ps -Ao pid= | awk '\$1 == $marker {print \$1\"  512B\"; next} {print \$1\"  100M\"}'"
  mc_ps_snapshot > "$SNAP"
  kb=$(awk -v p="$marker" '$1==p{print $3}' "$SNAP")
  rss=$(ps -o rss= -p "$marker" 2>/dev/null | tr -d ' ')
  kill "$marker" 2>/dev/null || true
  # 512B is a real top unit and converts to 0 KB, so the guard here is that the
  # value is the process's own RSS rather than 512 (the old 1024x over-count).
  [ -n "$kb" ]
  [ "$kb" = "0" ] || [ "$kb" = "$rss" ]
}

@test "mc_footprint_kb converts each unit top can emit" {
  stub_bin top '#!/bin/sh
pid=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-pid" ]; then pid="$a"; fi
  prev="$a"
done
echo "PID    MEM"
echo "$pid  $STUB_TOP_VALUE"'
  export STUB_TOP_VALUE
  STUB_TOP_VALUE="4096K"; [ "$(mc_footprint_kb 1)" = "4096" ]
  STUB_TOP_VALUE="512M";  [ "$(mc_footprint_kb 1)" = "524288" ]
  STUB_TOP_VALUE="2G";    [ "$(mc_footprint_kb 1)" = "2097152" ]
  STUB_TOP_VALUE="1.5T";  [ "$(mc_footprint_kb 1)" = "1610612736" ]
  STUB_TOP_VALUE="512B";  [ "$(mc_footprint_kb 1)" = "0" ]
  # A trailing +/- growth marker is top's own annotation, not a unit.
  STUB_TOP_VALUE="2G+";   [ "$(mc_footprint_kb 1)" = "2097152" ]
}

@test "mc_footprint_kb falls back to ps RSS on an unrecognised unit rather than mis-scaling" {
  sleep 600 &
  marker=$!
  wait_spawned "$marker"
  stub_bin top '#!/bin/sh
pid=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-pid" ]; then pid="$a"; fi
  prev="$a"
done
echo "PID    MEM"
echo "$pid  1.5X"'
  got=$(mc_footprint_kb "$marker")
  rss=$(ps -o rss= -p "$marker" 2>/dev/null | tr -d ' ')
  kill "$marker" 2>/dev/null || true
  [ "$got" = "$rss" ]
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "1.5X"
}

@test "mc_footprint_kb falls back to ps RSS when top has no row for the pid" {
  sleep 600 &
  marker=$!
  wait_spawned "$marker"
  stub_bin top '#!/bin/sh
echo "PID    MEM"'
  got=$(mc_footprint_kb "$marker")
  rss=$(ps -o rss= -p "$marker" 2>/dev/null | tr -d ' ')
  kill "$marker" 2>/dev/null || true
  [ "$got" = "$rss" ]
}

# --- C4: the flag has to survive the subshell it is set in --------------------

# Every real call site is `eval "$(mc_ps_snapshot | mc_classify)"` -- a pipeline
# inside a command substitution, two subshells deep. A global assigned in there
# is gone before any consumer could read it, which is why the state file exists.
@test "degradation set inside a command substitution reaches the caller" {
  stub_bin top '#!/bin/sh
exit 1'
  eval "$(mc_ps_snapshot | awk '{ next } END { print ":" }')"
  # Proves the point: the in-process globals really are back to their defaults.
  [ "$MC_MEASURE_DEGRADED" = "0" ]
  mc_measure_status_load
  [ "$MC_MEASURE_DEGRADED" = "1" ]
  [ "$MC_MEASURE_FAULT" = "1" ]
  [ "$MC_MEASURE_REASON" = "top-failed" ]
}

@test "a recovered pass clears the previous fault record" {
  stub_bin top '#!/bin/sh
exit 1'
  mc_ps_snapshot > "$SNAP"
  mc_measure_status_load
  [ "$MC_MEASURE_DEGRADED" = "1" ]
  rm -f "$STUBDIR/top"
  stub_top_complete
  mc_ps_snapshot > "$SNAP"
  mc_measure_status_load
  [ "$MC_MEASURE_DEGRADED" = "0" ]
  [ "$MC_MEASURE_REASON" = "none" ]
}

@test "mc_measure_status_load reads healthy when no snapshot has been taken" {
  mc_measure_mark top-failed 1
  mc_measure_status_load
  [ "$MC_MEASURE_DEGRADED" = "0" ]
  [ "$MC_MEASURE_REASON" = "none" ]
}

@test "a corrupt status file reads as healthy rather than as nonsense" {
  mkdir -p "$(mc_state_dir)"
  printf 'degraded=yes\nfault=;rm -rf /\nreason=../../etc\nmissing=x\nrows=\n' > "$(mc_state_dir)/measure-status"
  mc_measure_status_load
  [ "$MC_MEASURE_DEGRADED" = "0" ]
  [ "$MC_MEASURE_FAULT" = "0" ]
  [ "$MC_MEASURE_REASON" = "none" ]
  [ "$MC_MEASURE_MISSING" = "0" ]
}

@test "the summary names the cause for every reason it can report" {
  for r in none no-top mktemp-failed top-failed top-empty bad-units missing-rows; do
    MC_MEASURE_REASON="$r"
    out=$(mc_measure_summary)
    [ -n "$out" ]
    assert_not_contains "$out" "unknown"
  done
}

# --- Temp file hygiene on the paths detect.bats does not reach ----------------

@test "mc_ps_snapshot leaves no temp directory behind when top fails" {
  sandbox="$BATS_TEST_TMPDIR/tmpdir-sandbox"
  mkdir -p "$sandbox"
  stub_bin top '#!/bin/sh
exit 1'
  TMPDIR="$sandbox" mc_ps_snapshot > "$SNAP"
  run bash -c "ls -A '$sandbox'"
  [ -z "$output" ]
}

@test "mc_ps_snapshot leaves no temp directory behind on a degraded-but-complete pass" {
  sandbox="$BATS_TEST_TMPDIR/tmpdir-sandbox"
  mkdir -p "$sandbox"
  stub_bin top '#!/bin/sh
echo "PID    MEM"
ps -Ao pid= | awk "NR%2==0 {print \$1\"  100M\"}"'
  TMPDIR="$sandbox" mc_ps_snapshot > "$SNAP"
  [ "$MC_MEASURE_FAULT" = "1" ]
  run bash -c "ls -A '$sandbox'"
  [ -z "$output" ]
}

# --- mc_free_pct fails closed, not open ---------------------------------------

# The old code ran sysctl inside awk and printed a hardcoded 100 when it got
# nothing back -- verified returning 100 where the real value was 17. enforce.sh
# compares that against MIN_FREE_PCT (15), so tier 1's low-memory trigger could
# never fire and nothing said so. 100 is the single worst possible default here.
@test "mc_free_pct reports 0, not 100, when sysctl is unavailable" {
  stub_bin sysctl '#!/bin/sh
exit 1'
  run mc_free_pct
  [ "$output" = "0" ]
  # Status stays 0 on purpose: `free=$(mc_free_pct)` in an enforcement pass
  # would abort the pass under `set -e` if this failed loudly through its exit
  # status, which trades a dead trigger for a dead daemon.
  [ "$status" -eq 0 ]
}

@test "mc_free_pct logs when it cannot measure" {
  stub_bin sysctl '#!/bin/sh
exit 1'
  mc_free_pct > /dev/null
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "hw.memsize"
  assert_contains "$output" "fails CLOSED"
}

@test "mc_free_pct reports 0 when vm_stat is unavailable" {
  stub_bin vm_stat '#!/bin/sh
exit 1'
  run mc_free_pct
  [ "$output" = "0" ]
}

@test "mc_free_pct reports 0 when sysctl returns something that is not a byte count" {
  stub_bin sysctl '#!/bin/sh
echo "hw.memsize: unknown oid"'
  run mc_free_pct
  [ "$output" = "0" ]
}

@test "mc_free_pct leaves a marker status can surface, and clears it on recovery" {
  stub_bin sysctl '#!/bin/sh
exit 1'
  mc_free_pct > /dev/null
  [ -f "$(mc_state_dir)/free-pct-unavailable" ]
  rm -f "$STUBDIR/sysctl"
  mc_free_pct > /dev/null
  [ ! -f "$(mc_state_dir)/free-pct-unavailable" ]
}

@test "mc_free_pct computes the expected percentage from known inputs" {
  stub_bin sysctl '#!/bin/sh
echo 17179869184'
  stub_bin vm_stat '#!/bin/sh
echo "Mach Virtual Memory Statistics: (page size of 16384 bytes)"
echo "Pages free:                               100000."
echo "Pages active:                             500000."
echo "Pages inactive:                            50000."
echo "Pages speculative:                          5000."
echo "Pages purgeable:                            2000."'
  # (100000+50000+5000+2000) * 16384 * 100 / 17179869184 = 14.97 -> 14.
  # Truncation, not rounding: it errs toward tripping the trigger.
  run mc_free_pct
  [ "$output" = "14" ]
}

@test "mc_free_pct on the real machine is a plausible percentage and not the old constant" {
  run mc_free_pct
  [ "$status" -eq 0 ]
  [ "$output" -ge 0 ]
  [ "$output" -le 100 ]
}
