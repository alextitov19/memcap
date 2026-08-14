#!/usr/bin/env bash
# Sweep roots learn themselves from the working directories of live agent sessions,
# so no user ever has to answer "which directories should I clean?".
set -uo pipefail

mc_roots_file() { printf '%s/roots\n' "$(mc_state_dir)"; }

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

# Refuse anything broad enough to sweep unrelated work: at least two levels below
# $HOME, judged on the canonical path.
mc_root_is_safe() {
  local real
  real=$(mc_canonicalize "$1") || return 1
  case "$real" in
    "$HOME"/*/*) return 0 ;;
    *) return 1 ;;
  esac
}

# Persists the CANONICAL path, not the caller's string: what gets stored must be what
# was validated, or a later sweep acts on a path nobody checked.
mc_record_root() {
  local real f
  real=$(mc_canonicalize "$1") || return 0
  case "$real" in "$HOME"/*/*) : ;; *) return 0 ;; esac
  f="$(mc_roots_file)"; mkdir -p "$(dirname "$f")"; touch "$f"
  grep -qxF "$real" "$f" 2>/dev/null || printf '%s\n' "$real" >> "$f"
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
