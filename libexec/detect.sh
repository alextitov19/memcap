#!/usr/bin/env bash
set -uo pipefail

mc_total_ram_gb() { awk -v b="$(sysctl -n hw.memsize)" 'BEGIN{printf "%d", int(b/1024/1024/1024+0.5)}'; }
mc_cpu_count()    { sysctl -n hw.ncpu; }

MC_KNOWN_AGENTS="claude codex cursor-agent aider gemini amp opencode goose crush"

# Agents present on this machine: on PATH, or currently running.
mc_installed_agents() {
  local a found=""
  # Word-splitting EXTRA_AGENTS unquoted is deliberate -- it is a space-separated
  # list -- but leaves it exposed to pathname expansion: a glob character in the
  # value (the README only asks users to avoid it, doesn't enforce it) would
  # expand against whatever the current directory happens to contain. `set -f`
  # disables that expansion without disabling the word-splitting this loop needs.
  set -f
  for a in $MC_KNOWN_AGENTS ${EXTRA_AGENTS:-}; do
    if command -v "$a" >/dev/null 2>&1 || pgrep -qx "$a" 2>/dev/null; then
      found="$found $a"
    fi
  done
  set +f
  echo "${found# }"
}
