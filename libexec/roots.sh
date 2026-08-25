#!/usr/bin/env bash
# Sweep roots learn themselves from the working directories of live agent sessions,
# so no user ever has to answer "which directories should I clean?".
set -uo pipefail

mc_roots_file() { printf '%s/roots\n' "$(mc_state_dir)"; }

# --- roots file format --------------------------------------------------------
# One row per root, `EPOCH<TAB>PATH`, most-recently-seen FIRST.
#
# Tab is a safe separator precisely because mc_canonicalize rejects any path
# containing a control character, and tab (0x09) is one -- a stored path can
# never contain one, so the split below is unambiguous even for a root with
# spaces in it (`~/dev/foo bar` is a real case tier 1 already handles).
#
# A row with no tab is the older plain-path format -- this machine's live state
# file holds 40 such rows. It is read as a perfectly valid root whose last-seen
# time is simply unknown, and is stamped with `now` the first time the file is
# rewritten. A legacy root therefore gets a FULL TTL from first sight rather
# than being treated as infinitely old and pruned wholesale on the upgrade pass.
MC_ROOTS_SEP=$(printf '\t')

# Splits one row into MC_ROOT_TS / MC_ROOT_PATH (bash 3.2 has no way to return
# two values). MC_ROOT_TS is empty for a legacy row or a corrupt timestamp;
# callers substitute `now`. Returns 1 for a row carrying no path at all.
mc_roots_parse_row() {
  local row="$1"
  case "$row" in
    *"$MC_ROOTS_SEP"*)
      MC_ROOT_TS="${row%%"$MC_ROOTS_SEP"*}"
      MC_ROOT_PATH="${row#*"$MC_ROOTS_SEP"}"
      ;;
    *) MC_ROOT_TS=""; MC_ROOT_PATH="$row" ;;
  esac
  case "$MC_ROOT_TS" in *[!0-9]*) MC_ROOT_TS="" ;; esac
  [ -n "$MC_ROOT_PATH" ]
}

# Canonical absolute path for a directory that may not exist yet. Echoes the resolved
# path; returns 1 for anything that cannot be reasoned about safely.
mc_canonicalize() {
  local dir="$1" head tail real
  [ -n "$dir" ] || return 1
  case "$dir" in
    ../*|*/../*|*/..) return 1 ;;
  esac
  while [ "$dir" != "${dir%/}" ]; do dir="${dir%/}"; done
  [ -n "$dir" ] || return 1
  case "$dir" in /*) : ;; *) return 1 ;; esac
  head="$dir"; tail=""
  while [ ! -d "$head" ] && [ "$head" != "/" ]; do
    tail="${head##*/}${tail:+/$tail}"
    case "$head" in */*) head="${head%/*}" ;; *) head="" ;; esac
    [ -z "$head" ] && head="/"
  done
  real=$(cd "$head" 2>/dev/null && pwd -P) || return 1
  [ "$real" = "/" ] && real=""
  [ -n "$tail" ] && real="$real/$tail"
  [ -n "$real" ] || return 1
  # Guard the FINAL resolved value, not the caller's input. The roots state file is
  # line-oriented, so an embedded newline becomes two roots on read-back and the stray
  # fragment gets substring-matched against process command lines during a sweep. It
  # must be checked here because a symlinked ancestor can introduce a control character
  # the raw input never contained.
  case "$real" in *[[:cntrl:]]*) return 1 ;; esac
  printf '%s\n' "$real"
}

# $HOME itself must be compared in its own canonical form, not the raw value: on
# macOS, /tmp and /var are symlinks (to /private/tmp, /private/var), so a HOME
# that lives under either -- as `mktemp -d` produces, and as CI/sandboxed test
# runs commonly use -- differs from mc_canonicalize's fully-resolved output for
# anything underneath it. Comparing against the raw $HOME then rejects every
# root as "not under HOME" even though it plainly is. Falls back to the raw
# value if $HOME itself cannot be resolved, matching mc_canonicalize's own
# posture of failing closed rather than crashing.
mc_home_real() {
  (cd "$HOME" 2>/dev/null && pwd -P) || printf '%s' "$HOME"
}

# Refuse anything broad enough to sweep unrelated work: at least two levels below
# $HOME, judged on the canonical path.
mc_root_is_safe() {
  local real home_real
  real=$(mc_canonicalize "$1") || return 1
  home_real=$(mc_home_real)
  case "$real" in
    "$home_real"/*/*) return 0 ;;
    *) return 1 ;;
  esac
}

# C1's mc_num lives in config.sh, which bin/memcap sources; roots.sh is also
# sourced standalone (tests, and any caller that only wants path handling), so
# the guard keeps a knob from becoming a hard dependency on load order. The
# fallback applies the same rule mc_num does -- reject anything that is not a
# whole decimal number, never let it reach arithmetic -- just without the
# throttled log line.
mc_roots_num() {
  if command -v mc_num >/dev/null 2>&1; then
    mc_num "$1" "$2" "$3"
    return
  fi
  case "$1" in
    ''|*[!0-9]*) printf '%s' "$2" ;;
    *) printf '%s' "$((10#$1))" ;;
  esac
}

# Pruning policy, and why it is this one.
#
# mc_record_root only ever appended, and nothing anywhere removed a row. Every
# client project a session ever sat in was a permanent row: 40 on this machine,
# growing monotonically. That is not cosmetic -- tier 1 is O(orphans x roots),
# measured at 5,121us per pair, so 388 orphans against 40 roots is 79s of work
# against a 60s service interval.
#
# The policy is last-seen TTL + a recency cap, and the deciding fact is an
# ASYMMETRY: mc_record_roots re-registers the cwd of every live agent session on
# EVERY pass, so a root that is dropped and then genuinely needed again returns
# on its own within one 60s pass of a session opening there. Dropping a stale
# root therefore costs at most one pass of tier-1 coverage in a directory nobody
# is working in; keeping one costs 5.1ms per orphan on every pass forever. With
# the downside that cheap and that self-healing, a short TTL is the right call
# -- 14 days still spans a holiday or a context switch away from a project.
#
# The cap is the backstop for the case TTL cannot bound: someone touching more
# than ROOT_MAX distinct projects inside one TTL window. It drops the
# least-recently-seen rows, which is why the file is kept newest-first.
#
# Rows that no longer canonicalize, or no longer resolve somewhere safe, are
# dropped regardless of age. enforce.sh already skips those on every single pass
# (with a throttled log line), so they can never match anything -- they are pure
# per-orphan cost, and the same re-registration restores them if the directory
# comes back while a session is in it.
MC_ROOT_TTL_DAYS_DEFAULT=14
MC_ROOT_MAX_DEFAULT=64

# Rewrites the roots file, keeping only rows that are still fresh, still valid
# and still within the cap. Newest-first order is preserved, so the cap always
# discards the oldest rows and tier 1 tries the hottest root first.
#
# Called at the END of mc_record_roots, never before it: every live session's
# root is re-stamped with `now` first, so a root that is about to be used cannot
# be aged out by this, and cannot be pushed past the cap either (it is now the
# newest row). Writes via a temp file and rename so a concurrent mc_sweep_roots
# never reads a half-written file.
mc_prune_roots() {
  local f tmp row now ttl max home_real real kept=0
  f="$(mc_roots_file)"
  [ -f "$f" ] || return 0
  now=$(date +%s)
  # `${VAR:-DEFAULT}` as the VALUE, not an empty string: mc_num logs an empty
  # value as "not a whole number", so passing one through for a knob nobody set
  # would put two spurious config warnings in actions.log on every single pass.
  # Same idiom init.sh uses for TOTAL_BUDGET_GB.
  ttl=$(( $(mc_roots_num "${ROOT_TTL_DAYS:-$MC_ROOT_TTL_DAYS_DEFAULT}" "$MC_ROOT_TTL_DAYS_DEFAULT" ROOT_TTL_DAYS) * 86400 ))
  max=$(mc_roots_num "${ROOT_MAX:-$MC_ROOT_MAX_DEFAULT}" "$MC_ROOT_MAX_DEFAULT" ROOT_MAX)
  home_real=$(mc_home_real)
  tmp="$f.$$.tmp"
  : > "$tmp" || return 0
  while IFS= read -r row; do
    mc_roots_parse_row "$row" || continue
    MC_ROOT_TS="${MC_ROOT_TS:-$now}"
    # 10#: a hand-edited or truncated row could carry a leading zero, and bare
    # arithmetic would read that as octal (or fail outright on `08`, taking the
    # whole prune down with it).
    [ $((now - 10#$MC_ROOT_TS)) -ge "$ttl" ] && continue
    [ "$kept" -ge "$max" ] && continue
    # mc_root_is_safe's rule, inlined so $HOME is resolved once for the whole
    # file instead of once per row. Judged on the CANONICAL form only, never on
    # the stored string: on macOS a $HOME under /tmp or /var (what `mktemp -d`
    # produces, and what sandboxed CI runs use) does not textually prefix the
    # fully-resolved paths beneath it, so testing the stored string would prune
    # every root on exactly those machines -- the same trap mc_home_real exists
    # to document.
    #
    # A root that still resolves inside $HOME but no longer to what was recorded
    # is deliberately KEPT: that is the TOCTOU case, and enforce.sh reports it
    # per pass rather than having the evidence silently deleted here.
    real=$(mc_canonicalize "$MC_ROOT_PATH") || continue
    case "$real" in "$home_real"/*/*) : ;; *) continue ;; esac
    printf '%s%s%s\n' "$MC_ROOT_TS" "$MC_ROOTS_SEP" "$MC_ROOT_PATH" >> "$tmp"
    kept=$((kept + 1))
  done < "$f"
  # An empty result is a legitimate outcome (every root aged out), so this
  # cannot gate on `-s`; the file's existence is what says the rewrite ran.
  [ -f "$tmp" ] && mv -f "$tmp" "$f"
  rm -f "$tmp"
  return 0
}

# Persists the CANONICAL path, not the caller's string: what gets stored must be what
# was validated, or a later sweep acts on a path nobody checked.
#
# Re-recording an existing root REFRESHES its timestamp and moves it to the
# front rather than being a no-op, which is what makes the TTL above mean "not
# seen in 14 days" instead of "recorded 14 days ago".
mc_record_root() {
  local real home_real f now tmp row
  real=$(mc_canonicalize "$1") || return 0
  home_real=$(mc_home_real)
  case "$real" in "$home_real"/*/*) : ;; *) return 0 ;; esac
  f="$(mc_roots_file)"; mkdir -p "$(dirname "$f")"; touch "$f"
  now=$(date +%s)
  tmp="$f.$$.tmp"
  {
    printf '%s%s%s\n' "$now" "$MC_ROOTS_SEP" "$real"
    while IFS= read -r row; do
      mc_roots_parse_row "$row" || continue
      [ "$MC_ROOT_PATH" = "$real" ] && continue
      printf '%s%s%s\n' "${MC_ROOT_TS:-$now}" "$MC_ROOTS_SEP" "$MC_ROOT_PATH"
    done < "$f"
  } > "$tmp"
  # The row just written is always present, so unlike the prune above an empty
  # temp file here means the write itself failed -- keep what is on disk.
  [ -s "$tmp" ] && mv -f "$tmp" "$f"
  rm -f "$tmp"
  return 0
}

# Working directory of a pid, via lsof. Empty if it cannot be determined.
mc_pid_cwd() { lsof -a -d cwd -p "$1" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1; }

mc_record_roots() {
  local pid dir
  for pid in $1; do
    dir="$(mc_pid_cwd "$pid")"
    [ -n "$dir" ] && mc_record_root "$dir"
  done
  # After every live root has been refreshed above, never before -- see
  # mc_prune_roots.
  mc_prune_roots
  return 0
}

# Emits the paths only: tier 1 consumes this as one root per line and must not
# have to know about the timestamp column.
mc_sweep_roots() {
  local f row
  f="$(mc_roots_file)"
  [ -f "$f" ] || return 0
  while IFS= read -r row; do
    mc_roots_parse_row "$row" && printf '%s\n' "$MC_ROOT_PATH"
  done < "$f"
  return 0
}
