#!/usr/bin/env bash
set -uo pipefail

mc_profile_list() {
  local cap="${TOTAL_BUDGET_GB:-16}" p split
  echo "  profile    docker  agents  for"
  for p in balanced stacks mobile; do
    split=$(mc_profile_split "$cap" "$p")
    case "$p" in
      balanced) desc="one stack plus a simulator and dev servers" ;;
      stacks)   desc="several container stacks at once" ;;
      mobile)   desc="simulators, emulators, Playwright" ;;
    esac
    printf "  %-10s %-7s %-7s %s\n" "$p" "$(echo "$split" | cut -d' ' -f1) GB" "$(echo "$split" | cut -d' ' -f2) GB" "$desc"
  done
}

mc_profile_set() {
  local name="$1" cap split docker conf tmp
  case "$name" in balanced|stacks|mobile) ;; *) echo "unknown profile: $name" >&2; return 1 ;; esac
  conf="$(mc_config_file)"
  [ -f "$conf" ] || { echo "no config — run 'memcap init' first" >&2; return 1; }
  cap="${TOTAL_BUDGET_GB:-16}"
  split=$(mc_profile_split "$cap" "$name")
  docker=$(echo "$split" | cut -d' ' -f1)
  # Rewrite only the one assignment; every comment and other knob is preserved.
  tmp=$(mktemp)
  sed -e "s/^DOCKER_BUDGET_GB=.*/DOCKER_BUDGET_GB=$docker/" "$conf" > "$tmp" && mv "$tmp" "$conf"
  echo "profile '$name' → docker ${docker} GB, agents $((cap - docker)) GB"
  echo "Run 'memcap docker apply' to move the VM ceiling (needs a Docker restart)."
  return 0
}
