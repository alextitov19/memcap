#!/usr/bin/env bash
# memcap's own LaunchAgent -- install, uninstall, status, and migration away
# from Homebrew's copy of the same job.
#
# v0.1.x had Homebrew write and own this plist (`service do` in the formula).
# `brew upgrade` turned out to REMOVE it, not just unload it -- confirmed twice,
# including a 28-hour outage on the author's own machine with no paused marker,
# no crash evidence, and a plist that was simply gone. A tool cannot be
# install-and-forget if routine maintenance on the package manager silently
# stops it and it never comes back at login either, because the plist that
# would have loaded it no longer exists. Homebrew only manages plists it
# created; a plist memcap writes and owns is one `brew upgrade` cannot delete.
set -uo pipefail

MC_LAUNCHAGENT_LABEL="com.alextitov19.memcap"

mc_launchagent_dir() { printf '%s' "${MEMCAP_LAUNCHAGENT_DIR:-$HOME/Library/LaunchAgents}"; }
mc_launchagent_label() { printf '%s' "$MC_LAUNCHAGENT_LABEL"; }
mc_launchagent_plist() { printf '%s/%s.plist' "$(mc_launchagent_dir)" "$MC_LAUNCHAGENT_LABEL"; }

# Homebrew's own copy, from v0.1.x's `service do` block -- kept as a constant
# here (not derived from the label above) because it is intentionally a
# DIFFERENT, fixed name: `homebrew.mxcl.<formula>`, unrelated to whatever label
# memcap chooses for its own agent.
mc_homebrew_launchagent_plist() { printf '%s/homebrew.mxcl.memcap.plist' "$(mc_launchagent_dir)"; }

# Escape hatches, same pattern as MC_DOCKER_RUNTIME (docker.sh): a function-level
# test stub can't reach a real `bin/memcap` subprocess, because sourcing this
# file again clobbers any override set beforehand. A plain environment variable
# survives into any subprocess normally, and lets a test point these at a fake
# script that logs its own invocation instead of touching the real launchd or
# Homebrew -- this machine has a live, enforcing memcap, and a test that
# unloads or removes it for real ends enforcement, which is the exact bug this
# feature exists to fix.
mc_launchctl_bin() { printf '%s' "${MC_LAUNCHCTL_BIN:-launchctl}"; }
mc_brew_bin() { printf '%s' "${MC_BREW_BIN:-brew}"; }

# Resolved via `brew --prefix`, not hardcoded, so the same plist content is
# correct on Apple Silicon (/opt/homebrew) and Intel (/usr/local) -- and always
# points at the stable `opt/memcap` symlink, never a versioned Cellar path, so
# it keeps working across `brew upgrade` swapping the binary underneath it.
# Falls back to /usr/local (the more common historical default) only if brew
# itself cannot answer, which should not happen for a brew-installed memcap.
mc_brew_prefix() {
  local p
  p=$("$(mc_brew_bin)" --prefix 2>/dev/null)
  if [ -n "$p" ]; then printf '%s' "$p"; else printf '/usr/local'; fi
}

# Log paths match what the formula's `service do` block used to configure, so
# an upgrading user's existing log-watching habits (`tail -f
# $(brew --prefix)/var/log/memcap.log`) keep working even though Homebrew no
# longer owns the process that writes them.
mc_launchagent_plist_content() {
  local prefix bin
  prefix=$(mc_brew_prefix)
  bin="$prefix/opt/memcap/bin/memcap"
  cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$MC_LAUNCHAGENT_LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>${bin}</string>
		<string>watch</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>StartInterval</key>
	<integer>60</integer>
	<key>StandardOutPath</key>
	<string>${prefix}/var/log/memcap.log</string>
	<key>StandardErrorPath</key>
	<string>${prefix}/var/log/memcap.err</string>
</dict>
</plist>
PLIST
}

# If both memcap's own agent and Homebrew's older one are loaded, two `watch`
# passes race every 60 seconds -- two processes making kill decisions off two
# separate snapshots. `brew services stop` is best-effort: it can fail if brew
# itself no longer tracks the formula as a service, or isn't on PATH in this
# shell, which is reported but not fatal, since removing the plist file is
# what actually neutralizes it. That removal is NOT best-effort, though: it is
# verified, not assumed. `rm -f` can fail silently for reasons that have
# nothing to do with brew -- an immutable flag, permissions, a read-only
# filesystem -- and letting install proceed anyway would leave the machine
# with BOTH agents loaded: the exact double-agent race this migration exists
# to prevent, arriving through the code written to prevent it. Failing closed
# here costs the user one manual `rm`; failing open costs them two processes
# racing to kill things.
mc_migrate_homebrew_launchagent() {
  local old; old="$(mc_homebrew_launchagent_plist)"
  [ -f "$old" ] || return 0
  echo "Found Homebrew's own memcap LaunchAgent ($old) -- brew upgrade can remove this without warning, which is the bug memcap's own LaunchAgent replaces. Stopping it and installing memcap's own." >&2
  "$(mc_brew_bin)" services stop memcap >/dev/null 2>&1 || \
    echo "  (brew services stop memcap found nothing to stop -- continuing)" >&2
  "$(mc_launchctl_bin)" unload "$old" >/dev/null 2>&1 || true
  rm -f "$old"
  if [ -f "$old" ]; then
    echo "Failed to remove $old -- refusing to install memcap's own LaunchAgent alongside it, which would leave both loaded and racing. Remove $old by hand (check permissions and the immutable/uchg flag: 'chflags nouchg \"$old\"'), then re-run 'memcap service install'." >&2
    return 1
  fi
}

# Idempotent: safe to run again after a config change, a brew prefix change, or
# just to confirm the agent is loaded. Unloading before loading means a rewrite
# takes effect immediately rather than waiting for the next login; unloading
# something not currently loaded is a harmless no-op error, suppressed.
mc_service_install() {
  mc_migrate_homebrew_launchagent || return 1
  local dir plist tmp
  dir="$(mc_launchagent_dir)"
  plist="$(mc_launchagent_plist)"
  mkdir -p "$dir"
  # Written to a temp file IN THE SAME DIRECTORY (not /tmp -- `mv` across
  # filesystems is not atomic) and verified before it ever replaces a working
  # plist. `mc_launchagent_plist_content > "$plist"` directly would truncate
  # the target immediately: a content-generation failure partway through, or
  # the process being interrupted mid-write, leaves a truncated plist on disk
  # having already destroyed a working one. This way, an interrupt leaves the
  # temp file orphaned and the old plist (if any) untouched.
  tmp=$(mktemp "$dir/.${MC_LAUNCHAGENT_LABEL}.XXXXXX") || {
    echo "Failed to create a temp file in $dir -- not touching $plist" >&2
    return 1
  }
  mc_launchagent_plist_content > "$tmp"
  if [ ! -s "$tmp" ]; then
    rm -f "$tmp"
    echo "Failed to generate plist content -- not touching $plist" >&2
    return 1
  fi
  if command -v plutil >/dev/null 2>&1 && ! plutil -lint "$tmp" >/dev/null 2>&1; then
    rm -f "$tmp"
    echo "Generated plist failed validation -- not touching $plist" >&2
    return 1
  fi
  mv "$tmp" "$plist"
  "$(mc_launchctl_bin)" unload "$plist" >/dev/null 2>&1 || true
  if "$(mc_launchctl_bin)" load -w "$plist" >/dev/null 2>&1; then
    echo "Installed and loaded $plist"
  else
    echo "Wrote $plist but 'launchctl load' failed -- load it yourself: launchctl load -w '$plist'" >&2
    return 1
  fi
}

# A tool that installs a LaunchAgent and cannot remove it is worse than one
# that never installed it. Also cleans up a lingering Homebrew plist if one is
# still present, so a stale copy left over from before migration can't recreate
# the double-agent risk on a future `memcap service install`.
mc_service_uninstall() {
  local plist old
  plist="$(mc_launchagent_plist)"
  if [ -f "$plist" ]; then
    "$(mc_launchctl_bin)" unload "$plist" >/dev/null 2>&1 || true
    rm -f "$plist"
    echo "Removed $plist"
  else
    echo "No memcap LaunchAgent installed."
  fi
  old="$(mc_homebrew_launchagent_plist)"
  if [ -f "$old" ]; then
    "$(mc_brew_bin)" services stop memcap >/dev/null 2>&1 || true
    "$(mc_launchctl_bin)" unload "$old" >/dev/null 2>&1 || true
    rm -f "$old"
    echo "Also removed Homebrew's old LaunchAgent ($old)."
  fi
  return 0
}

mc_service_status() {
  local plist old label
  plist="$(mc_launchagent_plist)"
  old="$(mc_homebrew_launchagent_plist)"
  label="$(mc_launchagent_label)"
  if [ -f "$plist" ]; then
    echo "memcap LaunchAgent: $plist"
    if "$(mc_launchctl_bin)" list 2>/dev/null | grep -q "$label"; then
      echo "  loaded"
    else
      echo "  NOT LOADED -- run: memcap service install"
    fi
  else
    echo "No memcap LaunchAgent installed. Run: memcap service install"
  fi
  if [ -f "$old" ]; then
    echo "WARNING: Homebrew's old LaunchAgent is also still present ($old) -- run: memcap service install"
  fi
  return 0
}
