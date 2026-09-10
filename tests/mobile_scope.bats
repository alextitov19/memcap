# Globals and stubs are consumed by sourced production modules.
# shellcheck disable=SC2034,SC2329
load helper
setup() {
  setup_common
  export MC_DRY_RUN=1
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/enforce.sh"
  AGENTPIDS='10 20'; PROTECTEDPIDS='10 11 20 21'; SIMPIDS='11'
  MC_PROC_TABLE='10 1 /bin/codex
11 10 /tmp/ms-playwright/chrome
20 1 /bin/claude
21 20 /bin/xcodebuild'
  MC_MOBILE_BLOCKERS=21
}

@test "MOBILE-SCOPE: only a different known agent can release the browser veto" {
  run mc_mobile_browser_independent 11 /tmp/ms-playwright/chrome
  [ "$status" = 0 ]
  MC_MOBILE_BLOCKERS=10
  run mc_mobile_browser_independent 11 /tmp/ms-playwright/chrome
  [ "$status" = 1 ]
}

@test "MOBILE-SCOPE: unknown, missing or cyclic ownership retains the veto" {
  MC_MOBILE_BLOCKERS=999
  run mc_mobile_browser_independent 11 /tmp/ms-playwright/chrome
  [ "$status" = 1 ]
  MC_MOBILE_BLOCKERS=''
  run mc_mobile_browser_independent 11 /tmp/ms-playwright/chrome
  [ "$status" = 1 ]
  MC_MOBILE_BLOCKERS=21
  MC_PROC_TABLE='11 12 /tmp/ms-playwright/chrome
12 11 /bin/node
21 20 /bin/xcodebuild
20 1 /bin/claude'
  run mc_mobile_browser_independent 11 /tmp/ms-playwright/chrome
  [ "$status" = 1 ]
}

@test "MOBILE-SCOPE: simulators and browser mentions cannot bypass the veto" {
  run mc_mobile_browser_independent 11 '/bin/rg /tmp/ms-playwright/chrome'
  [ "$status" = 1 ]
  run mc_mobile_browser_independent 11 /Library/Developer/CoreSimulator/launchd_sim
  [ "$status" = 1 ]
}

@test "MOBILE-MATCH: text mentioning a GUI app does not count as hands-on work" {
  ps() {
    printf '9001 /usr/bin/rg /Applications/Xcode.app/Contents/MacOS/Xcode\n9002 /Applications/Xcode.app/Contents/MacOS/Xcode\n'
  }
  run mc_hands_on_mobile_pids
  assert_contains "$output" 9002
  assert_not_contains "$output" 9001
}

@test "MOBILE-MATCH: a search for maestro or expo is not active tooling" {
  ps() {
    printf '9001 /usr/bin/rg .maestro/lib\n9002 /usr/bin/java -cp /Users/test/.maestro/lib/a.jar maestro.cli.AppKt mcp\n9003 /usr/bin/rg expo start\n9004 /usr/local/bin/node /project/node_modules/.bin/expo start\n'
  }
  run mc_mobile_tooling_pids
  assert_contains "$output" 9002
  assert_contains "$output" 9004
  assert_not_contains "$output" 9001
  assert_not_contains "$output" 9003
}

reap_fixture() {
  mc_active_mobile_tooling() { MC_ACTIVE_MOBILE_PIDS="$fixture_blocker"; return 0; }
  mc_hands_on_mobile() { return 1; }
  mc_pid_alive() { return 0; }
  mc_sim_is_target() { return 0; }
  mc_kill_pids() { printf 'selected:%s\n' "$1"; }
  MC_VETO_EVIDENCE_CACHE=''
  ps() {
    case "$*" in
      *time=*) echo '0:00.00' ;;
      *command=*) echo '/tmp/ms-playwright/chrome' ;;
      *comm=*) echo /bin/xcodebuild ;;
    esac
  }
  mkdir -p "$(mc_sims_idle_dir)"
  printf '1 999999\n' > "$(mc_sims_idle_stamp 11)"
}

@test "MOBILE-SCOPE: actual tier3 selection retains same-session and unknown vetoes" {
  reap_fixture
  fixture_blocker=21
  run mc_reap_sims
  assert_contains "$output" 'selected: 11'
  fixture_blocker=10
  run mc_reap_sims
  assert_not_contains "$output" 'selected:'
  fixture_blocker=999
  run mc_reap_sims
  assert_not_contains "$output" 'selected:'
}

@test "MOBILE-MATCH: failed process enumeration retains both mobile vetoes" {
  unset MC_ACTIVE_MOBILE_TOOLING MC_HANDS_ON_MOBILE
  ps() { return 1; }
  run mc_active_mobile_tooling
  [ "$status" = 0 ]
  run mc_hands_on_mobile
  [ "$status" = 0 ]
}

@test "MOBILE-MATCH: native tools are recognized under paths containing spaces" {
  ps() {
    printf '9001 /Applications/Xcode Beta.app/Contents/Developer/usr/bin/xcodebuild\n'
  }
  run mc_mobile_tooling_pids
  [ "$status" = 0 ]
  assert_contains "$output" 9001
}
