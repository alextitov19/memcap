#!/usr/bin/env bash
# Sweep roots learn themselves from the working directories of live agent sessions,
# so no user ever has to answer "which directories should I clean?".
set -uo pipefail

mc_roots_file() { printf '%s/roots\n' "$(mc_state_dir)"; }

mc_root_is_safe() {
  local dir="$1" real head tail
  [ -n "$dir" ] || return 1
  case "$dir" in
    ../*|*/../*|*/..) return 1 ;;
  esac
  # Trim trailing slashes. "$HOME/code/" otherwise satisfies "$HOME"/*/* with the
  # second * matching the empty string, passing one level off as two.
  while [ "$dir" != "${dir%/}" ]; do dir="${dir%/}"; done
  [ -n "$dir" ] || return 1
  # Only absolute paths can be reasoned about; a relative path also has no ancestor
  # chain to walk, which would spin the loop below forever.
  case "$dir" in /*) : ;; *) return 1 ;; esac
  # Canonicalize against the nearest EXISTING ancestor, then re-append the remainder.
  # Judging a non-existent path textually lets an intermediate symlink escape: with
  # $HOME/scratch -> /tmp, the string "$HOME/scratch/proj" looks safe while actually
  # designating /tmp/proj.
  head="$dir"; tail=""
  while [ ! -d "$head" ] && [ "$head" != "/" ]; do
    tail="${head##*/}${tail:+/$tail}"
    case "$head" in
      */*) head="${head%/*}" ;;
      *)   head="" ;;
    esac
    [ -z "$head" ] && head="/"
  done
  real=$(cd "$head" 2>/dev/null && pwd -P) || return 1
  [ -n "$tail" ] && real="$real/$tail"
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
