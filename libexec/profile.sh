#!/usr/bin/env bash
set -uo pipefail

mc_profile_list() {
  local cap="${TOTAL_BUDGET_GB:-$(mc_cap_gb "$(mc_total_ram_gb)")}" p split desc
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
  local name="$1" cap split docker conf tmp mode
  case "$name" in balanced|stacks|mobile) ;; *) echo "unknown profile: $name" >&2; return 1 ;; esac
  conf="$(mc_config_file)"
  [ -f "$conf" ] || { echo "no config — run 'memcap init' first" >&2; return 1; }
  cap="${TOTAL_BUDGET_GB:-$(mc_cap_gb "$(mc_total_ram_gb)")}"
  split=$(mc_profile_split "$cap" "$name")
  docker=$(echo "$split" | cut -d' ' -f1)
  # Rewrite only the one assignment; every comment and other knob is preserved.
  # A substitution can only change a line that exists. If the key is absent -- an older
  # config, or one where the user commented it out -- sed matches nothing, and without
  # the append branch the command would report success and change no bytes.
  # Colocate the temp file with the config so the mv is atomic on the same filesystem,
  # and clean it up if the rewrite fails rather than leaving a stray file beside it.
  # Fatal, not a fallback default: a stat failure here has no safe guess. 644
  # could WIDEN a config the user deliberately set to 600; some other default
  # could narrow one they set wider. Either way the mode restored below would be
  # a guess, not the file's own mode.
  mode=$(stat -f '%Lp' "$conf" 2>/dev/null) || {
    echo "failed to read the mode of $conf" >&2
    return 1
  }
  if grep -q '^DOCKER_BUDGET_GB=' "$conf"; then
    tmp=$(mktemp "${conf}.XXXXXX") || return 1
    if sed -e "s/^DOCKER_BUDGET_GB=.*/DOCKER_BUDGET_GB=$docker/" "$conf" > "$tmp"; then
      # mktemp creates the file 0600, and `mv` replaces the config's inode along with
      # its permissions. Restore the original mode so switching profiles never silently
      # narrows who can read the config.
      chmod "$mode" "$tmp" 2>/dev/null
      mv "$tmp" "$conf" || { rm -f "$tmp"; return 1; }
    else
      rm -f "$tmp"; return 1
    fi
  else
    # Appending to a file with no trailing newline glues the new key onto the last
    # line: it corrupts that key's value AND never defines this one. Command
    # substitution strips trailing newlines, so a non-empty result here means the
    # final byte was not a newline.
    if [ -n "$(tail -c1 "$conf")" ]; then
      printf '\n' >> "$conf" || return 1
    fi
    # Checked, like the sed branch above it. An unchecked append would report success
    # on a read-only config while writing nothing -- the same false-success bug this
    # whole task has been chasing.
    printf 'DOCKER_BUDGET_GB=%s\n' "$docker" >> "$conf" || return 1
  fi
  echo "profile '$name' → docker ${docker} GB, agents $((cap - docker)) GB"
  echo "Run 'memcap docker apply' to move the VM ceiling (needs a Docker restart)."
  return 0
}
