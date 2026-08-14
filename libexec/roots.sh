#!/usr/bin/env bash
# Sweep roots learn themselves from the working directories of live agent sessions,
# so no user ever has to answer "which directories should I clean?".
set -uo pipefail

mc_roots_file() { printf '%s/roots\n' "$(mc_state_dir)"; }

# Refuse anything broad enough to sweep unrelated work: must be at least two
# levels below $HOME.
mc_root_is_safe() {
  case "$1" in
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
