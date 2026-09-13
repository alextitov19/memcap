#!/usr/bin/env bash
# Optional hook transport. Enforcement itself does not depend on jq. Records
# are private and bounded; no terminal injection and no transcript modification.
# Dollar variables in jq programs belong to jq, not the shell.
# shellcheck disable=SC2016
set -uo pipefail

mc_feedback_jq() {
  if command -v jq >/dev/null 2>&1; then command -v jq
  elif [ -x /opt/homebrew/bin/jq ]; then printf /opt/homebrew/bin/jq
  elif [ -x /usr/local/bin/jq ]; then printf /usr/local/bin/jq
  else return 1; fi
}

mc_job_feedback() {
  local jqbin dir body command_text file number
  jqbin=$(mc_feedback_jq) || {
    mc_log 'oversized-job: jq unavailable; agent feedback could not be recorded'; return 1;
  }
  dir="$(mc_state_dir)/job-feedback"
  if ! { mkdir -p "$dir" && chmod 700 "$dir"; }; then mc_state_error "$dir"; return 1; fi
  command_text=$(mc_diag_command "$MC_JOB_IDENTITY")
  body=$("$jqbin" -n --arg pid "$MC_JOB_PID" --arg owner "$MC_JOB_OWNER" \
    --arg cwd "$MC_JOB_CWD" --arg command "$command_text" \
    --arg kb "$MC_JOB_KB" --arg limit "$MC_JOB_LIMIT" --arg at "$(date +%s)" \
    '{pid:$pid,agent_pid:$owner,cwd:$cwd,command:$command,footprint_kb:$kb,limit_gb:$limit,at:$at,
      message:("memcap requested termination of oversized agent job PID " + $pid +
        " (agent PID " + $owner + "): " + (($kb|tonumber)/1048576|tostring) +
        " GB footprint exceeds the " + $limit + " GB per-process limit. Command identity: " + $command +
        ". Split the workload into smaller batches or test files, reduce parallel workers, and investigate unbounded allocation. Do not retry the identical job or increase/disable the cap to get around this. Exit 137/143 may be memcap, not a Docker OOM. This notice is shared with agents working in this project; it may refer to another session.")}') || return 1
  mc_state_write "$dir/$MC_JOB_PID.json" "$body" || return 1
  # Only memcap-created numeric filenames are removed. Keep the latest 32 jobs.
  # shellcheck disable=SC2012 # numeric basenames only, not arbitrary paths
  while IFS= read -r file; do
    number="${file%.json}"
    case "$number" in ''|*[!0-9]*) continue ;; esac
    rm -f "$dir/$file"
    rm -f "$dir"/seen-*"-$file.receipt"
  done < <(LC_ALL=C ls -t "$dir" | awk '/^[0-9]+\.json$/ {n++; if(n>32) print}')
  return 0
}

mc_feedback_hook() {
  local jqbin payload event cwd session key dir file message messages="" at now receipt token canonical
  jqbin=$(mc_feedback_jq) || { echo 'memcap feedback requires jq' >&2; return 1; }
  # Only fields required for routing are read, not transcript contents or tool
  # arguments. Invalid JSON or an unsupported event yields no injected context.
  payload=$(cat)
  event=$(printf '%s' "$payload" | "$jqbin" -er '.hook_event_name | strings') || return 1
  case "$event" in PreToolUse|PostToolUse|PostToolUseFailure|SessionStart|UserPromptSubmit) ;; *) return 0 ;; esac
  cwd=$(printf '%s' "$payload" | "$jqbin" -er '.cwd | strings | select(length>0)') || return 1
  session=$(printf '%s' "$payload" | "$jqbin" -er '.session_id | strings | select(length>0)') || return 1
  canonical=$(cd "$cwd" && pwd -P) 2>/dev/null || return 1
  [ "$canonical" != / ] || return 0
  key=$(printf '%s' "$session" | shasum -a 256 | awk '{print $1}') || return 1
  dir="$(mc_state_dir)/job-feedback"
  [ -d "$dir" ] || return 0
  now=$(date +%s)
  for file in "$dir"/*.json; do
    [ -f "$file" ] || continue
    at=$("$jqbin" -er '.at | tonumber' "$file") || continue
    case "$at" in ''|*[!0-9]*) continue ;; esac
    [ ${#at} -le 12 ] || continue
    [ "$at" -le "$now" ] && [ $((now-at)) -le 86400 ] || continue
    # Component boundary comparison; /project-two is not inside /project.
    message=$("$jqbin" -er --arg cwd "$canonical" \
      'select(.cwd == $cwd or (.cwd | startswith($cwd + "/"))) | .message | strings' "$file") || continue
    token=$(cksum < "$file" | awk '{print $1}') || continue
    receipt="$dir/seen-$key-${file##*/}.receipt"
    [ "$(cat "$receipt" 2>/dev/null)" != "$token" ] || continue
    messages="${messages}${message}
"
    mc_state_write "$receipt" "$token" || :
  done
  [ -n "$messages" ] || return 0
  "$jqbin" -n --arg event "$event" --arg message "$messages" \
    '{hookSpecificOutput:{hookEventName:$event,additionalContext:$message}}'
}

mc_agent_hooks() {
  local agent="$1" jqbin command_text events
  jqbin=$(mc_feedback_jq) || return 1
  printf -v command_text '%q feedback' "$MEMCAP_ROOT/bin/memcap"
  case "$agent" in
    codex) events='["PreToolUse","PostToolUse","SessionStart","UserPromptSubmit"]' ;;
    claude) events='["PreToolUse","PostToolUse","PostToolUseFailure","SessionStart","UserPromptSubmit"]' ;;
    *) echo 'usage: memcap agent-hooks codex|claude' >&2; return 2 ;;
  esac
  "$jqbin" -n --arg command "$command_text" --argjson events "$events" \
    '{hooks:($events | map({key:.,value:[{hooks:[{type:"command",command:$command,timeout:5}]}]}) | from_entries)}'
}
