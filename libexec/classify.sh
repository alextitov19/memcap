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

# EXTRA_AGENTS is a space-separated word list (detect.sh's mc_installed_agents word-
# splits it the same way). Splicing the raw string into the awk ERE as a single
# alternation branch would only match that literal multi-word string -- never a real
# command line -- so build one alternation branch per name instead.
mc_extra_agent_pattern() {
  local a alt=""
  for a in ${EXTRA_AGENTS:-}; do alt="$alt|(^|/)$a([[:space:]]|\$)"; done
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
      if (cmd ~ agentpat) { agent[pid]=1; roots[pid]=1; alist = alist " " pid }
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
      for (i=1;i<=n;i++) {
        p=order[i]
        if (dock[p]) { dtot += R[p]; continue }
        orphan = (P[p] == 1 && C[p] ~ devpat)
        if (sim[p]) { stot += R[p]; slist = slist " " p }
        if (agent[p] || orphan || sim[p]) {
          atot += R[p]
          if (orphan) { olist = olist " " p; ototal += R[p] }
          if (C[p] ~ devpat && !roots[p]) devlist = devlist " " p
        }
      }
      # Quoted on purpose: unquoted `X= 123` evals as an empty assignment plus an
      # attempt to run the command `123`.
      printf "AGENT_KB=%d\nDOCKER_KB=%d\nORPHAN_KB=%d\nSIM_KB=%d\n", atot+0, dtot+0, ototal+0, stot+0
      printf "ORPHANS=\"%s\"\nDEVPIDS=\"%s\"\nSIMPIDS=\"%s\"\nAGENTPIDS=\"%s\"\n", olist, devlist, slist, alist
    }'
}
