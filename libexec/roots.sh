#!/usr/bin/env bash
# Sweep roots learn themselves from the working directories of live agent sessions,
# so no user ever has to answer "which directories should I clean?".
set -uo pipefail

mc_roots_file() { printf '%s/roots\n' "$(mc_state_dir)"; }

# Refuse anything broad enough to sweep unrelated work: must be at least two
# levels below $HOME. This gates a destructive operation -- whatever it accepts becomes a
# directory where memcap later kills processes -- so it rejects two escapes that
# satisfy the depth pattern while resolving elsewhere:
#   $HOME/../etc            -> /etc
#   $HOME/x/y/../../../../  -> /
#   a symlink at $HOME/a/b pointing to /etc
mc_root_is_safe() {
  local dir="$1" real
  [ -n "$dir" ] || return 1
  case "$dir" in
    ../*|*/../*|*/..) return 1 ;;
  esac
  # Judge the canonical path when the directory exists, so a symlink cannot smuggle
  # the sweep outside $HOME. Non-existent paths are judged textually.
  if [ -d "$dir" ]; then
    real=$(cd "$dir" 2>/dev/null && pwd -P) || return 1
  else
    real="$dir"
  fi
  case "$real" in
    "$HOME"/*/*) return 0 ;;
    *) return 1 ;;
  esac
}

mc_record_root() {
  local dir="$1" f
  mc_root_is_safe "$dir" || return 0
  f="$(mc_roots_file)"; mkdir -p "$(dirname "$f")"; touch "$f"
  grep -qxF "$dir" "$f" 2>/dev/null || echo "$dir" >> "$f"
}

# Working directory of a pid, via lsof. Empty if it cannot be determined.
mc_pid_cwd() { lsof -a -d cwd -p "$1" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1; }

mc_record_roots() {
  local pid dir
  for pid in $1; do
    dir="$(mc_pid_cwd "$pid")"
    [ -n "$dir" ] && mc_record_root "$dir"
  done
  return 0
}

mc_sweep_roots() { cat "$(mc_roots_file)" 2>/dev/null; }
