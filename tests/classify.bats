load helper

setup() { setup_common;
  # common.sh, not just classify.sh: classify now logs a rejected EXTRA_AGENTS
  # entry through mc_log_throttled (best-effort -- it degrades to silence when
  # the logger is absent), and the rejection tests below assert on that line.
  # Sandboxed by setup_common's MEMCAP_STATE_HOME, so nothing reaches the real
  # actions.log.
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/classify.sh"; }

actions_log() { cat "$MEMCAP_STATE_HOME/memcap/actions.log" 2>/dev/null; }

fixture() { cat "$MEMCAP_ROOT/tests/fixtures/$1"; }

@test "orphaned dev servers are counted as orphans" {
  eval "$(fixture orphan-storm.txt | mc_classify)"
  assert_contains "$ORPHANS" "8506"
  assert_contains "$ORPHANS" "8507"
}

@test "an esbuild child with a live parent is NOT an orphan" {
  eval "$(fixture orphan-storm.txt | mc_classify)"
  assert_not_contains "$ORPHANS" "8524"
}

@test "the agent process itself is found and is never an orphan" {
  eval "$(fixture orphan-storm.txt | mc_classify)"
  assert_contains "$AGENTPIDS" "91633"
  assert_not_contains "$ORPHANS" "91633"
}

@test "REGRESSION: 'rg ms-playwright' is not a simulator" {
  eval "$(fixture mixed.txt | mc_classify)"
  assert_not_contains "$SIMPIDS" "12589"
}

@test "REGRESSION: a real playwright browser IS a simulator" {
  eval "$(fixture mixed.txt | mc_classify)"
  assert_contains "$SIMPIDS" "12600"
}

@test "REGRESSION: Maestro's JVM IS a simulator" {
  eval "$(fixture mixed.txt | mc_classify)"
  assert_contains "$SIMPIDS" "91650"
}

@test "REGRESSION: grepping for a maestro/playwright marker is not itself a simulator (MC_SIM_SKIP)" {
  # Without MC_SIM_SKIP excluding /grep as an exe, this line's argument
  # ("maestro.cli") matches MC_SIM_ARG and the process would land in SIMPIDS --
  # tier 3's kill-candidate list -- for having merely searched for the string.
  eval "$(printf '%s\n' '12700 91633 4976 /opt/homebrew/bin/grep -r maestro.cli /Users/x/proj' | mc_classify)"
  assert_not_contains "$SIMPIDS" "12700"
}

@test "EXTRA_AGENTS from the config extends the agent list" {
  # shellcheck disable=SC2034  # consumed by mc_classify via awk -v
  EXTRA_AGENTS="mycustomagent"
  eval "$(printf '%s\n' '  999     1  50000 /usr/local/bin/mycustomagent --run' | mc_classify)"
  assert_contains "$AGENTPIDS" "999"
}

# --- Final review, C2: EXTRA_AGENTS with more than one name -------------------
# detect.sh word-splits EXTRA_AGENTS; classify.sh spliced the raw string into the
# awk ERE as a single alternation branch, so a two-name value only matched the
# literal string "myagent otheragent" -- which no real command line produces --
# and silently voided mc_filter_protected's protection for both names.
@test "EXTRA_AGENTS with more than one name protects every name, not just a literal match" {
  # shellcheck disable=SC2034  # consumed by mc_classify via awk -v
  EXTRA_AGENTS="myagent otheragent"
  eval "$(printf '%s\n%s\n' '998 1 50000 /usr/local/bin/myagent --run' '999 1 50000 /usr/local/bin/otheragent --run' | mc_classify)"
  assert_contains "$AGENTPIDS" "998"
  assert_contains "$AGENTPIDS" "999"
}

@test "the docker VM is counted as docker, not as an agent" {
  eval "$(fixture mixed.txt | mc_classify)"
  [ "$DOCKER_KB" -gt 9000000 ]
  # T3 gap the final review flagged: this asserted DOCKER_KB only, never that the
  # VM's own pid (70001 in mixed.txt) is excluded from AGENTPIDS. classify.sh's
  # `if (dock[p]) { dtot += R[p]; continue }` makes that structural, but the test
  # should say so.
  assert_not_contains "$AGENTPIDS" "70001"
}

@test "pid lists are quoted so eval cannot execute a pid" {
  run bash -c "$(fixture mixed.txt | mc_classify | head -20); echo ok"
  [ "$status" -eq 0 ]
  assert_contains "$output" "ok"
}

# --- Final review, residual: EXTRA_AGENTS must not be exposed to globbing -----
# `for a in ${EXTRA_AGENTS:-}` word-splits unquoted -- deliberate, it's a
# space-separated list -- but that also exposes it to pathname expansion. A
# glob character in the value (the README asks users to avoid one; nothing
# enforces it) would expand against whatever the current directory happens to
# contain instead of being used literally.
#
# `set -f` is still what fixes this, not the name validation added for C3: the
# expansion happens during the word split, BEFORE any name is inspected, so
# without it this would arrive as the two words "aa" and "ab" -- both of which
# pass validation and would be accepted as genuine agent names.
@test "EXTRA_AGENTS containing a glob character is not expanded against the cwd" {
  globdir="$BATS_TEST_TMPDIR/globtest"
  mkdir -p "$globdir"
  touch "$globdir/aa" "$globdir/ab"
  run bash -c "cd '$globdir' && source '$MEMCAP_ROOT/libexec/classify.sh' && EXTRA_AGENTS='a*' mc_extra_agent_pattern"
  # Unexpanded AND rejected: `*` is not a legal agent-name character, so the
  # pattern comes back empty. Expanded (the pre-fix bug), the loop would iterate
  # "aa" and "ab" as two separate, valid-looking words.
  [ -z "$output" ]
  assert_not_contains "$output" "aa"
  assert_not_contains "$output" "ab"
}

# --- Audit C3: PROTECTEDPIDS is the agent TREE, not just direct CLI matches ---
# `alist` (AGENTPIDS) was appended to only on a direct agent-CLI match, while the
# ancestry propagation marked descendants in agent[] for AGENT_KB accounting
# only. Every MCP server, hook and tool subprocess under a live session was
# therefore both unprotected by mc_filter_protected and, when it matched the dev
# pattern, a tier-2 kill candidate. 6 of the 10 real tier-2 kills in 11 days of
# production logs were chrome-devtools-mcp telemetry watchdogs killed exactly
# this way. The fixture is that real tree.
@test "REGRESSION C3: an MCP watchdog under a live agent is protected, not a dev server" {
  eval "$(fixture agent-mcp-tree.txt | mc_classify)"
  # The whole chain: claude -> npm exec -> chrome-devtools-mcp -> node watchdog.
  assert_contains "$PROTECTEDPIDS" "83665"
  assert_contains "$PROTECTEDPIDS" "83693"
  assert_contains "$PROTECTEDPIDS" "83957"
  assert_contains "$PROTECTEDPIDS" "83989"
  # The watchdog matches the dev pattern (`/node `), which is what made it a
  # tier-2 victim. It must no longer be a candidate.
  assert_not_contains "$DEVPIDS" "83989"
}

@test "REGRESSION C3: every pid in PROTECTEDPIDS is excluded from DEVPIDS" {
  eval "$(fixture agent-mcp-tree.txt | mc_classify)"
  for p in $PROTECTEDPIDS; do
    assert_not_contains " $DEVPIDS " " $p "
  done
  # ...while a genuine orphan, reparented to launchd, still is one.
  assert_contains "$ORPHANS" "77482"
  assert_contains "$DEVPIDS" "77482"
}

@test "AGENTPIDS keeps its old meaning so existing callers do not shift" {
  eval "$(fixture agent-mcp-tree.txt | mc_classify)"
  assert_contains "$AGENTPIDS" "83665"
  assert_not_contains "$AGENTPIDS" "83693"
  assert_not_contains "$AGENTPIDS" "83957"
  assert_not_contains "$AGENTPIDS" "83989"
}

# Deliberate scope limit, recorded so it cannot be "fixed" by accident: protection
# follows the LIVE process tree only. Once the agent session dies its children are
# reparented to launchd, nothing links them to a session any more, and a reparented
# dev server is exactly what tier 1 exists to reap -- the 388-orphan leak memcap was
# built for was made of precisely these. Tier 1's own gates (ppid==1, dev pattern,
# and a canonical match against a recorded sweep root) are the protection at that
# point, not PROTECTEDPIDS.
@test "a process reparented away from a dead agent session is NOT protected" {
  # The same watchdog as above, minus its ancestry: the session it belonged to
  # has exited, so its ppid is now 1.
  eval "$(printf '%s\n' '83989 1 40000 /opt/homebrew/bin/node /Users/x/.npm/_npx/chrome-devtools-mcp/telemetry/watchdog/main.js --parent-pid=83957' | mc_classify)"
  [ -z "${PROTECTEDPIDS// /}" ]
  assert_contains "$ORPHANS" "83989"
  assert_contains "$DEVPIDS" "83989"
}

@test "PROTECTEDPIDS is empty, not unset, when no agent is running" {
  eval "$(printf '%s\n' '4242 1 1000 /bin/cat /etc/hosts' | mc_classify)"
  [ -z "${PROTECTEDPIDS// /}" ]
}

# --- Audit: EXTRA_AGENTS is spliced into an ERE, so it must be validated ------
# Three verified pre-fix behaviours, all silent:
#   `foo,bar`  -> AGENTPIDS="" (a branch no command line can match; the user
#                 believed they had added protection and had none)
#   `a|`       -> an empty alternation branch matching EVERY process, so the
#                 whole machine became agent-classified sweep roots
#   `my(agent` -> `awk: syntax error in regular expression`, classify emitted
#                 nothing at all
@test "EXTRA_AGENTS with a comma-separated value is rejected, not silently matched" {
  # shellcheck disable=SC2034  # consumed by mc_classify via awk -v
  EXTRA_AGENTS="foo,bar"
  eval "$(printf '%s\n%s\n' '901 1 5000 /usr/local/bin/foo --run' '902 1 5000 /usr/local/bin/bar --run' | mc_classify)"
  assert_not_contains "$AGENTPIDS" "901"
  assert_not_contains "$AGENTPIDS" "902"
  assert_contains "$(actions_log)" "EXTRA_AGENTS"
  assert_contains "$(actions_log)" "foo,bar"
}

@test "REGRESSION: EXTRA_AGENTS='a|' does not turn every process into an agent" {
  # shellcheck disable=SC2034  # consumed by mc_classify via awk -v
  EXTRA_AGENTS="a|"
  eval "$(printf '%s\n%s\n' '100 1 5000 /bin/cat /etc/hosts' '200 1 5000 /usr/sbin/cupsd -l' | mc_classify)"
  [ -z "${AGENTPIDS// /}" ]
  [ -z "${PROTECTEDPIDS// /}" ]
  assert_contains "$(actions_log)" "EXTRA_AGENTS"
}

@test "REGRESSION: EXTRA_AGENTS with an unbalanced paren still classifies" {
  # shellcheck disable=SC2034  # consumed by mc_classify via awk -v
  EXTRA_AGENTS="my(agent"
  run bash -c "source '$MEMCAP_ROOT/libexec/common.sh'
    source '$MEMCAP_ROOT/libexec/classify.sh'
    EXTRA_AGENTS='my(agent'
    printf '%s\n' '91633 1 178000 claude' | mc_classify"
  [ "$status" -eq 0 ]
  # Pre-fix awk died on the regex and printed nothing, so eval got an empty
  # string and every downstream variable was left unset.
  assert_contains "$output" "AGENT_KB="
  assert_contains "$output" 'AGENTPIDS=" 91633"'
  assert_not_contains "$output" "syntax error"
}

@test "a valid name alongside an invalid one is still honoured" {
  # shellcheck disable=SC2034  # consumed by mc_classify via awk -v
  EXTRA_AGENTS="goodagent bad;name"
  eval "$(printf '%s\n%s\n' '911 1 5000 /usr/local/bin/goodagent --run' '912 1 5000 /usr/local/bin/badname --run' | mc_classify)"
  assert_contains "$AGENTPIDS" "911"
  assert_not_contains "$AGENTPIDS" "912"
  assert_contains "$(actions_log)" "bad;name"
  assert_not_contains "$(actions_log)" "goodagent"
}

@test "a rejected EXTRA_AGENTS value logs once per throttle window, not every pass" {
  # shellcheck disable=SC2034  # consumed by mc_classify via awk -v
  EXTRA_AGENTS="foo,bar"
  for _ in 1 2 3 4 5; do
    printf '%s\n' '901 1 5000 /usr/local/bin/foo --run' | mc_classify >/dev/null
  done
  run bash -c "grep -c EXTRA_AGENTS '$MEMCAP_STATE_HOME/memcap/actions.log'"
  [ "$(echo "$output" | tr -d ' ')" = "1" ]
}

@test "correcting a rejected EXTRA_AGENTS value logs the new value immediately" {
  EXTRA_AGENTS="foo,bar"
  printf '%s\n' '901 1 5000 /usr/local/bin/foo --run' | mc_classify >/dev/null
  # A different bad value must not be swallowed by the first one's window --
  # the throttle key is derived from the rejected names themselves.
  # shellcheck disable=SC2034  # consumed by mc_classify via awk -v
  EXTRA_AGENTS="baz;qux"
  printf '%s\n' '902 1 5000 /usr/local/bin/baz --run' | mc_classify >/dev/null
  assert_contains "$(actions_log)" "foo,bar"
  assert_contains "$(actions_log)" "baz;qux"
}
