load helper

bats_require_minimum_version 1.5.0

setup() {
  setup_common
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/config.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/notify.sh"
  # helper.bash sets this for every other test file so no test compiles an app
  # bundle by accident. This file is the one that tests the building, so it opts
  # back in per test rather than globally.
  unset MC_NO_NOTIFIER
  FAKEBIN="$BATS_TEST_TMPDIR/fakebin"
  mkdir -p "$FAKEBIN"
  OPEN_LOG="$BATS_TEST_TMPDIR/open.calls"
  OSASCRIPT_LOG="$BATS_TEST_TMPDIR/osascript.calls"
  OSACOMPILE_LOG="$BATS_TEST_TMPDIR/osacompile.calls"
  : > "$OPEN_LOG"; : > "$OSASCRIPT_LOG"; : > "$OSACOMPILE_LOG"
}

# `open` and `osascript` are the two ways a notification can actually leave this
# machine. Both are stubbed by PATH rather than by function override, because
# mc_notify calls them as commands -- a shell function cannot be seen by the
# subprocess, which is the mistake AGENTS.md records.
stub_post_commands() {
  local open_status="${1:-0}"
  cat > "$FAKEBIN/open" <<SCRIPT
#!/bin/sh
echo "\$@" >> "$OPEN_LOG"
exit $open_status
SCRIPT
  cat > "$FAKEBIN/osascript" <<SCRIPT
#!/bin/sh
printf '%s' "\$2" >> "$OSASCRIPT_LOG"
exit 0
SCRIPT
  chmod +x "$FAKEBIN/open" "$FAKEBIN/osascript"
}

# A stub that records its invocation and produces the minimum bundle shape the
# rest of mc_build_notifier edits, so the build's ORCHESTRATION can be tested on
# any machine -- including one with no window server, where the real icon render
# cannot run at all.
stub_build_commands() {
  local osacompile_status="${1:-0}"
  cat > "$FAKEBIN/osacompile" <<SCRIPT
#!/bin/sh
out=""
while [ \$# -gt 0 ]; do
  case "\$1" in -o) out="\$2"; shift ;; esac
  shift
done
# ONE line per invocation, naming only the output path. Logging "\$@" instead
# put the multi-line AppleScript source in here, and the tests below that
# count invocations by line read a single build as seven.
echo "-o \$out" >> "$OSACOMPILE_LOG"
if [ "$osacompile_status" != "0" ]; then exit $osacompile_status; fi
mkdir -p "\$out/Contents/Resources"
printf 'stock icon\n' > "\$out/Contents/Resources/Assets.car"
# The real osacompile writes an Info.plist carrying CFBundleIconName, which is
# the key that must be removed for a custom icns to be used at all.
cat > "\$out/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleIconFile</key>
	<string>applet</string>
	<key>CFBundleIconName</key>
	<string>applet</string>
	<key>CFBundleName</key>
	<string>applet</string>
</dict>
</plist>
PLIST
exit 0
SCRIPT
  cat > "$FAKEBIN/codesign" <<'SCRIPT'
#!/bin/sh
exit 0
SCRIPT
  chmod +x "$FAKEBIN/osacompile" "$FAKEBIN/codesign"
}

# The icon render is the one step that needs a window server. Stub it so the
# orchestration tests do not depend on one.
stub_icon_render() {
  local status="${1:-0}"
  eval "mc_notifier_render_icns() { [ \"$status\" = 0 ] || return 1; printf 'icns\n' > \"\$2\"; }"
}

# --- NOTIFY_ICON validation (the value reaches sips and osascript) ------------

@test "mc_notify_icon defaults when NOTIFY_ICON is unset or empty" {
  unset NOTIFY_ICON
  [ "$(mc_notify_icon)" = "🧠" ]
  NOTIFY_ICON=""
  [ "$(mc_notify_icon)" = "🧠" ]
}

@test "mc_notify_icon passes a configured emoji through unchanged" {
  NOTIFY_ICON="🦖"
  [ "$(mc_notify_icon)" = "🦖" ]
}

@test "mc_notify_icon returns empty for NOTIFY_ICON=none, the documented opt-out" {
  NOTIFY_ICON="none"
  [ -z "$(mc_notify_icon)" ]
  NOTIFY_ICON="NONE"
  [ -z "$(mc_notify_icon)" ]
}

@test "mc_notify_icon rejects a control character and says so in the log" {
  NOTIFY_ICON="$(printf 'a\tb')"
  [ "$(mc_notify_icon)" = "🧠" ]
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "NOTIFY_ICON is not a usable icon"
}

@test "mc_notify_icon rejects a value too long to be an icon" {
  NOTIFY_ICON="$(printf 'x%.0s' $(seq 1 40))"
  [ "$(mc_notify_icon)" = "🧠" ]
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "NOTIFY_ICON is not a usable icon"
}

@test "mc_notify_icon accepts a multi-codepoint emoji at the byte limit" {
  # A ZWJ sequence is several codepoints and 25 bytes -- a real icon someone
  # might pick, and the reason the cap is measured in bytes rather than in what
  # bash 3.2 happens to call a character under whatever locale is set.
  # shellcheck disable=SC2034  # read by mc_notify_icon, which is sourced here
  NOTIFY_ICON="👨‍👩‍👧‍👦"
  [ "$(mc_notify_icon)" = "👨‍👩‍👧‍👦" ]
}

# --- Posting through the bundle ----------------------------------------------

@test "mc_notify posts through the bundle when one exists" {
  stub_post_commands
  mkdir -p "$(mc_notifier_app)"
  PATH="$FAKEBIN:$PATH" mc_notify "killed vite"
  run cat "$OPEN_LOG"
  assert_contains "$output" "-a $(mc_notifier_app)"
  run cat "$(mc_notifier_message_file)"
  [ "$output" = "killed vite" ]
  # The whole point of the bundle path: no AppleScript involved.
  run cat "$OSASCRIPT_LOG"
  [ -z "$output" ]
}

@test "mc_notify writes the message verbatim -- no AppleScript escaping on the bundle path" {
  stub_post_commands
  mkdir -p "$(mc_notifier_app)"
  PATH="$FAKEBIN:$PATH" mc_notify 'killed "vite" at C:\temp'
  run cat "$(mc_notifier_message_file)"
  [ "$output" = 'killed "vite" at C:\temp' ]
}

@test "mc_notify leaves no half-written message behind" {
  stub_post_commands
  mkdir -p "$(mc_notifier_app)"
  PATH="$FAKEBIN:$PATH" mc_notify "killed vite"
  # The message is written to a temp name and moved, so the applet -- which
  # `open` may start while mc_notify is still running -- can never read a
  # partial line. Nothing may be left over from that.
  run bash -c "ls '$(mc_state_dir)' | grep -c 'notify-message\\.'"
  [ "$output" = "0" ]
}

@test "mc_notify falls back to osascript when there is no bundle" {
  stub_post_commands
  [ ! -d "$(mc_notifier_app)" ]
  PATH="$FAKEBIN:$PATH" mc_notify "killed vite"
  run cat "$OSASCRIPT_LOG"
  assert_contains "$output" 'display notification "killed vite" with title "memcap"'
  run cat "$OPEN_LOG"
  [ -z "$output" ]
}

@test "mc_notify falls back to osascript when open fails" {
  # A bundle can exist and still not launch: quarantined, signature broken by a
  # backup tool, LaunchServices confused. The notification must still arrive.
  stub_post_commands 1
  mkdir -p "$(mc_notifier_app)"
  PATH="$FAKEBIN:$PATH" mc_notify "killed vite"
  run cat "$OPEN_LOG"
  assert_contains "$output" "-a $(mc_notifier_app)"
  run cat "$OSASCRIPT_LOG"
  assert_contains "$output" 'display notification "killed vite" with title "memcap"'
}

@test "mc_notify still escapes quotes and backslashes on the fallback path" {
  stub_post_commands
  PATH="$FAKEBIN:$PATH" mc_notify 'killed "vite" at C:\temp'
  run cat "$OSASCRIPT_LOG"
  [ "$output" = 'display notification "killed \"vite\" at C:\\temp" with title "memcap"' ]
}

@test "mc_notify keeps its 300-second throttle on the bundle path" {
  stub_post_commands
  mkdir -p "$(mc_notifier_app)"
  PATH="$FAKEBIN:$PATH" mc_notify "first"
  PATH="$FAKEBIN:$PATH" mc_notify "second"
  run bash -c "wc -l < '$OPEN_LOG' | tr -d ' '"
  [ "$output" = "1" ]
  run cat "$(mc_notifier_message_file)"
  [ "$output" = "first" ]
}

# --- Building, and not rebuilding --------------------------------------------

@test "mc_build_notifier removes the stock Assets.car so the custom icns is used" {
  stub_build_commands
  stub_icon_render
  PATH="$FAKEBIN:$PATH" mc_build_notifier
  [ -d "$(mc_notifier_app)" ]
  [ ! -f "$(mc_notifier_app)/Contents/Resources/Assets.car" ]
  [ -f "$(mc_notifier_app)/Contents/Resources/applet.icns" ]
  # CFBundleIconName points into Assets.car and WINS over CFBundleIconFile, so
  # leaving it in place ships the stock AppleScript icon no matter what icns is
  # written next to it.
  run /usr/libexec/PlistBuddy -c "Print :CFBundleIconName" "$(mc_notifier_app)/Contents/Info.plist"
  [ "$status" -ne 0 ]
}

@test "mc_build_notifier gives the bundle memcap's identity and keeps it out of the Dock" {
  stub_build_commands
  stub_icon_render
  PATH="$FAKEBIN:$PATH" mc_build_notifier
  run /usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$(mc_notifier_app)/Contents/Info.plist"
  [ "$output" = "com.memcap.notifier" ]
  run /usr/libexec/PlistBuddy -c "Print :CFBundleName" "$(mc_notifier_app)/Contents/Info.plist"
  [ "$output" = "memcap" ]
  run /usr/libexec/PlistBuddy -c "Print :LSUIElement" "$(mc_notifier_app)/Contents/Info.plist"
  [ "$output" = "1" ]
}

@test "mc_build_notifier leaves no bundle and no staging directory when the icon cannot be rendered" {
  stub_build_commands
  stub_icon_render 1
  PATH="$FAKEBIN:$PATH" run mc_build_notifier
  [ "$status" -ne 0 ]
  [ ! -d "$(mc_notifier_app)" ]
  [ ! -d "$(mc_state_dir)/Notifier.new.app" ]
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "could not render"
}

@test "mc_build_notifier reports a failed osacompile instead of leaving a broken bundle" {
  stub_build_commands 1
  stub_icon_render
  PATH="$FAKEBIN:$PATH" run mc_build_notifier
  [ "$status" -ne 0 ]
  [ ! -d "$(mc_notifier_app)" ]
  run cat "$(mc_state_dir)/actions.log"
  assert_contains "$output" "osacompile failed"
}

@test "mc_ensure_notifier builds once and then leaves it alone" {
  stub_build_commands
  stub_icon_render
  PATH="$FAKEBIN:$PATH" mc_ensure_notifier
  PATH="$FAKEBIN:$PATH" mc_ensure_notifier
  PATH="$FAKEBIN:$PATH" mc_ensure_notifier
  # Every 60-second pass calls this. A rebuild costs ~4 seconds of osacompile,
  # sips and codesign, so anything but a single file read here is a performance
  # bug of exactly the shape tier 1 already had.
  run bash -c "wc -l < '$OSACOMPILE_LOG' | tr -d ' '"
  [ "$output" = "1" ]
}

@test "mc_ensure_notifier rebuilds when the icon changes" {
  stub_build_commands
  stub_icon_render
  NOTIFY_ICON="🧠" PATH="$FAKEBIN:$PATH" mc_ensure_notifier
  NOTIFY_ICON="🦖" PATH="$FAKEBIN:$PATH" mc_ensure_notifier
  run bash -c "wc -l < '$OSACOMPILE_LOG' | tr -d ' '"
  [ "$output" = "2" ]
}

@test "mc_ensure_notifier rebuilds when the bundle has gone missing" {
  stub_build_commands
  stub_icon_render
  PATH="$FAKEBIN:$PATH" mc_ensure_notifier
  rm -rf "$(mc_notifier_app)"
  PATH="$FAKEBIN:$PATH" mc_ensure_notifier
  [ -d "$(mc_notifier_app)" ]
  run bash -c "wc -l < '$OSACOMPILE_LOG' | tr -d ' '"
  [ "$output" = "2" ]
}

@test "mc_ensure_notifier does not retry a failing build on every pass" {
  stub_build_commands
  stub_icon_render 1
  PATH="$FAKEBIN:$PATH" mc_ensure_notifier || true
  PATH="$FAKEBIN:$PATH" mc_ensure_notifier || true
  PATH="$FAKEBIN:$PATH" mc_ensure_notifier || true
  # A machine with no window server will never render an icon, and retrying a
  # multi-second build 1,440 times a day to rediscover that costs more than the
  # icon is worth. The stamp is written on failure for exactly this reason.
  run bash -c "wc -l < '$OSACOMPILE_LOG' | tr -d ' '"
  [ "$output" = "1" ]
}

@test "mc_ensure_notifier does nothing at all under MC_NO_NOTIFIER=1" {
  stub_build_commands
  stub_icon_render
  MC_NO_NOTIFIER=1 PATH="$FAKEBIN:$PATH" mc_ensure_notifier
  run bash -c "wc -l < '$OSACOMPILE_LOG' | tr -d ' '"
  [ "$output" = "0" ]
  [ ! -d "$(mc_notifier_app)" ]
}

@test "NOTIFY_ICON=none removes an existing bundle and returns to plain notifications" {
  stub_build_commands
  stub_icon_render
  PATH="$FAKEBIN:$PATH" mc_ensure_notifier
  [ -d "$(mc_notifier_app)" ]
  NOTIFY_ICON="none" PATH="$FAKEBIN:$PATH" mc_ensure_notifier
  [ ! -d "$(mc_notifier_app)" ]
  # And with the bundle gone, mc_notify is back on the path it always used.
  stub_post_commands
  PATH="$FAKEBIN:$PATH" mc_notify "killed vite"
  run cat "$OSASCRIPT_LOG"
  assert_contains "$output" "display notification"
}

# --- The real thing ----------------------------------------------------------
# Everything above stubs osacompile so it runs anywhere. This one does not: it is
# the only test that would notice osacompile changing what it emits, an applet
# that does not compile, or a signature that does not verify.

@test "a real build produces a signed applet that reads memcap's message file" {
  if ! mc_notifier_render_icns "🧠" "$BATS_TEST_TMPDIR/probe.icns"; then
    # Rendering needs a window server (NSImage lockFocus). Skipped rather than
    # asserted-around so a headless run reports "skipped", not "passed".
    skip "no window server here -- cannot render an icon"
  fi
  run mc_build_notifier
  [ "$status" -eq 0 ]
  [ -d "$(mc_notifier_app)" ]

  # The icon is a real icns, not a file that merely exists.
  run file "$(mc_notifier_app)/Contents/Resources/applet.icns"
  assert_contains "$output" "Mac OS X icon"

  # Editing a bundle invalidates the ad-hoc signature osacompile applied, and an
  # applet whose signature does not match its resources is killed on launch
  # instead of run -- which would look exactly like notifications being off.
  run codesign --verify --strict "$(mc_notifier_app)"
  [ "$status" -eq 0 ]

  # And the compiled script knows where to read the message memcap writes.
  run osadecompile "$(mc_notifier_app)/Contents/Resources/Scripts/main.scpt"
  assert_contains "$output" "$(mc_notifier_message_file)"
  assert_contains "$output" "display notification"
}
