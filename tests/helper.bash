setup_common() {
  MEMCAP_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
  export MEMCAP_ROOT
  MEMCAP_CONFIG_HOME="$BATS_TEST_TMPDIR/config"
  MEMCAP_STATE_HOME="$BATS_TEST_TMPDIR/state"
  export MEMCAP_CONFIG_HOME MEMCAP_STATE_HOME
  mkdir -p "$MEMCAP_CONFIG_HOME" "$MEMCAP_STATE_HOME"

  # Sandbox for the LaunchAgent feature (service.sh): this machine has a real,
  # live, enforcing memcap install. `memcap uninstall` already called
  # `brew services stop memcap` for real, unsandboxed, before this existed --
  # every bats run of tests/uninstall.bats was quietly attempting to stop this
  # machine's actual enforcement. A real ~/Library/LaunchAgents write, or a real
  # launchctl/brew invocation from ANY test (not just service.bats), risks
  # unloading or deleting memcap's own plist -- the exact bug this feature
  # exists to fix. Every test gets its own fake LaunchAgent directory and fake
  # launchctl/brew binaries that log their invocation (for tests that want to
  # assert on it) instead of touching the real launchd or Homebrew.
  MEMCAP_LAUNCHAGENT_DIR="$BATS_TEST_TMPDIR/launchagents"
  FAKE_LAUNCHCTL_LOG="$BATS_TEST_TMPDIR/launchctl.calls"
  FAKE_LAUNCHCTL_LIST_OUTPUT="$BATS_TEST_TMPDIR/launchctl.list-output"
  FAKE_BREW_LOG="$BATS_TEST_TMPDIR/brew.calls"
  FAKE_BREW_PREFIX="${FAKE_BREW_PREFIX:-/opt/homebrew}"
  export MEMCAP_LAUNCHAGENT_DIR FAKE_LAUNCHCTL_LOG FAKE_LAUNCHCTL_LIST_OUTPUT FAKE_BREW_LOG FAKE_BREW_PREFIX
  : > "$FAKE_LAUNCHCTL_LOG"
  : > "$FAKE_BREW_LOG"
  : > "$FAKE_LAUNCHCTL_LIST_OUTPUT"

  MC_LAUNCHCTL_BIN="$BATS_TEST_TMPDIR/fake-launchctl"
  cat > "$MC_LAUNCHCTL_BIN" <<'SCRIPT'
#!/bin/sh
echo "$@" >> "$FAKE_LAUNCHCTL_LOG"
case "$1" in
  list) cat "$FAKE_LAUNCHCTL_LIST_OUTPUT" 2>/dev/null ;;
esac
exit 0
SCRIPT
  chmod +x "$MC_LAUNCHCTL_BIN"

  MC_BREW_BIN="$BATS_TEST_TMPDIR/fake-brew"
  cat > "$MC_BREW_BIN" <<'SCRIPT'
#!/bin/sh
echo "$@" >> "$FAKE_BREW_LOG"
case "$1" in
  --prefix) printf '%s\n' "$FAKE_BREW_PREFIX" ;;
esac
exit 0
SCRIPT
  chmod +x "$MC_BREW_BIN"
  export MC_LAUNCHCTL_BIN MC_BREW_BIN
}

# Bash 3.2 -- the shipped macOS /bin/bash, and what `bats` itself runs under
# whenever nothing newer is on PATH -- does not fail a test on a bare `[[ ]]`
# that evaluates false unless it is that test's last command (confirmed
# empirically: `bash -c 'set -e; [[ a == b ]]; echo reached'` prints "reached",
# `[ a = b ]` in the same position does not). `[ ]` and real commands (`grep -q`,
# `false`) are unaffected -- only `[[ ]]` has this quirk. These helpers are
# ordinary function calls, so their failure participates in bash 3.2's error
# handling correctly regardless of position, and they print what was expected
# and what was actually seen so a failure is diagnosable rather than silent.
assert_contains() {
  local haystack="$1" needle="$2"
  case "$haystack" in
    *"$needle"*) return 0 ;;
  esac
  {
    echo "expected to find: $needle"
    echo "in:               $haystack"
  } >&2
  return 1
}

assert_not_contains() {
  local haystack="$1" needle="$2"
  case "$haystack" in
    *"$needle"*)
      {
        echo "expected NOT to find: $needle"
        echo "in:                   $haystack"
      } >&2
      return 1
      ;;
  esac
  return 0
}

# For the handful of assertions that need a regex rather than a literal
# substring (an ERE, matched via grep -E rather than bash's own =~).
assert_matches() {
  local haystack="$1" pattern="$2"
  if printf '%s' "$haystack" | grep -Eq -- "$pattern"; then
    return 0
  fi
  {
    echo "expected to match: $pattern"
    echo "in:                 $haystack"
  } >&2
  return 1
}

# Wait until each spawned fixture process is actually visible to `ps` with its
# own command line, or give up after ~5s and let the test's own assertion report
# the real problem.
#
# Replaces a fixed `sleep 0.2` after `cmd & pid=$!`. That raced: between the
# shell's fork and the exec, `ps -o command=` still shows the forking shell, so
# a fixture that a test needs matched against a sim or dev-server pattern is not
# yet matchable. At 0.2s it was reliable when run alone and intermittently short
# under full-suite load -- `tier3: once the idle stamp is past
# SIM_IDLE_GRACE_SEC the reap proceeds` failed one full-suite run in three while
# passing 6/6 in isolation, because the fixture had not exec'd in time to be
# stamped.
#
# Every fixture in this suite is either `sleep 600` or `perl -e 'sleep 600'
# <marker>`, so both carry the literal "sleep 600" once exec'd -- one predicate
# covers all of them without each call site naming its own marker.
wait_spawned() {
  local pid i
  for pid in "$@"; do
    i=0
    while [ "$i" -lt 250 ]; do
      case "$(ps -o command= -p "$pid" 2>/dev/null)" in
        *"sleep 600"*) break ;;
      esac
      sleep 0.02
      i=$((i + 1))
    done
  done
}
