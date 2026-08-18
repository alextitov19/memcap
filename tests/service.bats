load helper
setup() { setup_common; }

# A test below makes MEMCAP_LAUNCHAGENT_DIR read-only to force `rm -f` to fail
# closed. This runs regardless of whether the test that did it passed or
# failed, so a stuck permission bit never survives into bats' own tmp-dir
# cleanup for this or any later test.
teardown() {
  if [ -d "$MEMCAP_LAUNCHAGENT_DIR" ]; then
    chmod -R u+w "$MEMCAP_LAUNCHAGENT_DIR" 2>/dev/null || true
  fi
}

# --- Plist content -------------------------------------------------------

@test "service install writes a plist with the right label, RunAtLoad, and StartInterval" {
  run "$MEMCAP_ROOT/bin/memcap" service install
  [ "$status" -eq 0 ]
  plist="$MEMCAP_LAUNCHAGENT_DIR/com.alextitov19.memcap.plist"
  [ -f "$plist" ]
  run cat "$plist"
  assert_contains "$output" "<string>com.alextitov19.memcap</string>"
  assert_contains "$output" "<key>RunAtLoad</key>"
  assert_contains "$output" "<key>StartInterval</key>"
  assert_contains "$output" "<integer>60</integer>"
}

# Never `homebrew.mxcl.memcap` -- that label belongs to the plist Homebrew
# itself writes, and this one must never be mistaken for it or collide with it.
@test "service install never reuses Homebrew's own label" {
  run "$MEMCAP_ROOT/bin/memcap" service install
  plist="$MEMCAP_LAUNCHAGENT_DIR/com.alextitov19.memcap.plist"
  run cat "$plist"
  assert_not_contains "$output" "homebrew.mxcl"
}

@test "the generated plist points at the opt/ symlink, never a versioned Cellar path" {
  FAKE_BREW_PREFIX="/opt/homebrew" run "$MEMCAP_ROOT/bin/memcap" service install
  plist="$MEMCAP_LAUNCHAGENT_DIR/com.alextitov19.memcap.plist"
  run cat "$plist"
  assert_contains "$output" "/opt/homebrew/opt/memcap/bin/memcap"
  assert_not_contains "$output" "Cellar"
}

@test "the generated plist is well-formed XML" {
  command -v plutil >/dev/null 2>&1 || skip "plutil not available"
  "$MEMCAP_ROOT/bin/memcap" service install >/dev/null
  plist="$MEMCAP_LAUNCHAGENT_DIR/com.alextitov19.memcap.plist"
  run plutil -lint "$plist"
  [ "$status" -eq 0 ]
  assert_contains "$output" "OK"
}

# --- brew --prefix resolution: Apple Silicon vs Intel ---------------------

@test "an Apple-Silicon brew prefix produces an /opt/homebrew path" {
  FAKE_BREW_PREFIX="/opt/homebrew" run "$MEMCAP_ROOT/bin/memcap" service install
  plist="$MEMCAP_LAUNCHAGENT_DIR/com.alextitov19.memcap.plist"
  run cat "$plist"
  assert_contains "$output" "<string>/opt/homebrew/opt/memcap/bin/memcap</string>"
}

@test "an Intel brew prefix produces a /usr/local path" {
  FAKE_BREW_PREFIX="/usr/local" run "$MEMCAP_ROOT/bin/memcap" service install
  plist="$MEMCAP_LAUNCHAGENT_DIR/com.alextitov19.memcap.plist"
  run cat "$plist"
  assert_contains "$output" "<string>/usr/local/opt/memcap/bin/memcap</string>"
}

# --- launchctl is stubbed, never real --------------------------------------
# The whole point of MC_LAUNCHCTL_BIN: this asserts against the FAKE binary's
# own call log, never against real launchd state -- a real `launchctl load`
# here would load a second, memcap-controlled agent on top of whatever this
# machine already has running for its own real memcap install.

@test "service install calls launchctl load, never for real" {
  run "$MEMCAP_ROOT/bin/memcap" service install
  [ "$status" -eq 0 ]
  run cat "$FAKE_LAUNCHCTL_LOG"
  assert_contains "$output" "load"
}

@test "service install is idempotent -- a second install still succeeds and rewrites the same plist" {
  run "$MEMCAP_ROOT/bin/memcap" service install
  [ "$status" -eq 0 ]
  first=$(cat "$MEMCAP_LAUNCHAGENT_DIR/com.alextitov19.memcap.plist")
  run "$MEMCAP_ROOT/bin/memcap" service install
  [ "$status" -eq 0 ]
  second=$(cat "$MEMCAP_LAUNCHAGENT_DIR/com.alextitov19.memcap.plist")
  [ "$first" = "$second" ]
}

# --- Uninstall --------------------------------------------------------------

@test "service uninstall removes the plist" {
  "$MEMCAP_ROOT/bin/memcap" service install >/dev/null
  plist="$MEMCAP_LAUNCHAGENT_DIR/com.alextitov19.memcap.plist"
  [ -f "$plist" ]
  run "$MEMCAP_ROOT/bin/memcap" service uninstall
  [ "$status" -eq 0 ]
  [ ! -f "$plist" ]
}

@test "service uninstall calls launchctl unload, never for real" {
  "$MEMCAP_ROOT/bin/memcap" service install >/dev/null
  : > "$FAKE_LAUNCHCTL_LOG"
  run "$MEMCAP_ROOT/bin/memcap" service uninstall
  run cat "$FAKE_LAUNCHCTL_LOG"
  assert_contains "$output" "unload"
}

@test "service uninstall is a no-op when nothing is installed" {
  run "$MEMCAP_ROOT/bin/memcap" service uninstall
  [ "$status" -eq 0 ]
  assert_contains "$output" "No memcap LaunchAgent installed"
}

@test "the overall memcap uninstall also removes the LaunchAgent" {
  "$MEMCAP_ROOT/bin/memcap" service install >/dev/null
  plist="$MEMCAP_LAUNCHAGENT_DIR/com.alextitov19.memcap.plist"
  [ -f "$plist" ]
  run "$MEMCAP_ROOT/bin/memcap" uninstall
  [ "$status" -eq 0 ]
  [ ! -f "$plist" ]
}

# --- Migration from Homebrew's own plist -----------------------------------
# v0.1.x had Homebrew write and own homebrew.mxcl.memcap.plist via the
# formula's `service do` block. If both that and memcap's own agent are ever
# loaded at once, two `watch` passes race every 60 seconds against separate
# snapshots. Installing memcap's own agent must detect and neutralize the old
# one first.

@test "service install detects and stops an existing Homebrew LaunchAgent" {
  mkdir -p "$MEMCAP_LAUNCHAGENT_DIR"
  old="$MEMCAP_LAUNCHAGENT_DIR/homebrew.mxcl.memcap.plist"
  echo "<plist/>" > "$old"

  run "$MEMCAP_ROOT/bin/memcap" service install
  [ "$status" -eq 0 ]
  assert_contains "$output" "Homebrew's own memcap LaunchAgent"
  [ ! -f "$old" ]

  run cat "$FAKE_BREW_LOG"
  assert_contains "$output" "services stop memcap"
}

@test "no machine is left with both agents installed after migration" {
  mkdir -p "$MEMCAP_LAUNCHAGENT_DIR"
  echo "<plist/>" > "$MEMCAP_LAUNCHAGENT_DIR/homebrew.mxcl.memcap.plist"

  "$MEMCAP_ROOT/bin/memcap" service install >/dev/null

  [ -f "$MEMCAP_LAUNCHAGENT_DIR/com.alextitov19.memcap.plist" ]
  [ ! -f "$MEMCAP_LAUNCHAGENT_DIR/homebrew.mxcl.memcap.plist" ]
}

# --- Fail-closed: a migration that cannot actually remove the old plist ------
# must abort the install, not proceed. `rm -f` can fail silently for reasons
# unrelated to brew -- permissions, an immutable flag, a read-only filesystem
# -- and installing memcap's own agent anyway would leave BOTH loaded: the
# double-agent race this migration exists to prevent, arriving through the
# code written to prevent it. Forced here by making the LaunchAgent directory
# itself read-only, which makes `rm -f` on a file inside it fail regardless of
# the file's own permissions -- `teardown()` above restores it afterward.
@test "a migration that fails to remove the old plist aborts the install" {
  mkdir -p "$MEMCAP_LAUNCHAGENT_DIR"
  old="$MEMCAP_LAUNCHAGENT_DIR/homebrew.mxcl.memcap.plist"
  echo "<plist/>" > "$old"
  chmod 555 "$MEMCAP_LAUNCHAGENT_DIR"

  run "$MEMCAP_ROOT/bin/memcap" service install
  [ "$status" -ne 0 ]
  assert_contains "$output" "Failed to remove $old"
  assert_contains "$output" "$old"

  # The old plist is still there (removal genuinely failed) and no new plist
  # was written alongside it -- not two agents, not zero, but the one that was
  # already there, untouched, with a message telling the user what to do.
  [ -f "$old" ]
  [ ! -f "$MEMCAP_LAUNCHAGENT_DIR/com.alextitov19.memcap.plist" ]
}

# --- Atomic write: a failed generation must not destroy a working plist -----
# `mc_launchagent_plist_content > "$plist"` directly would truncate the target
# immediately, before any content lands -- a generation failure or an
# interrupt partway through leaves a truncated plist where a working one used
# to be. The fix writes to a temp file in the same directory first and checks
# it is non-empty before ever replacing the real plist. Forced here with a
# function-level override of mc_launchagent_plist_content -- valid in this
# form because the override is defined in the same subshell that sources
# service.sh and calls mc_service_install directly, so nothing re-sources the
# file afterward to clobber it (unlike a real `bin/memcap` invocation).
@test "a failed plist generation does not touch an existing working plist, and install fails" {
  "$MEMCAP_ROOT/bin/memcap" service install >/dev/null
  plist="$MEMCAP_LAUNCHAGENT_DIR/com.alextitov19.memcap.plist"
  before=$(cat "$plist")

  run env MEMCAP_LAUNCHAGENT_DIR="$MEMCAP_LAUNCHAGENT_DIR" MC_LAUNCHCTL_BIN="$MC_LAUNCHCTL_BIN" MC_BREW_BIN="$MC_BREW_BIN" bash -c "
    source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/service.sh'
    mc_launchagent_plist_content() { :; }
    mc_service_install
  "
  [ "$status" -ne 0 ]
  assert_contains "$output" "Failed to generate plist content"

  after=$(cat "$plist")
  [ "$before" = "$after" ]
}

# No leftover temp file after a normal install -- the mktemp'd file is always
# either renamed into place or removed, never abandoned.
@test "service install leaves no orphaned temp file behind" {
  "$MEMCAP_ROOT/bin/memcap" service install >/dev/null
  run bash -c "ls -A '$MEMCAP_LAUNCHAGENT_DIR'"
  assert_not_contains "$output" ".com.alextitov19.memcap."
}

@test "service install with no Homebrew plist present does not touch brew services" {
  run "$MEMCAP_ROOT/bin/memcap" service install
  run cat "$FAKE_BREW_LOG"
  assert_not_contains "$output" "services stop"
}

@test "service uninstall also cleans up a lingering Homebrew plist" {
  mkdir -p "$MEMCAP_LAUNCHAGENT_DIR"
  old="$MEMCAP_LAUNCHAGENT_DIR/homebrew.mxcl.memcap.plist"
  echo "<plist/>" > "$old"

  run "$MEMCAP_ROOT/bin/memcap" service uninstall
  [ "$status" -eq 0 ]
  [ ! -f "$old" ]
  assert_contains "$output" "Also removed Homebrew's old LaunchAgent"
}

# --- Status ------------------------------------------------------------------

@test "service status reports nothing installed" {
  run "$MEMCAP_ROOT/bin/memcap" service status
  assert_contains "$output" "No memcap LaunchAgent installed"
}

@test "service status reports installed and loaded" {
  "$MEMCAP_ROOT/bin/memcap" service install >/dev/null
  echo "com.alextitov19.memcap" > "$FAKE_LAUNCHCTL_LIST_OUTPUT"
  run "$MEMCAP_ROOT/bin/memcap" service status
  assert_contains "$output" "com.alextitov19.memcap.plist"
  assert_contains "$output" "loaded"
  assert_not_contains "$output" "NOT LOADED"
}

@test "service status reports installed but not loaded" {
  "$MEMCAP_ROOT/bin/memcap" service install >/dev/null
  : > "$FAKE_LAUNCHCTL_LIST_OUTPUT"
  run "$MEMCAP_ROOT/bin/memcap" service status
  assert_contains "$output" "NOT LOADED"
}

@test "service status warns about a lingering Homebrew plist" {
  mkdir -p "$MEMCAP_LAUNCHAGENT_DIR"
  echo "<plist/>" > "$MEMCAP_LAUNCHAGENT_DIR/homebrew.mxcl.memcap.plist"
  run "$MEMCAP_ROOT/bin/memcap" service status
  assert_contains "$output" "WARNING"
  assert_contains "$output" "homebrew.mxcl.memcap.plist"
}

# --- Dispatcher --------------------------------------------------------------

@test "an unknown service subcommand prints usage and exits 2" {
  run "$MEMCAP_ROOT/bin/memcap" service bogus
  [ "$status" -eq 2 ]
  assert_contains "$output" "usage: memcap service install|uninstall|status"
}
