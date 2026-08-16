setup_common() {
  MEMCAP_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
  export MEMCAP_ROOT
  MEMCAP_CONFIG_HOME="$BATS_TEST_TMPDIR/config"
  MEMCAP_STATE_HOME="$BATS_TEST_TMPDIR/state"
  export MEMCAP_CONFIG_HOME MEMCAP_STATE_HOME
  mkdir -p "$MEMCAP_CONFIG_HOME" "$MEMCAP_STATE_HOME"
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
