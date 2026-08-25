#!/usr/bin/env bash
# Pure classifier. Reads `pid ppid rss command` lines on stdin, emits shell
# assignments. Takes no measurements itself so it can be tested from fixtures.
set -uo pipefail

MC_DEV_PATTERN='(/node|/bun|/deno|/esbuild|/tsx|/nodemon|/next-server|/vite|/ts-node|/webpack|/rollup|/concurrently)([[:space:]]|$)|[[:space:]](uvicorn|gunicorn|nodemon|vite|tsx|next|manage\.py[[:space:]]+runserver)([[:space:]]|$)'
MC_AGENT_PATTERN='(^|/)(claude|codex|cursor-agent|aider|gemini|amp|opencode|goose|crush)([[:space:]]|$)'
MC_DOCKER_PATTERN='Virtualization\.framework.*VirtualMachine|com\.docker|/Docker\.app/|hyperkit|vpnkit'
# argv[0] only -- matching the whole command line classified `rg ms-playwright`
# as a browser and made it a kill candidate.
MC_SIM_EXE='(CoreSimulator|Simulator\.app|launchd_sim|SimulatorTrampoline|simdiskimaged|qemu-system|/emulator$|emulator64|ms-playwright|headless_shell)'
# Narrow argv patterns a casual mention cannot produce.
MC_SIM_ARG='--user-data-dir=[^[:space:]]*(playwright|pw-browser)|\.maestro/lib|maestro\.cli|[[:space:]]-avd[[:space:]]'
MC_SIM_SKIP='/(rg|grep|egrep|awk|sed|ps|top|tail|head|cat|sort|cut|tr|xargs|find|bash|zsh|sh|jq)$'

# Logs, once per throttle window, that some EXTRA_AGENTS entries were thrown
# away. Best-effort on purpose: classify.sh is sourced on its own in tests,
# where common.sh's logger does not exist, and a classifier must never fail
# because it could not write a log line. The key is derived from the rejected
# names themselves (metacharacters folded to `_`, since the key becomes a
# filename) so correcting the config produces a fresh line immediately instead
# of being swallowed by the window the previous, wrong value opened.
mc_log_bad_extra_agents() {
  local names="$1" key
  command -v mc_log_throttled >/dev/null 2>&1 || return 0
  key="extra-agents-invalid-${names//[!A-Za-z0-9_-]/_}"
  mc_log_throttled "${key:0:80}" \
    "config: ignoring EXTRA_AGENTS entries that are not plain agent names (letters, digits, '_' or '-' only):$names"
}

# EXTRA_AGENTS is a space-separated word list (detect.sh's mc_installed_agents word-
# splits it the same way). Splicing the raw string into the awk ERE as a single
# alternation branch would only match that literal multi-word string -- never a real
# command line -- so build one alternation branch per name instead.
#
# Each name is spliced straight into an ERE, so it is validated rather than
# trusted. Three failure modes this closes, all of them silent before:
#
#   `foo,bar`  -> one branch `(^|/)foo,bar([[:space:]]|$)`, which no real command
#                 line can produce. AGENTPIDS came back empty: the user believed
#                 they had added agent protection and had none.
#   `a|`       -> an EMPTY alternation branch, which matches every line. Every
#                 process on the machine became an agent -- and therefore a sweep
#                 root and a protected pid.
#   `my(agent` -> `awk: syntax error in regular expression`; classify emitted
#                 nothing at all, so `eval` got an empty string and every
#                 downstream variable was left unset.
#
# Letters, digits, `_` and `-` cover every real agent binary name (the built-in
# list included) and contain no ERE metacharacter. Anything else is dropped and
# logged rather than spliced -- failing closed, since the middle case above
# turns the whole machine into agent-classified sweep roots.
mc_extra_agent_pattern() {
  local a alt="" bad=""
  # set -f: word-splitting EXTRA_AGENTS unquoted is deliberate, but leaves it
  # exposed to pathname expansion -- a glob character in the value would expand
  # against the current directory's contents instead of being used literally.
  # Still needed alongside the validation below, not replaced by it: expansion
  # happens during the word split, BEFORE any name is inspected, so `a*` in a
  # directory holding `aa` and `ab` would arrive here as two names that both
  # pass validation and would be accepted as real agents.
  set -f
  for a in ${EXTRA_AGENTS:-}; do
    case "$a" in
      *[!A-Za-z0-9_-]*) bad="$bad $a"; continue ;;
    esac
    alt="$alt|(^|/)$a([[:space:]]|\$)"
  done
  set +f
  [ -n "$bad" ] && mc_log_bad_extra_agents "$bad"
  printf '%s' "$alt"
}

mc_classify() {
  awk -v agentpat="${MC_AGENT_PATTERN}$(mc_extra_agent_pattern)" \
      -v devpat="$MC_DEV_PATTERN" -v dockpat="$MC_DOCKER_PATTERN" \
      -v simexe="$MC_SIM_EXE" -v simarg="$MC_SIM_ARG" -v simskip="$MC_SIM_SKIP" '
    {
      pid=$1; ppid=$2; rss=$3
      cmd=$0; sub(/^[[:space:]]*[0-9]+[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+/, "", cmd)
      exe=cmd; sub(/[[:space:]].*$/, "", exe)
      P[pid]=ppid; R[pid]=rss; C[pid]=cmd
      if (cmd ~ agentpat) { agent[pid]=1; alist = alist " " pid }
      if (cmd ~ dockpat)  { dock[pid]=1 }
      if (exe !~ simskip && (exe ~ simexe || cmd ~ simarg)) { sim[pid]=1 }
      order[++n]=pid
    }
    END {
      for (pass=0; pass<24; pass++) { ch=0
        for (i=1;i<=n;i++){p=order[i]; if(!agent[p] && agent[P[p]]){agent[p]=1;ch=1}}
        if (!ch) break }
      for (pass=0; pass<24; pass++) { ch=0
        for (i=1;i<=n;i++){p=order[i]; if(!sim[p] && sim[P[p]]){sim[p]=1;ch=1}}
        if (!ch) break }
      # PROTECTEDPIDS is the PROPAGATED agent set -- the whole live process tree
      # under an agent CLI, not just the pids whose own command line matched.
      # alist (AGENTPIDS) was only ever appended to on a direct match, so every
      # MCP server, hook and tool subprocess under a live session was both
      # unprotected and, if it matched devpat, a tier-2 kill candidate. That was
      # not theoretical: 6 of 10 real tier-2 kills in production were
      # chrome-devtools-mcp telemetry watchdogs running as grandchildren of a
      # live claude session. AGENTPIDS deliberately keeps its old meaning so
      # existing callers do not shift underneath them.
      #
      # Protection is scoped to the LIVE tree only. A process reparented away
      # from a dead session (ppid becomes 1) is not in agent[] and is not
      # protected -- deliberately: once the parent is gone there is no evidence
      # left tying it to a session, and a reparented dev server is precisely
      # what tier 1 exists to reap. Extending protection there would re-create
      # the 388-orphan leak this tool was built for.
      for (i=1;i<=n;i++) { p=order[i]; if (agent[p]) plist = plist " " p }
      for (i=1;i<=n;i++) {
        p=order[i]
        if (dock[p]) { dtot += R[p]; continue }
        orphan = (P[p] == 1 && C[p] ~ devpat)
        if (sim[p]) { stot += R[p]; slist = slist " " p }
        if (agent[p] || orphan || sim[p]) {
          atot += R[p]
          if (orphan) { olist = olist " " p; ototal += R[p] }
          # `!agent[p]`, not `!<direct match>`: DEVPIDS must exclude the entire
          # protected tree, or tier 2 keeps ranking an agents own subprocesses
          # as leaked dev servers.
          if (C[p] ~ devpat && !agent[p]) devlist = devlist " " p
        }
      }
      # Quoted on purpose: unquoted `X= 123` evals as an empty assignment plus an
      # attempt to run the command `123`.
      printf "AGENT_KB=%d\nDOCKER_KB=%d\nORPHAN_KB=%d\nSIM_KB=%d\n", atot+0, dtot+0, ototal+0, stot+0
      printf "ORPHANS=\"%s\"\nDEVPIDS=\"%s\"\nSIMPIDS=\"%s\"\nAGENTPIDS=\"%s\"\nPROTECTEDPIDS=\"%s\"\n", olist, devlist, slist, alist, plist
    }'
}
