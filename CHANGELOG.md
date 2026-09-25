# Changelog

## v0.16.6 — 2026-09-25

- Keep single read-only sed substitutions with escaped slash delimiters native
  (#129). Execution/write flags, additional scripts and in-place edits remain
  managed; expanded support does not evaluate shell expressions.
- Correct the Stop-hook notification contradiction reported after v0.16.5:
  once Stop has blocked ending the turn, use the existing task's blocking wait
  instead of trying to end the turn to receive a native notification.
- Explain this boundary in installed and runtime guidance. Native completion
  notifications remain preferred when the client can suspend without a blocked
  Stop. Admission, pending-work ownership and cancellation rules are unchanged.

## v0.16.5 — 2026-09-25

- Retain reduced adaptive allowances during sampler contention and incomplete
  observations, instead of repeatedly restoring the original startup estimate.
  Fresh measurement/pressure gates and explicit reservation floors still apply.

- Keep GitHub issue/PR inspection, finite JSON selectors, bounded inspection loops, literal note appends and
  guarded filename-search consumers out of unnecessary workload admission.
- Validate every `memcap wait` usage error immediately, including invalid timeouts.
- Prefer native completion consistently across hooks and queue output. Routine
  queue transitions now carry concise context without redundant host probes.
- Explain direct report submission and prevent instructions from encouraging
  duplicate retrospective reports when versions or symptom options change.
- Audit follow-up reports #104–122 and #124 and today’s retained logs through 11:10 PDT.

## v0.16.4 — 2026-09-25

- Keep current-home reads, literal path aliases, additional device/cloud status
  calls and bounded log excerpts native. Invalid wait IDs reach usage validation
  immediately; Git hooks, opaque scripts and arbitrary execution remain managed.
- Recognize wrapped dev servers and streaming device logs as persistent resources
  so finite-work Stop hooks do not wait for them to exit. Admission still applies.
- Preserve valid reservation history across intervening stale cache reads without
  releasing capacity from stale evidence. The next fresh sample must still pass
  the continuity gate before an old peak can retire.
- Send full operational guidance once per session/version/state, suppress false
  diagnostics from successful inspection, and distinguish running/queued work in
  fallback wait output. Native completion notifications remain preferred.
- Add fixed report symptoms and explicit deduplication outcomes. Reporting time is
  no longer presented as proof of when an older incident occurred.
- Distinguish shared-sampler contention from a failed memory measurement in
  admission notices, queue blockers and numeric event code 10.
- Audit September 25 logs and reports #82–102, including retrospective incidents
  and proposed improvements, in `docs/feedback-2026-09-25.md`.

## v0.16.3 — 2026-09-24

- Keep redirected waits, literal regex end anchors, regex-range file reads,
  process lookups, version aliases and `tr` inspection pipelines out of admission.
  Preserve redirection adjacency and quoted operators; execution and mixed build
  stages still require admission. Legacy managed waits cannot await themselves.
- In adaptive mode, retire old reservation peaks after a complete minute of fresh
  measurements. Retain effective allowances during incomplete samples or owner
  loss; immediately reserve observed growth. Strict and explicit policies remain.
- Explain headroom arithmetic in queue output and emit correlated, numeric queue
  events. Distinguish application exits, signal exits and supervisor cancellation.
- Separate reporting-probe failures from queue-sample failures, and report effective
  reservations instead of making users infer them from original requests.
- Audit the September 24 logs and reports #58–80; document reproduced fixes and
  remaining limits of historical attribution in `docs/feedback-2026-09-24.md`.

## v0.16.2 — 2026-09-23

- Make owner pause preserve native command foreground/background behavior. Hooks
  no longer force background tasks or issue misleading queue instructions while
  paused; new paused runs avoid registry locks, reservations and worker limits.
- Preserve original worker arguments/environment for queued work released by an
  owner pause, while retaining supervision and disabling estimate learning for
  that unrestricted run. Existing running processes retain their launch settings.
- Refresh runtime guidance once per pause/resume transition as well as per version.
- Recognize finite AWS log reads, GitHub workflow control with literal repository
  aliases, single-file cleanup plus inspection, and checked pathname brace reads.
  Persistent streams, recursive cleanup, substitutions and mixed builds stay managed.
- Address reports #49–55 with concrete reproductions and owner-pause regressions.

## v0.16.1 — 2026-09-23

- Run reported glob searches and narrow path-query/file-read commands immediately
  after validating the actual shell-expanded arguments. Execution options still
  enter normal admission. Newly invoked cached wrappers gain the same behavior.
- Recognize the reported finite directory-status loop and process diagnostics as
  lightweight inspection; preserve foreground output and exit status, including
  127/126 for missing or non-executable tools instead of queue-failure 75.
- Teach all agent integrations that Docker’s ceiling is not reserved RAM, VM and
  container figures cannot be subtracted to promise reclaim, and accumulated swap
  is not current paging. Paused admission is not a refusal to execute.
- Stop directing agents back to memcap wait when no managed work remains; native
  background tasks need their own completion notification and final status.

## v0.16.0 — 2026-09-23

- Add owner-selected adaptive admission: allow green/yellow launches above the
  footprint planning target while retaining physical headroom, staged starts,
  fresh measurement, explicit reservations and red-pressure blocking.
- Move host sampling out of the shared registry lock; status and wait remain
  read-only. Busy sampling no longer makes ordinary queue observation contend.
- Learn private workload estimates from completed sampled runs, allocate workers
  from shared CPU capacity, and bound large-job fairness drain windows.
- Keep adaptive watchdog behavior consistent with its planning target; preserve
  existing termination exclusions and explicitly configured oversized-job limits.
- Add numeric bounded local events and sanitized policy/paging/sample-age reports.
  New setup selects adaptive; existing settings and paused state remain untouched.

- Scope the agent protection guidance (#43): hook context and diagnostics previously forbade any change to memcap protection, which also blocked explicitly owner-authorized memcap development. Hooks and the integrated profile block now share one wording: never weaken protection to unblock unrelated work; development, testing, releases and installation of memcap itself are permitted only when the user explicitly authorizes them.

## v0.15.1 — 2026-09-23

- Remove memcap’s shared five-report daily publication cap and one-hour retry cooldown. Existing ledgers no longer prevent new reports after an upgrade.
- Keep publishing consent, duplicate suppression, private sanitized drafts, bounded network calls and uncertain-POST protection. No automatic retry loop or replay of saved drafts.
- Refresh agent guidance and reporting status to explain that memcap has no publication quota; GitHub/network/authentication errors can still defer a report.

## v0.15.0 — 2026-09-23

- Fix unnecessary 2 GB reservations for multiline and finite file-loop inspection, literal locale/AWS prefixes, Git directory options and SSM control calls. Keep unknown execution and heavy stages managed.
- Add read-only `memcap wait --session --timeout 60`, avoiding queued shell pipelines for job lookup. Explain cancellation of obsolete owned tasks without abandoning needed work.
- Refresh running agents’ guidance once per installed version on their next tool call; reclassify newly launched cached automatic wrappers. Existing running supervisors retain their code.
- Add fixed `--context` report details and include them in deduplication, retaining separate read/wait/remote/Stop-hook incidents without uploading commands or free text.
- Cover the failures reported in #19, #20, #22, #23 and #24. Real heavy-work capacity waits still honor pressure, total budget, headroom and reservations; snapshots alone do not prove deadlock.

## v0.14.2 — 2026-09-23

- Require agents to report memcap-caused productivity regressions even when commands succeed: queued lightweight inspection, excessive waiting/starvation, and wasteful polling.
- Add `lightweight-queued` and `polling-overhead` report categories, plus optional bounded `--wait-seconds` observations. Keep these reporting commands outside workload admission.
- Deliver the policy through global guidance, session/prompt hooks, queue interception and pending/completed admission feedback. Preserve consent, deduplication and publication rate limits; never report on every poll.

## v0.14.1 — 2026-09-22

- Include sanitized machine capacity, macOS version, CPU load, swap/disk availability, enforcement pause state and measured memory subtotals in agent reports.
- Add aggregate queue ages, session counts, requested memory and configured admission limits. Persist fixed last-admission blocker codes for read-only reporting without taking the queue lock.
- Explain measurement freshness and unknown values; instruct agents to report suspected excessive throttling with `queue-stall`. No hostnames, project identities or raw diagnostics are published.

## v0.14.0 — 2026-09-22

- Add `memcap report CATEGORY` with private local drafts and one-time opt-in to public GitHub reporting using the user's own GitHub CLI login.
- Publish only fixed error categories and allowlisted numeric diagnostics. Never upload raw logs, commands, project paths, source or tool output.
- Reuse matching issues across installations, add at most one report per installation/category/version, and bound submissions with a local lock, daily limit, cooldown and uncertain-submission handling.
- Keep reporting outside the workload queue, update Claude/Codex guidance, and offer opt-in during setup with a default of no. Reporting does not change memory enforcement or its pause state.

## v0.13.0 — 2026-09-22

- Cover the later semicolon and bounded output-file polling variants observed in the affected Claude session.
- Default to one shared measured-use budget across Docker, agents and simulators. A configured Docker VM maximum no longer reserves an agent-budget slice.
- Keep admission reservations, live pressure checks, headroom and guarded cleanup. Report shared usage without attributing queue delays to Docker's separate ceiling.
- Preserve the legacy watchdog split via explicit `BUDGET_MODE=split`; no Docker restart or VM setting change during upgrade.

## v0.12.1 — 2026-09-22

- Add read-only `memcap wait JOB_ID --timeout 60` for agent builds without TaskOutput; hooks and managed guidance explain the fallback.
- Refuse recognized synthetic polling loops and retire verified legacy queued/orphaned wait-only jobs through the watchdog, with fresh identity checks and notices.
- Share host samples for at most two seconds across waiters, retain atomic reservation accounting, and recheck current pressure before admission.
- Distinguish lock failures and integration health from memory admission reasons.

## v0.12.0 — 2026-09-21

- Add `memcap integrate` to discover Claude/Codex profiles, merge memcap hooks,
  and maintain a small global instruction block. Back up changed files, preserve
  user settings and symlinks, refuse malformed profiles, and make reruns idempotent.
- Offer integration during `memcap init` with explicit opt-in. Use stable Homebrew
  opt paths so hooks survive upgrades; support custom profile directories.
- Add `memcap doctor` for missing/stale hooks, timeouts, integration versions,
  disabled Claude hooks and Codex trust. A running Codex daemon can report trust;
  unavailable runtime verification is explicitly unverified, never assumed healthy.
- Do not alter agent permissions or automatically approve hook trust.

## v0.11.0 — 2026-09-21

- Reduce unused automatic reservations after the 30-second startup window using
  measured demand plus growth allowance; keep explicit and uncertain reservations.
- Avoid blocking smaller work behind an aged request when no finite job can finish
  to free capacity. Generated Stop hooks wait locally up to a minute, with a matching
  75-second timeout, preventing rapid read/Stop/model loops after hook updates.

- Ask agents to use a blocking 60-second task poll while pending, and reduce
  queue reminders to once per minute. Local admission checks still run every two
  seconds without model calls.

- Rotate new admissions between sessions across jobs and persistent resources.
  A request waiting at the front for 60 seconds can hold back newer admissions
  while existing work finishes, preventing continuous small jobs from starving it.
- Cancel confirmed standalone simulator boot attempts after three minutes via
  the watchdog, including older registered runners. Fresh identity, whole-group
  ownership and narrow command checks protect active builds and simulator services.
  Keep reservations until managed processes exit and report preparation failure.
- Keep bounded `sed -n '1,20p'` reads from stdin outside the heavy-work queue.

## v0.10.1 — 2026-09-21

- Deliver queue, polling and verification guidance through standard agent hooks,
  including fresh sessions and separate Claude profiles, without custom Markdown.
- Add bounded read-only diagnostics when tool results mention simulator boot,
  memory allocation or queue problems. Report current measurements and device
  states without inferring a past cause or declaring tests passed/failed.
- Explain live waiting versus expired waiters directly in queue output, and
  report when a waiting command starts. Never automatically retry or reset devices.

## v0.10.0 — 2026-09-21

- Add lifecycle-based garbage collection for idle agent-owned Playwright
  browsers and recognized development servers, plus abandoned iOS runtime
  leaves. Completion hooks, subagent state, CPU history, TCP connections and
  fresh process identities guard a narrow exception to live-tree protection.
  Agent CLIs, MCP/language servers and Docker are retained. Default mode is
  observation; `GC_MODE=on` enables automatic collection after a ten-minute grace.
- Let the oldest waiting job that fits run, so an oversized reservation cannot
  stall smaller jobs. Document a throughput preset for 24 GB Macs: eight possible
  jobs, 20 GB combined budget, 2 GB headroom, two workers and yellow permitted.
- Keep grep searches and quoted ripgrep regex/glob patterns outside the build
  queue. Shell substitutions, execution hooks and heavy pipeline stages still queue.
- Claude launches managed tasks in the background with an unlimited queue wait.
  Lifecycle feedback asks it to keep polling pending finite jobs and read their
  final result. The host retains control of cancellation and tool deadlines.

## v0.9.1 — 2026-09-21

- Keep lightweight search pipelines, bounded sed reads, memcap diagnostics and
  GitHub run watchers out of the expensive-job queue. A remote CI watcher no
  longer occupies a build slot or reserves 2 GB. Every stage of a recognized
  lightweight shell chain must qualify; builds and unknown stages remain queued.
- Add `QUEUE_MAX_PRESSURE=yellow` for users who want to admit work at warning
  memory pressure. Green-only admission remains the default; critical/red,
  unknown pressure and faulty measurements always block new jobs.
- Keep job slots, combined-budget reservations and host headroom enforced under
  both policies. Document why a green pressure graph can still leave jobs queued
  and when waiting runners pick up configuration changes.

## v0.9.0 — 2026-09-21

- Queue expensive agent work before it starts with `memcap run`. Participating
  Claude/Codex sessions share two finite-job slots, 2 GB reservations per job,
  and a 3 GB host-headroom gate by default. Admission also requires normal
  macOS memory pressure and healthy footprint measurements.
- Account for outstanding reservations and observed workload memory atomically
  across sessions. Waiting requests expire after 30 minutes by default; ordinary
  background children retain their reservation after the launch shell exits.
- Reuse registered persistent resources by project and name, and bound workers
  for supported build/test tools. Add `memcap queue` and queue status summaries.
- Generate opt-in queue hooks with `memcap agent-hooks codex|claude --queue`.
  Hooks preserve existing permission handling; commands outside supported hooks
  and arbitrary internal workers are not universally contained. Python 3.9+ is
  required for the queue; the watchdog remains independent of Python.
- Route explicit managed-job cancellation through the existing kill choke point
  with fresh ownership, process-start and group checks. Agent CLIs, memcap's
  ancestry and `memcap off` remain protected; cleanup-tier gates are unchanged.

## v0.8.0 — 2026-09-13

- Stop confirmed oversized Claude/Codex child processes, including live Python
  test jobs previously protected by the entire agent tree. Default per-process
  threshold is 4 GB (`AGENT_JOB_MAX_GB`); fresh footprint and ownership checks are
  required, and agent CLIs, memcap ancestry, Docker and sims remain protected.
- Add project-scoped agent feedback hooks with batching and worker-limit guidance.
  `memcap agent-hooks codex|claude` prints mergeable configuration; `memcap feedback`
  consumes supported lifecycle input. Hook installation/trust is separate.

## v0.7.0 — 2026-09-09

Pressure diagnostics and safer cleanup after the September watchdog-panic audit.

- Watch host disk availability and used swap independently of the agent budget.
  Defaults warn below 10 GB disk or at 8 GB used swap; an unavailable reading is
  reported as unknown, and full *allocated* swap alone is not treated as exhaustion.
- Keep twelve private, bounded pressure snapshots, at most once every five minutes
  during sustained pressure. Each records the largest twenty processes across the
  whole machine, including unclassified Python workers, parent ancestry, start
  identity, memory metric, command and protection reason. Common credential
  arguments are redacted. `memcap diagnostics` reads the latest saved snapshot.
- Always report combined over-cap usage, including when agents exceed their own
  allocation and tier 2 declines. Status distinguishes an over-budget sample, host
  pressure, and failed state writes from a healthy completed pass.
- Preserve counters and heartbeats through atomic writes; failed state writes
  include timestamps and paths on stderr. Failed audit writes also send the
  original event to stderr instead of losing the explanation.
- Withhold tier 2 on unexpected footprint-measurement faults. Carry health beside
  the sample so an unwritable state directory cannot substitute stale health.
  Deliberate `MC_NO_TOP=1` remains supported; orphan and idle-resource checks retain
  their independent safety gates. Correct status text that claimed no tier ran.
- Mobile veto diagnostics identify the blocking processes. Search commands that
  merely mention Maestro, Expo or a simulator app no longer count as mobile work.
  An idle browser may bypass a mobile veto only when it belongs to a different
  known agent from **every** blocker. Unknown ownership, same-session work,
  mobile simulators, held MCP resources and all existing kill safeguards remain
  protected. Tier 2 retains its mobile veto.
- When Docker's ceiling differs from configuration, status shows the resulting
  agent-plus-Docker allowances and headroom at current Docker usage. No Docker
  settings or agent budgets are automatically changed.
- New settings: `HOST_MIN_DISK_GB`, `HOST_MAX_SWAP_GB`, `PRESSURE_SNAPSHOT_SEC`.
  Existing configuration is preserved. Generic Python workers are diagnosed, not
  made automatic kill targets.

## v0.6.0 — 2026-09-04

Found by auditing nine days of `actions.log` (1,463 lines) on the author's machine
since v0.5.1 shipped, and confirmed live: a device was booted and shut down by hand to
read what `launchd_sim` actually carries in its argv, and a throwaway launchd job was
run to see what the daemon can and cannot read.

### Tier 3 shut down a simulator out from under a live Maestro run, 38 times

- **The evidence that "a device is idle" was a process with no device.**
  `MC_SIM_IOS_EXE` matched `launchd_sim|SimulatorTrampoline`, and one machine-wide
  flag was set by any ready pid matching either. SimulatorTrampoline is a
  CoreSimulator helper with no device affinity — measured on the author's machine at
  40 hours alive, 11 CPU-seconds total, the same pid across a device booting _and_
  being shut down — so "a simulator is idle" was permanently true and permanently
  CPU-flat. The moment `simctl list devices booted` showed anything Booted,
  `xcrun simctl shutdown all` took every device on the machine. Between 2026-08-27
  and 08-29 that ran 38 times in bursts a minute apart against the simulator a live
  Maestro run was driving: Maestro re-booted the device, memcap shut it down again
  on the next pass, and the user stopped the daemon with `memcap off` at 01:48 on
  the 28th.
- **Devices are now judged one at a time, by their own process.** CoreSimulator
  starts a `launchd_sim` per device whose argv names that device's data directory —
  `.../CoreSimulator/Devices/<UDID>/data/var/run/launchd_bootstrap.plist` — so the
  pid, its idle clock and the UDID `simctl shutdown` takes are one fact. Booted
  devices are enumerated with `simctl list devices booted -j` (with a plain-text
  fallback for a machine without jq, the same shape as docker.sh's), and a device is
  shut down individually only when a `launchd_sim` naming its UDID is in the ready
  set. `shutdown all` is gone. SimulatorTrampoline still counts toward the budget in
  `classify.sh`; it is simply never evidence about a device.
- **Both ways of not knowing mean not touching it.** A booted device whose
  `launchd_sim` is tracked but still inside its grace is held, and the existing
  tier3-holding line names the blocking pid. A booted device with nothing in the
  snapshot mapping to it gets its own line, throttled per UDID, saying memcap will
  not shut down a device it cannot prove is idle.
- **The shutdown's result was never recorded.** The old line was written _before_
  the command ran and named no device, so `actions.log` could not distinguish a
  device memcap took down from one that refused. Each device now gets one line
  either way, carrying the pid that spoke for it and how long that pid had been
  flat, or the rc and the first line of stderr (`simctl shutdown` on an already-down
  device exits 149 with `Unable to shutdown device in current state: Shutdown`).

### The tooling veto flapped, because its window was shorter than one pass

- **`MOBILE_TOOLING_IDLE_SEC` defaulted to 60 seconds, and an enforcement pass takes
  about 64.** A 60-second veto window bought no hysteresis at all: an idle `maestro`
  MCP server that handled a single request switched the tier-3 veto on, and the very
  next pass switched it back off. A state change clears the throttle key, so every
  flap logged — **544 `tier3: declining -- active mobile tooling detected` lines in
  nine days**, alternating minute-to-minute with the hands-on veto. It is also the
  gap the shutdown loop above fired through: on the passes where the veto was off.
- **The rationale behind 60 was wrong too.** It read "a CLI tool going quiet for a
  minute is likelier idle than a simulator is." A Maestro run goes quiet for a minute
  between flows: that quiet minute is the middle of a test suite. The default is
  **300** now, in `memcap init`'s template and in the code's own fallback — which a
  test pins to each other, since they are two copies of one number. An existing
  `memcap.conf` keeps whatever it already says; edit it by hand to pick this up.

### `watch` could not read Docker's settings store, and said nothing about it

- **The drift warning v0.5.1 shipped logged zero times in eight days and roughly
  ten thousand passes**, while `memcap status`, typed in a terminal, showed the
  drift every single time. macOS denies LaunchAgents access to
  `~/Library/Group Containers`: from the daemon, `settings-store.json` tests as
  present and every read of it fails with "Operation not permitted" (reproduced
  with a transient launchd job under the daemon's own PATH). memcap treated that
  permission error exactly as it treats a missing file — a silent "no Docker
  Desktop here" — so the one process that needed the warning was the one process
  structurally unable to produce it. Unreadable is now its own outcome.
- **`status` and `docker apply` run from a terminal, so their reads are cached** in
  `docker-ceiling` in the state directory; `watch` falls back to that and says how
  old the number is rather than stating a ceiling in the present tense on the
  strength of a file it cannot open. With nothing cached it logs, once, that the
  check is blind. **Run `memcap status` once after upgrading** — that is what gives
  the background service a value to work from.
- Both readers now run in-process and leave their diagnosis in globals: capturing
  them through a command substitution was how the difference between "no Docker"
  and "launchd cannot open the file" got thrown away in the first place.

### The "combined over cap" line blamed simulators when Docker was the excess

- **186 of these lines in eight days, every one naming simulators.** On 09-04:
  combined 17.20 GB against a 16 GB cap, agents net of sims 9.45 GB, sims about
  1.15 GB — and Docker holding 6.6 GB against a 4 GB budget, because the ceiling
  had never been applied. Tier 3 could have reclaimed every simulator on the
  machine and it would still have been over the cap, so the line promised a
  reclaim that could not happen and never named the one action that would have
  fixed it (`memcap docker apply`). The overage is now attributed to Docker, to
  the simulators, or to both, and each version promises only what the tier that
  owns it can actually do. The notification matches. An unmanaged Docker
  (`DOCKER_BUDGET_GB=0`) is told to set a budget first, since `docker apply` with
  that config would write a 0 MiB ceiling.

### memcap can say which memcap it is

- **`memcap version` was "unknown command", and nothing memcap wrote carried a
  version at all.** That is the problem when the evidence for a bug is nine days of
  someone else's `actions.log`. `memcap version` (also `--version`, `-v`) prints
  `memcap 0.6.0`; `status`'s header reads `memcap 0.6.0 — <conf>`; the liveness line
  is now `watch: alive (memcap 0.6.0, 48 passes in 3604s -- one every 75s)`.
- A release-guard test extracts the top `## vX.Y.Z` heading from this file and
  asserts it equals `MEMCAP_VERSION`, so a release cannot bump one without the other.

### `status` names the LaunchAgent

- **`brew services info memcap` reports `Running: false` on a perfectly healthy
  install**, because memcap writes and owns its own LaunchAgent and Homebrew only
  tracks plists it created itself. The row now reads
  `LaunchAgent loaded (com.alextitov19.memcap -- memcap's own, not a brew service)`,
  with the label taken from `service.sh` rather than a second copy of the string.

## v0.5.1 — 2026-08-27

Found by auditing 14 days of `actions.log` (4,738 lines) on the author's machine,
two days after v0.5.0 shipped.

### The Docker ceiling was a number nothing checked

- **`DOCKER_BUDGET_GB` is only a request until `memcap docker apply` writes it into
  Docker's own settings, and nothing ever read it back.** memcap has written
  `MemoryMiB` since v0.1.0. On the author's machine the config said 4 GB while
  Docker was enforcing 6144 MiB — `apply` had never actually run there, which the
  absent `settings-store.json.memcap.bak` proves — so every agent budget memcap
  computed on that machine subtracted a ceiling nothing honored, and the machine
  had 2 GB more in play than the config described. `status` rendered it as
  `6.39 GB / 4 GB ceiling`, which reads as Docker overrunning a limit rather than
  as there being no limit at all.
- `status` now reads the enforced value and names both, and `watch` logs the same
  line throttled so the audit trail carries it. **Reported, never silently
  adopted**: 16 GB total with 4 GB for Docker is the policy the user chose, and
  enforcing against 6 instead would be memcap choosing a different one. An
  unreadable settings file, no Docker Desktop, or a `DOCKER_BUDGET_GB` of 0 all
  produce silence — an unknown ceiling must never be reported as a wrong one.
- **`MC_DOCKER_STORE` could not be overridden from the environment.** It was a
  plain assignment, and `bin/memcap` re-sources `docker.sh` on every invocation,
  so an override set beforehand was discarded — the same clobbering AGENTS.md
  records for function stubs. It is `${MC_DOCKER_STORE:-…}` now, like
  `MC_DRY_RUN` on the line below it.

### Tier 2 no longer reclaims what tier 3 is still judging

- **A browser subtree ranks first precisely because it is the biggest thing on the
  machine.** On 2026-08-26 at 21:10:30 tier 2 killed a Playwright driver, a headed
  Chrome carrying `--user-data-dir=…playwright_chromiumdev_profile-…`, and its six
  helpers — eight processes in one event. That Chrome was sim-classified, i.e.
  tier 3's population, which tier 3 only reclaims after measuring CPU flatness
  across the whole grace, and which it holds indefinitely while a live server is
  using it. Tier 2 selects by inference and has no idleness test of any kind, so
  the subtree expansion took a browser out from under every one of those rules.
- A candidate holding a sim that has not cleared `SIM_IDLE_GRACE_SEC` is now
  skipped, and the walk continues **down** the ranking rather than stopping — one
  protected browser must not turn into tier 2 never acting at all. **"Orphaned" is
  not "idle":** a Playwright run whose shell has exited is reparented to init while
  its tests are still running, which is exactly the shape tier 2 ranks first. A sim
  with no readable idle stamp blocks as well, because tier 3 stamps every sim pid
  on every pass — an unstamped one is a pid memcap has not seen yet, not one it has
  watched sit still.

### Logging

- **The one repeating decline v0.4.0 forgot to throttle.** `tier2: over budget but
  no candidate is both older than 300s and measurable` was written on every pass
  that reached it: 79 identical lines in 42 hours, the most frequent line in the
  file, on a machine that is chronically over budget with only young processes
  running. Every other per-pass decline has been throttled since v0.4.0. Its key
  clears the moment tier 2 finds a candidate again, so consecutive quiet spells
  still get their own lines.
- **The liveness line assumed a cadence the machine does not have.** It reported
  `alive (N passes since last mark)`, and the code's own comment called 60 passes
  "a healthy hour". Measured on an awake machine (0 seconds of sleep since boot),
  two consecutive intervals were **73 seconds** apart, not 60: `StartInterval` is
  a request and launchd coalesces timers. Healthy hours in the log ran 46–58
  passes, so anyone reading the count against the documented baseline would
  diagnose a stalling daemon that was working perfectly. The line now states its
  own window — `alive (48 passes in 3604s -- one every 75s)` — so a real stall is
  visible without outside knowledge.
- **The first pass after install logged `alive (1 passes since last mark)`**, with
  "last mark" being the epoch. It now says the clock started, and reports nothing
  it cannot derive.

## v0.5.0 — 2026-08-25

### Notifications carry memcap's own icon

- **Every memcap notification was attributed to Script Editor.** A notification's
  icon belongs to the app that posts it — `display notification` has no icon
  parameter, and a plain `osascript` call is Script Editor as far as Notification
  Center is concerned. So the alert saying memcap had just killed a dev server
  arrived wearing the icon of a text editor the user had never opened, and could
  only be silenced by silencing Script Editor for everything.
- `memcap init` now compiles a bundle for memcap to post through: an AppleScript
  applet carrying an `.icns` rendered from a single emoji (`NOTIFY_ICON`, default
  🧠). Everything it uses ships with macOS — `osacompile`, AppKit through JXA,
  `sips`, `iconutil`, `codesign` — which is what keeps a binary icon out of a repo
  that is otherwise entirely shell. `memcap notify` rebuilds it and posts a sample;
  `NOTIFY_ICON=none` removes it.
- **It is not load-bearing.** Rendering an icon needs a window server, so a machine
  without one (an ssh session, a headless runner) cannot build the bundle at all.
  That path logs why and posts exactly the way every previous version did, and the
  same fallback covers a bundle that exists but will not launch.
- **A failed build is recorded, not rediscovered.** The stamp holds the icon, the
  memcap that built it, and whether the build worked. Inferring "not built" from the
  app's absence would have made a machine that can never render an icon re-attempt a
  ~4-second `osacompile`/`sips`/`codesign` build on all 1,440 passes a day — the
  same shape as the tier 1 root-scan cost fixed in v0.4.0. A changed `NOTIFY_ICON`,
  an upgraded memcap, or `memcap notify` are what ask for a retry.
- Two things found while building it, both of which would have shipped silently:
  `open --args` does not reach an applet's `run` handler on macOS 26 (it surfaces as
  a **modal dialog** the user has to dismiss, which is worse than no notification),
  so the message travels through a file written atomically before `open` is called;
  and `osacompile` ships an `Assets.car` whose `CFBundleIconName` **wins over** the
  `.icns` next to it, so replacing the icon file alone leaves the stock AppleScript
  icon in place and looks exactly like a rendering failure.
- `NOTIFY_ICON` is validated as bytes, not characters: `[[:cntrl:]]` classifies
  ZERO WIDTH JOINER in a UTF-8 locale, so the first version of that check rejected
  👨‍👩‍👧‍👦 — an ordinary icon — as a control character.

### Fixed

- **Two `set -e` fragilities in the same shape as the config bugs.** `have=$(cat
  stamp)` on a missing file, and a bare `PlistBuddy -c Delete` for a key that is not
  there, both fail ordinarily and both would abort a caller running under `set -e`.
  Neither could bite `bin/memcap`, which does not set it; both bit the test suite,
  which does. They are written as `|| have=""` and `|| :` now.

## v0.4.0 — 2026-08-25

A six-agent forensic audit of eleven days of production logs (4,002 lines) found
ten defects. Every one of them failed **silently** — a value became wrong, or a
guard failed open, and nothing anywhere said so. That is the same shape as every
defect in this project's history, so the theme of this release is that memcap
now tells you when it is not doing its job.

### Consent and configuration

- **Answering "no" to enforcement did not stop enforcement.** `memcap init`
  wrote its pause marker with `touch "$(mc_state_dir)/paused"` while only the
  *config* directory had been created. On a fresh install the state directory
  did not exist yet, the `touch` failed silently, and memcap killed processes
  the user had explicitly declined. Only new installs were affected — exactly
  the population that could not tell.
- **`"n"` was not "no".** Every yes/no prompt compared against the literal
  string, so answering `n` to the same question also got you enforcement.
  Anything unrecognised now lands on the side that kills nothing.
- **A one-character config typo silently disabled everything.** The config was
  sourced without checking the result, so on a syntax error the keys *before*
  the error applied and the keys *after* it did not. A stray quote produced an
  87 GB agent budget on a 24 GB machine: nothing was ever over budget, no tier
  ever fired again, and the heartbeat reported it healthy. The file is now
  parsed before it is sourced, so a broken config changes nothing at all, and
  memcap refuses to enforce rather than acting on a policy the user never chose.
- **Every numeric knob failed open.** `[` returns status 2 on a non-integer and
  each gate sat to the left of an `&&`, so a bad value did not fail the gate —
  it removed it. `TIER2_MIN_AGE_SEC="5m"` was not a long minimum age, it was no
  minimum age, and a one-second-old process became a kill target. That is the
  Linux-only-`etimes` bug of v0.1.3 reborn through configuration. Leading zeros
  were also read as octal, so `016` silently meant a 20% tighter budget.

### What gets killed

- **An agent's own tooling was unprotected.** `AGENTPIDS` held only *direct*
  agent-CLI matches; the ancestry propagation fed memory accounting but never
  the protection list, while the dev-server list excluded only the CLI itself.
  Every MCP server, hook, and tool subprocess under a live session was both
  unprotected and classified as a dev server. Six of the ten real tier-2 kills
  in the audited window were `chrome-devtools-mcp` watchdogs running as
  grandchildren of a live `claude` session.
- **`EXTRA_AGENTS` was spliced into a regex unvalidated.** The README advised
  avoiding metacharacters; it is now enforced. An `a|` matched **every process
  on the machine**, making all of them agent-classified and every working
  directory a sweep root; a `foo,bar` matched nothing at all and left the user
  believing they had added protection.

### Measurement

- **A failed measurement silently halved every total.** `top`'s exit status was
  never checked and neither was `mktemp`, so any failure dropped every process
  to `ps` RSS — combined 12.60 GB became 7.28 GB, a 42% under-measurement with
  no log line and nothing in `status`. `SIM_KB` moved the *opposite* way in the
  fallback, so the degraded state was not even a consistent bias.
- **`mc_free_pct` returned a hardcoded 100 when `sysctl` was unavailable**,
  permanently disabling tier 1's low-memory trigger. It now reports 0 and says
  so — the one signal grounded in real physical memory rather than footprint.

### Growth

- **The learned sweep-roots file only ever grew.** Nothing pruned it; this
  machine reached 40 rows and every new project added one permanently. Tier 1
  costs roughly 5 ms per (orphan × root) pair, so 388 orphans against 40 roots
  already exceeded the 60-second service interval. Roots are now bounded by
  `ROOT_TTL_DAYS` and `ROOT_MAX`; a wrongly-dropped root is re-registered within
  one pass, which is what makes a short TTL safe.

### The enforcement tiers

- **Tier 3 had never fired — not once, in the tool's entire life.** Eleven days of
  logs: 1,973 declines, zero reclaims. The cause was `kill -0` used as a liveness
  test, which fails for **EPERM** ("alive, but not yours to signal") exactly as it
  does for **ESRCH** ("dead"). Root-owned `simdiskimaged` is listed in the simulator
  pattern and appears on any Mac with Xcode installed, so every pass declared it
  dead, deleted its idle stamp, re-saw it as never-tracked, and returned — through
  the one unlogged return in the function. A process that is not even in the
  reclaim pattern, and could never have been killed if selected, blocked the tier
  permanently. It predates both the v0.3.0 and v0.3.1 "fixes", which addressed the
  vetoes standing in front of it.

- **The invariant that came out of unblocking it: memcap never reaps a process its
  own vetoes count as evidence of active work.** With tier 3 working, the first
  thing it selected was a Chrome browser held open by a live `@playwright/mcp`
  server under an active session — idle *by design* between requests — while the
  same pass counted the author's `maestro` servers as proof that mobile work was
  happening. A resource cannot be both proof someone is working and reclaimable
  garbage. Enforced once at the kill choke point, over whatever the veto matchers
  return, rather than by excluding one vendor from one pattern.

- **Simulator protection now has three bands**, because the populations genuinely
  differ: a resource held open by a live server under an agent session is exempt
  while its holder lives; a session-owned process that is *not* server-held gets a
  longer clock (`TIER3_AGENT_TREE_GRACE_SEC`, default 1800) rather than immunity;
  anything unowned keeps the ordinary grace. Every exclusion is logged with its
  reason — the difference between this and the original bug is not that tier 3
  reclaims more, but that when it reclaims nothing it says why.

- **Tier 2 killed live work, reclaimed nothing, and misreported it.** Ten kills in
  the audited window recovered 126 MB against overages of 0.5–6 GB. It never
  consulted the mobile vetoes, so it killed the Metro bundler feeding a simulator
  one second after tier 3 had declined to touch that simulator because the
  developer was driving it. It ranked candidates by their own footprint and then
  killed the whole subtree, so a fat worker outranked the server that owned the
  worker pool — memcap fighting a supervisor that respawns. It now consults both
  vetoes, ranks by subtree total, protects an agent's whole tree, names what it
  actually killed, and can be switched off with `TIER2_ENABLED`.

- **Tier 1 had no age gate**, while tier 2's documentation claimed an age gate
  "keeps builds from ever being the victim" — a guarantee that existed in only one
  of the two tiers that can kill a build. `npm run build &` reparented to init was
  an instant target. `TIER1_MIN_AGE_SEC` (default 300) closes it. Still open, and
  documented rather than papered over: `ppid == 1` is also what `nohup` and
  `disown` produce, so a deliberately daemonized production server is
  indistinguishable from a leak — a real kill of `npm exec next start -p 3100`
  is the counter-example, and an age gate does not help because such a server is
  old by definition.

- **The 2-second SIGTERM→SIGKILL window killed recycled pids.** The recheck asked
  "is *a* process alive at this number", not "is it the one I signalled", and fed
  the survivors to SIGKILL **without passing back through the protection filter**.
  At 135 pids allocated per 2-second window, a 388-orphan sweep carries roughly
  half an expected wrong-process kill. Identity is now confirmed by start time and
  argv, and the filter is re-applied before the kill.

- **Tier 1 was on course to exceed its own service interval again.** Three process
  forks remained inside the per-(orphan × root) loop, including canonicalizing the
  same path twice. At 40 roots, 388 orphans measured 79 seconds against a
  60-second interval. The inner loop now forks zero times.

- **Kill records were truncated where they became informative.** All 376 records in
  the audited window collapse to four distinct strings, because the `node` binary
  path plus the `--require` shim consumed the entire 160-character budget and the
  script actually executed always fell past the cut.

- **An hourly liveness line is back.** The 28-hour outage was detectable only
  because a line happened to fire every 30 minutes; v0.3.0 removed it, so the same
  outage today would be indistinguishable from a quiet week.

### Knowing whether it works

- **`status` reported activity, not outcome.** It printed a heartbeat whether or
  not the pass had enforced anything, so the states where memcap deliberately
  refuses — an unparseable config, a Docker ceiling leaving agents no budget —
  stamped the heartbeat and were certified healthy. The heartbeat added in
  v0.1.4 to make non-enforcement visible had become what concealed it. `status`
  now reports the outcome, the measurement basis, and whether the LaunchAgent is
  actually loaded, each with its own remedy.
- **Freshness never proved the service ran the pass** — any manual
  `memcap watch` stamps it. `status` now asks `launchctl` directly, and reports
  `unknown` rather than `no` when it cannot ask.
- **A sleeping laptop produced false alarms.** Staleness is judged on a
  monotonic clock that does not advance during sleep, so a closed lid no longer
  reads as a dead daemon. False alarms are how a real one gets ignored.
- **`memcap off` and `on` wrote nothing to the log**, so a paused week and a
  dead week were indistinguishable in the audit trail forever. Both are logged,
  and `status` says how long it has been paused.


## v0.3.1 — 2026-08-19

### Fixed

- **v0.3.0 swapped one permanent tier-3 veto for another.** The
  active-mobile-tooling check added in v0.3.0 matched a tool's mere
  existence, not it actively driving a simulator — and `maestro`'s own MCP
  server idles for days between requests as one of the author's registered
  MCP servers, matching the maestro pattern and permanently vetoing tier 3 on
  the exact machine whose 1,643 dead declines motivated the whole change.
  Fixed post-ship, on the real process, not caught by the tests that shipped
  with it.

  Active mobile tooling (`maestro`, `xcodebuild`, `expo`, `react-native`,
  `detox`) is now CPU-checked the same way simulators are, reusing
  `mc_cputime_secs` and the same stamp-file shape in a new `tooling-idle/`
  directory: a pid matching one of these patterns that stays CPU-flat for
  `MOBILE_TOOLING_IDLE_SEC` (default 60s, shorter than `SIM_IDLE_GRACE_SEC`
  since a quiet CLI tool or server is more likely genuinely idle than a quiet
  simulator) no longer vetoes; real work — an actual `maestro` flow,
  `xcodebuild`, or `detox` run — keeps vetoing for as long as it burns CPU.
  Hands-on mobile work (Xcode, Android Studio, Simulator.app) is unchanged —
  a plain presence check, since those are GUI apps a human has open and CPU
  is not the signal there.

  New tests assert against `mc_reap_sims` itself with a long-lived,
  CPU-idle, maestro-shaped fixture — not against the matcher in isolation,
  which is exactly what let the first version ship with this bug still live.

- **A real bug found while building the above:** `pgrep -f` returns multiple
  matches newline-separated, not space-separated. When two processes matched
  the same active-mobile-tooling pattern at once (a test fixture alongside
  the real `maestro` MCP server), the newline between their pids broke the
  prune step's `case " $pids " in *" $pid "*)` presence check — which
  pattern-matches on a literal space boundary — so a still-alive, still-
  matching pid's stamp was wrongly deleted and recreated on every single
  pass, discarding its accumulated idle history each time. Fixed by
  normalizing `pgrep`'s output to spaces before concatenating.

### Configuration

- Added `MOBILE_TOOLING_IDLE_SEC` (default `60`).

## v0.3.0 — 2026-08-19

### Changed

- **Tier 3 no longer declines just because an agent session is alive.**
  Production data: it fired zero times in 1,643 real opportunities on a
  machine that always has a session open, while `rm -rf` on every decline
  reset every tracked simulator's idle clock to zero on every single pass —
  tier 3 could not fire even in principle, despite simulator memory counting
  against the budget the whole time. "An agent session is alive" was chosen
  as a conservative stand-in because a simulator can't be attributed to a
  session by process tree, not because it was ever the right proxy for "a
  simulator is in use."

  Idleness is now measured directly: each tracked simulator's own accumulated
  CPU time is sampled (`ps -o time=`, parsed by a new `mc_cputime_secs` —
  confirmed empirically that this is a different shape than `etime`, never
  rolling into an hour/day segment). A pid whose CPU stays flat for the full
  `SIM_IDLE_GRACE_SEC` grace period is reclaimed; real CPU work resets its
  clock. Two vetoes still block a reap outright, independent of CPU
  idleness — active mobile tooling actually driving a simulator (`maestro`,
  `xcodebuild`, `expo`, `react-native`, `detox`) and hands-on mobile work
  (Xcode, Android Studio, or Simulator.app open, unchanged from before) — and
  neither a veto nor an unready pass erases accumulated idle history anymore;
  only an actual reclaim does. A reclaim now logs which pid, how long it was
  idle, and its flat CPU baseline, for auditing after the fact.

  `TIER3_REQUIRE_NO_SESSION=1` restores the original, maximally conservative
  behavior for anyone who wants it back.

### Fixed

- **Discovered while building the above:** two of tier 3's mobile-tooling
  patterns depend entirely on the real process table, with no way to make
  them deterministic in an environment that happens to run one of those
  processes for an unrelated reason — confirmed on this very development
  machine, whose maestro MCP server (`java ... maestro.cli.AppKt mcp`,
  unrelated to any simulator) matched the maestro veto pattern and made the
  test suite's result depend on which MCP servers or IDEs happen to be
  running. Added `MC_ACTIVE_MOBILE_TOOLING`/`MC_HANDS_ON_MOBILE` escape
  hatches (same pattern as `MC_DOCKER_RUNTIME`), forced to a known value for
  every test.

### Configuration

- Added `SIM_ACTIVE_CPU_SEC` (default `2`) and `TIER3_REQUIRE_NO_SESSION`
  (default `0`).

## v0.2.0 — 2026-08-18

### Changed

- **memcap now installs and owns its own LaunchAgent**, instead of Homebrew
  managing it via the formula's `service do` block. `brew upgrade` was found to
  _remove_ that plist outright, not just unload it — confirmed twice, including
  a 28-hour outage on the author's own machine with no paused marker, no crash
  evidence, and uptime of 5 days. Because `RunAtLoad` lived in a plist that no
  longer existed, the service did not come back at login either. The v0.1.4
  heartbeat reported this correctly, which is how it was caught, but reporting
  a dead daemon isn't the same as having one.

  New commands: `memcap service install`, `memcap service uninstall`, `memcap
service status`. `memcap init` installs the service as part of setup;
  `memcap uninstall` removes it. The label (`com.alextitov19.memcap`) is never
  `homebrew.mxcl.memcap` — a plist Homebrew never created is one it cannot
  delete. An existing Homebrew-owned plist from an older install is detected
  and migrated away from automatically (`brew services stop`, then the plist
  removed) so a machine is never left with both agents loaded racing separate
  `watch` passes every 60 seconds. Plist content is unchanged: `RunAtLoad`
  true, `StartInterval` 60, and `ProgramArguments` resolved via `brew --prefix`
  at write time so it works on both Apple Silicon and Intel and always points
  at the stable `opt/memcap` symlink, never a versioned Cellar path.

  `status`'s stale/absent remedy text now says `memcap service install`
  instead of `brew services start` accordingly.

  `brew services start/stop memcap` is no longer part of the supported
  workflow — the formula's `service do` block is being dropped in the tap.

### Fixed

- **`memcap uninstall` called `brew services stop memcap` for real,
  unsandboxed, in every test run.** `tests/uninstall.bats` invokes the real
  `bin/memcap uninstall`, so every `bats tests/` run on a machine with memcap
  actually installed via Homebrew was quietly attempting to stop that
  machine's real enforcement. Both `launchctl` and `brew` invocations are now
  routed through `MC_LAUNCHCTL_BIN`/`MC_BREW_BIN` (same escape-hatch pattern as
  `MC_DOCKER_RUNTIME`), stubbed to fake binaries for every test via
  `MEMCAP_LAUNCHAGENT_DIR` and the test harness, not only the new
  service-specific tests.

## v0.1.4 — 2026-08-18

### Added

- **`status` now reports whether memcap is actually running.** A stopped
  service looked exactly like a quiet one: `status` still printed a full
  budget, nothing errored, and no notification fired. The author's own machine
  went 28 hours without enforcement before this was noticed, by chance — no
  paused marker, no crash evidence, nothing but a stale `actions.log`.

  `watch` now stamps `last-pass` with epoch seconds on every pass it
  completes, including the paused and misconfigured-budget early returns, and
  `status` renders how long ago that was: fresh ("`12s ago`"), stale past
  `STALE_PASS_SEC` (default 300) with the exact command to restart the
  service, or never run since install. A paused service with a fresh
  heartbeat still reads as paused, not dead. `status`'s exit code is
  unchanged either way — it stays an informational command.

### Configuration

- Added `STALE_PASS_SEC` (default `300`).

## v0.1.3 — 2026-08-17

**The first release with passing CI.** Use this one. Every earlier tag ships at
least one of the defects below.

### Fixed

- **The test suite assumed Docker Desktop was installed**, so CI failed on every
  run from v0.1.0 onward. GitHub's macOS runners have no Docker runtime, so
  `mc_docker_runtime` returned `none`, `mc_docker_apply` took its
  unsupported-runtime branch, and seven tests failed. The production code was
  correct throughout — that branch is the documented degradation path. Added
  `MC_DOCKER_RUNTIME` as an escape hatch so runtime-dependent tests are
  deterministic anywhere.

- **Sweep roots were silently rejected under a symlinked `$HOME`.**
  `mc_root_is_safe` and `mc_record_root` compared canonicalized paths against
  the raw `$HOME`. `/tmp` and `/var` are symlinks on macOS, so under such a home
  every candidate resolved to `/private/...`, matched neither pattern, and was
  discarded as "not under HOME" — leaving tier 1 with no roots at all and no
  error anywhere.

- **Tier 1 missed orphans behind a symlinked project path.** Roots are recorded
  from the kernel's resolved cwd (always canonical) but were matched against
  `ps -o command=` (argv, whatever string launched the process). For a project
  behind a symlink those never match. `mc_reap_orphans` now also compares the
  orphan's own canonical cwd against the canonical root.

  This widens tier 1 on every machine, not only symlinked ones: an orphan
  launched with a relative path (`node server.js`) has argv that contains no
  absolute root, so it was previously spared everywhere. Such processes are now
  reaped. They still must clear `ppid == 1`, the dev-server pattern, and a
  learned safe root.

- **`mc_pid_cwd` ran once per (orphan × root).** It spawns `lsof`, measured at
  35.8 ms. At 388 orphans against 12 recorded roots — the leak that motivated
  this tool — that is 4,656 spawns and a 167-second pass against a 60-second
  service interval, i.e. slowest exactly when the leak is worst. Both per-pid
  facts are now resolved once per pid, with the cwd lookup lazy so the common
  case pays nothing.

### Testing

- Replaced fixed `sleep 0.2` calls after spawning fixtures with `wait_spawned`,
  which polls until the process is visible to `ps`. Between fork and exec, `ps`
  still reports the forking shell, so tests raced under full-suite load: one
  tier-3 test failed one run in three while passing 6/6 in isolation.
- The suite must now pass with no Docker Desktop installed. Verify with
  `env HOME="$(mktemp -d)" bats tests/`.

### Documentation

- README rewritten for readers who have not seen the tool before: badges, the
  incident that motivated it, sample `memcap status` output, requirements, and
  the budget proposed for 16/24/32/64 GB machines.
- Added `CONTRIBUTING.md`, `AGENTS.md` (for coding agents working in this
  repository), issue forms, a pull-request template, and `CODEOWNERS`.

### Packaging

- The formula now uses a release tarball with a `sha256` instead of the git
  download strategy. That strategy existed only because the repository was
  private and Homebrew fetches tarball URLs with unauthenticated curl.

## v0.1.2 and earlier — withdrawn

v0.1.0 (2026-08-15), v0.1.1, and v0.1.2 (both 2026-08-16) were tagged during
development, never had a passing CI run, and carry the defects listed above.
They are left in place so the history is honest, but no one should install them.
