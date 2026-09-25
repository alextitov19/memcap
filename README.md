# memcap

[![CI](https://github.com/alextitov19/memcap/actions/workflows/ci.yml/badge.svg)](https://github.com/alextitov19/memcap/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](#requirements)

**Keep AI coding agents from eating your Mac.**

memcap is a macOS command-line tool and background service that keeps AI coding
agents, and the processes they spawn, inside a memory budget. It measures actual
physical footprint on a roughly 60-second cycle and, when agents drift over budget,
reaps processes in three narrowly-scoped tiers rather than a blanket sweep. It also
sets Docker Desktop's VM memory as a hard ceiling, since that is the one limit macOS
will actually enforce; everything else it does is a soft policy it polices itself.

## Why this exists

Agents leak processes. A session that restarts a dev server on every retry leaves
the old ones orphaned; simulators and Playwright browsers outlive a crashed test
run; Docker Desktop's VM balloons and never gives the memory back.

memcap was written after a 24 GB Mac hard-shut-down mid-workday with **388 orphaned
`tsx` dev servers** holding 2.9 GB, Docker's VM ceiling set to 12 GB — half the
machine — and 20.3 GB of 21.5 GB swap in use. Nothing had crashed. Two coding
sessions had simply been left to accumulate.

The aim is to leave room for the rest of your work while keeping agents moving.
The shared budget is a soft policy, not a hard guarantee against memory exhaustion.

```
$ memcap status
memcap 0.13.0 — /Users/you/.config/memcap/memcap.conf

  agents + everything they spawn   6.93 GB / shares 20 GB total (13.53 GB pool after measured Docker use)
    of which leaked/orphaned       0.00 GB
    of which sims/playwright       0.93 GB
    net of sims (drives tier 2)    6.00 GB
  docker VM + helpers              6.47 GB / 6 GB ceiling
  ---------------------------------------------------------
  combined                        13.40 GB / 20 GB budget (shared)
  system memory available          25%
  memory measured by               top footprint (569 processes)
  last enforcement pass            3s ago
  last pass outcome                enforced
  background service               LaunchAgent loaded (com.alextitov19.memcap -- memcap's own, not a brew service)
```

The bottom four rows answer a question the earlier versions could not. A stopped
memcap used to be indistinguishable from a quiet one: `status` printed a budget,
nothing errored, and no notification fired, so a machine that had not enforced
anything in 28 hours looked exactly like one with nothing to do. Worse, the
states where memcap _refuses_ to act — an unparseable config, a Docker ceiling
that leaves agents no budget — still stamped the heartbeat, so the freshness
indicator actively vouched for them.

Each of those now has its own row and its own remedy, because the fixes differ:
a missing LaunchAgent is not a stale heartbeat, and `MC_NO_TOP=1` set on purpose
is not a `top` that failed.

## Requirements

- **macOS** (Apple Silicon or Intel). Linux is explicitly out of scope — the
  mechanics differ entirely, and a port would share almost no code.
- **Homebrew**, for install and for the background service.
- **`jq`**, installed automatically as a formula dependency.
- **Docker Desktop is optional.** Without it, memcap skips the Docker module and
  budgets agents only. OrbStack, Colima, and Podman are measured but cannot be
  capped — no VM ceiling exists to set.

The watchdog uses system `bash` (3.2); no newer shell is needed. The optional
workload queue additionally requires **Python 3.9+** (`brew install python`),
using only the standard library.

## Agent reports to GitHub (v0.14.0)

Agents can report suspected memcap defects with `memcap report CATEGORY`. Setup
offers a **one-time opt-in**, defaulting to no. Existing installations remain
opted out until the user enables reporting:

```sh
gh auth login                  # authenticate as yourself, if needed
memcap report enable           # consent to public reports in alextitov19/memcap
memcap report status
memcap report queue-lock       # create a report, or contribute to a matching issue
memcap report disable          # return to local drafts only
```

Categories: `queue-lock`, `measurement`, `integration`, `queue-stall`,
`unexpected-termination`, `missing-task-poll`, `lightweight-queued`, and `polling-overhead`.
Agents **must report each observed memcap-caused productivity incident once**, without
waiting for a user reminder, a failed command or proof of root cause:

- `lightweight-queued`: ordinary repository searches (`rg`/`grep`), file reads or
  status checks entered the workload queue, including switching tools to avoid it.
- `queue-stall`: excessive waits, starvation, or blocked independent work.
- `polling-overhead`: repeated polling, output-file reads or stop-hook loops waste
  time or agent usage.

**A command that eventually succeeds can still have a reportable latency regression.**
Do not dismiss delayed lightweight inspection as normal waiting merely because
memcap has a capacity reason. Waiting for genuinely heavy work alone, or a simulator
boot timeout alone, does not establish a defect. Reports describe suspected issues.
When the elapsed wait is known, include it (whole seconds, up to seven days):

```sh
memcap report lightweight-queued --context repository-search --wait-seconds 120
```

Optional `--context` distinguishes `repository-search`, `file-read`, `status-check`,
`ssm-control`, `remote-control`, `wait-command`, `stop-hook`, and `heavy-work`.
Use `--symptom` for a more precise fixed description, for example:

```sh
memcap report integration --context stop-hook --symptom persistent-work-pending
memcap report lightweight-queued --context file-read --symptom inspection-wrapped
```

`memcap report --help` lists the allowed symptoms. These distinguish inspection
wrappers, remote calls, invalid wait targets, persistent jobs, missing native waits,
repeated guidance, copied diagnostics, short-job overhead, slow admission,
degraded measurement and oversized processes without sending private text.
Different symptoms get distinct report identities. A `deduplicated` result with
an issue URL means the new snapshot was retained locally; it was **not appended**
to that issue. `commented` means a comment was posted, and `published` means a new
issue was created. Version and timestamp describe reporting time, which may be
later than the incident; report promptly rather than attributing old incidents
to a newly installed runner.
Deduplication includes this context, so a queued read and a queued wait command
can produce separate reports even in the same category/version. It accepts only
these fixed values, never arbitrary notes or commands. Existing consent and
uncertain-submission protections still apply. Memcap imposes no publication quota
or retry cooldown.

Omit the duration when unknown. It is labeled agent-reported, not independently
timed by memcap. Do not report again on every poll of the same incident; keep the
returned URL and continue the existing task. Deduplication still applies, so another
invocation does not guarantee another GitHub post. Authentication, GitHub service limits, network errors and uncertain
submissions can still defer publishing.
The installed Claude/Codex guidance explains this workflow; refresh it
with `memcap integrate` and restart sessions to reload Markdown.

Without consent—or with `--dry-run`—the command saves a private Markdown draft
under `~/.local/state/memcap/reports/` and makes no GitHub requests. After opt-in,
it uses your own `gh` login to publish to this repository. `gh` is optional; a
missing login, unavailable network or API error leaves the draft locally.
Consent is stored separately in `~/.config/memcap/reporting.json`; upgrades and
repeated setup preserve it. Agents must not opt in on a user's behalf.

Public reports contain the memcap version, a fixed error category, UTC report
time and allowlisted numeric diagnostics:

- **Machine:** physical RAM, logical/physical CPU counts, architecture, macOS version.
- **Current state:** measured tracked memory and cap, available memory, pressure,
  Docker footprint, agents including simulators/browsers and their simulator/browser
  subtotal, swap used, home-volume free space, 1/5/15-minute load, enforcement pause state.
- **Queue:** configured slots/workers/headroom/pressure policy, stored waiting/running
  counts, distinct session counts, oldest wait/runtime and original requested memory.
- **Blockers:** counts of last recorded budget, headroom, slot, pressure/measurement
  and fairness decisions, plus unknown decisions and their evidence age.
- **Observed delay:** optional agent-reported wait duration, useful after a delayed
  command has finished and its queue record has gone.

No hostnames, serial numbers, project paths,
commands, process identities, source, raw logs or tool output are uploaded.
Snapshots describe report time, not necessarily failure time; registry counts
are not proof that jobs are still alive. Original requests are not effective
reservations after adjustment, and simulator/browser usage is already included
in agent usage. Missing/invalid probes are omitted, not reported as healthy zeros.
Queue decisions come from runners started after this update; older runners have
unknown blockers. This is a current snapshot plus last decisions, not a historical
pressure trace or a measurement of completed-job throughput. Reports are observations for triage,
not automatic root-cause findings. Do not append unreviewed raw diagnostics.
Agents should use `queue-stall` for suspected excessive throttling or starvation.

Matching category/version/context reports share an issue; a different installation can
add one sanitized comment. Local locking prevents competing sessions from posting
duplicates. There is no memcap daily publication cap or retry cooldown, including
for installations whose existing ledger reached the former five-report allowance.
The rolling attempt history is retained for diagnostics only. An uncertain POST
is recorded before submission and is never blindly repeated. GitHub search indexing and
simultaneous first reports from different Macs can still produce duplicates;
cross-machine deduplication is best-effort. The reporter never edits or closes
existing issues, resets devices, changes budgets, or resumes enforcement.

Reporting runs outside the workload queue and reads its registry without a lock.
The footprint probe has a two-second timeout and each of three lightweight system
probes has a half-second timeout; GitHub requests share an eight-second deadline. A deferred report must
not turn into another agent retry loop. Maintainers and other contributors can
triage issues and propose fixes through the normal pull-request workflow.

## One shared budget (v0.13.0)

`TOTAL_BUDGET_GB` is shared by **measured Docker memory + agents and their children
+ simulators/browsers**. With a 20 GB total, Docker using 4 GB and agents using
8 GB consumes 12 GB, regardless of Docker's configured VM ceiling. Unused Docker
capacity is not reserved or subtracted from an agent allowance.

The queue already uses the combined total, current pressure, headroom and running
job reservations. The watchdog now follows measured use too: agent cleanup is not
triggered merely because agents exceeded `total minus Docker ceiling`. Simulator-only
excess stays with simulator cleanup; Docker alone over the total does not justify
killing unrelated agent work. Status and notifications describe the shared pool.

`BUDGET_MODE=shared` is the default, including existing installs whose config omits
it. Set `BUDGET_MODE=split` only to retain the legacy watchdog's fixed agent slice.
`DOCKER_BUDGET_GB` still requests a separate Docker VM maximum; changing/applying
that maximum may require a Docker restart. Profiles choose that VM maximum, not a
reservation in the shared pool. No Docker setting is changed by this upgrade.

The total is an admission/cleanup budget, **not an OS-level guarantee** that all
running work stays below it at every instant. Existing jobs and unmanaged Docker
activity can grow between samples; protected active work cannot always be safely
reclaimed. Red-pressure admission checks, host headroom and per-job protections
continue to apply. A hard aggregate limit would require stronger OS/VM isolation.

## Queue recovery and portable waiting (v0.12.1)

Some Claude builds do not expose `TaskOutput`. Use the existing memcap job ID
shown in queue output with `memcap wait JOB_ID --timeout 60` instead. This is a
bounded, read-only local wait: it creates no workload or reservation and takes
no queue lock. Repeat while it reports pending; once it leaves the queue, read
the original task's final output and exit status. Leaving the queue does not prove
success. Exit 0 from `wait` means the observation succeeded, not that the task did.

Do not launch “drain tick” loops or poll for the absence of an old “queued” line
in an append-only output file. Known waiting-only scripts are rejected before
submission, with the supported wait command in the explanation. The watchdog
retires verified queued drain ticks older than a minute and orphaned waiting-only
shell groups older than five minutes, including jobs from older sessions. Cleanup
requires registered identities, current ownership and exact script/child checks;
unknown work is retained. All signals use the normal kill choke point, spare agent
CLIs and memcap's ancestry, and leave reservations until processes actually exit.
Up to eight pollers are considered per watchdog pass; notices explain the cleanup.

Waiters share a memory sample for at most two seconds rather than each repeating
expensive measurement under the queue lock. Reservations are reconciled under the
lock on every admission, config changes invalidate the sample, and current host
pressure is checked again before launch. A queue-lock timeout is a coordination
failure, not proof of insufficient RAM. `doctor` checks integration health, not
admission capacity; use the runner's reason. Docker's separate ceiling does not
by itself explain workload admission.

## Agent setup and upgrades (v0.12.0)

`memcap init` detects Claude and Codex profiles and offers to install their
integrations. Installation requires an explicit yes; unattended/EOF setup leaves
agent files unchanged. Use `init --integrate` to opt in explicitly or
`init --no-integrations` to skip the offer. Existing users can install or refresh
agent integration without rerunning budget setup:

```bash
brew upgrade alextitov19/memcap/memcap
memcap integrate
memcap doctor
```

`integrate` installs queue and lifecycle hooks and a small managed guidance block
in global `CLAUDE.md` / `AGENTS.md`. Hooks supply current operational instructions
and contextual diagnostics; the Markdown block explains how to use them without
copying memory limits. The default selection includes detected `~/.claude`,
`~/.claude-*` directories containing settings or instructions, `CLAUDE_CONFIG_DIR`,
and `CODEX_HOME` (or `~/.codex`). Choose only the profiles you want when needed:

```bash
memcap integrate --claude
memcap integrate --codex
memcap integrate --claude-dir ~/.claude-personal
memcap integrate --claude-dir /path/to/work-profile --codex-dir /path/to/codex-home
memcap doctor --claude
```

The same profile selectors work for `doctor`. Explicit directory flags select
those profiles; `--claude` includes discovered Claude profiles and `--codex`
selects the configured Codex home. Missing explicit profiles can be created.

The installer preserves unrelated hooks, their group/handler positions (used by
Codex trust keys), permissions and user-written Markdown. Ambiguous mixed groups
that would shift unrelated hook positions are refused before writing; separate
the stale memcap entries and review trust before retrying.
It replaces recognized memcap hook entries rather than accumulating duplicates,
keeps symlinks intact, and validates all selected files before writing any config.
Changed files get private sibling backups named `.FILENAME.memcap-backup-ID`;
partial write failures restore unchanged originals when possible and report any
file needing manual recovery. Repeating the same installation changes nothing.
Malformed JSON, duplicate JSON keys, ambiguous managed Markdown markers and
concurrent edits are reported instead of overwritten. Restore a backup only after
checking for newer edits; it contains the original full file, including settings.

Homebrew hooks use the stable `opt/memcap/bin/memcap` path, so keg upgrades do not
leave stale paths behind. Each profile's `.memcap-integration.json` records the
installed integration schema and memcap version. Run `integrate` after upgrading;
`doctor` reports missing or outdated integration files, hooks and timeouts.
Upgrades and integration do not alter memory caps, job slots, worker limits or
agent permissions, and the installer never approves its own Codex hook trust.

**Activation:** existing sessions whose hooks already use the stable Homebrew
path execute the upgraded classifier on their next command. The feedback hook
refreshes current guidance once per session/version at the next `PreToolUse`,
without a new prompt or Markdown reload. Newly launched cached automatic wrappers
also recheck lightweight classification before reserving. Changed hook definitions
still require reloading/restarting the agent; an upgrade cannot replace code
already loaded in a running queue supervisor. In Codex, review and trust the memcap hooks in `/hooks` when requested.
A Mac reboot is not needed. Existing runners retain their loaded scheduler code
until they finish; keep polling those tasks rather than submitting replacements.

**What doctor verifies:** hook definitions, stable paths, Stop timeouts, managed
guidance, version metadata and globally disabled Claude hooks. If the selected
Codex home's local app-server daemon is running, a bounded read-only probe checks
that it reports the exact memcap hooks enabled and trusted. The probe starts no
agent and changes no trust. An absent/unsupported daemon or `--no-runtime` reports
trust as **unverified** and directs you to `/hooks`; it never assumes activation
from files alone. Doctor exits 1 for warnings/unverified checks and 0 when the
checks it can perform pass. Project/managed overrides and already-open session
reload state are outside those global-profile checks.

See the upstream [Claude hook settings](https://code.claude.com/docs/en/hooks)
and [Codex hook trust documentation](https://learn.chatgpt.com/docs/hooks) for
agent-specific activation and override behavior.

## Faster admission in v0.11.0

Automatic reservations adapt to measured usage after startup, admissions rotate
between sessions, and the watchdog cancels confirmed stuck standalone simulator
boot attempts. Bounded file-reading pipelines stay outside the expensive-work
queue. Stop hooks use `feedback --wait` with `timeout: 75` and wait locally for up
to a minute instead of repeatedly waking the model. `memcap integrate` installs
these entries for both new and previously configured profiles.

Green or yellow pressure does not guarantee admission: measured usage, outstanding
reservations, configured headroom and job slots still matter. The aim is more useful
concurrency, not a promise of zero waits or zero pressure spikes. The boot deadline
releases a reservation only after its managed processes exit; it does not reset a
simulator or prove that app tests ran. See [CHANGELOG.md](CHANGELOG.md) for details.

## Queue expensive agent work before it starts

The cleanup budget cannot force protected active work back under the cap. By
default on an existing configuration without `QUEUE_POLICY`, the optional strict
queue admits at most **two finite jobs** across participating
sessions, using an automatic **2 GB startup estimate per job**. Other commands
wait without starting.
Admission defaults to normal (green) macOS pressure and **3 GB of host headroom**
after reservations. Set `QUEUE_MAX_PRESSURE=yellow` to also admit work at warning
pressure; critical (red) and unknown pressure always block admission. The combined
budget and headroom gates still apply, so a green graph alone does not guarantee
that another job fits. Docker, simulators and agent workloads count against the
combined budget; ordinary applications affect the host-headroom gate.

```bash
memcap run -- npm test
memcap run --memory 3 --wait 600 -- go test ./...
memcap run --resource dev -- npm run dev
memcap queue
memcap queue --json
memcap wait --session --timeout 60
```


`wait --session` is a read-only, bounded wait on finite jobs belonging to the
calling agent process. It does not acquire the queue lock, create a reservation,
or run a shell pipeline to discover IDs. If several conversations share one agent
process, use `wait JOB_ID` to select an individual task. Outside a recognized
Claude/Codex ancestry, use an explicit ID. Waiting is not proof of task success;
read the original task's output and exit status. If an owned background task is
obsolete because its result is no longer needed, cancel that specific task using
the agent tool's native cancellation, then read its final status. Still-needed
work must keep waiting; never cancel another session's work.

Multiline read/search pipelines, the bounded literal-file inspection loop
(`for f in files; do [ -f $f ] && { reads; }; done`, at most 16 files),
literal locale/AWS environment prefixes,
`git -C` inspection, bounded `sleep` delays, and SSM send/get/list/wait control calls no longer reserve
build slots. This classification concerns local memory only; normal command
permissions still apply. The SSM path also accepts a literal `$(cat /tmp/id)`
command-ID read; that exception does not extend to arbitrary consumers or scripts.
Unknown shell expansion, execution helpers, mixed
read/build scripts, AWS transfers and interactive SSM sessions remain managed.
Explicit memory/resource reservations remain managed too.

`--memory` reserves GB, not a hard memory limit. Each workload is charged the
greater of its effective reservation and observed footprint, without double-counting
memory already in the combined budget. Unknown measurements admit nothing.

Automatic job estimates reserve the full startup allowance for 30 seconds. After
that, complete process measurements can reduce unused reservations to the greater
of 512 MB or 125% of the observed peak. In adaptive mode, peaks older than a minute
can retire after more than 60 seconds of complete, fresh observations with no gap
above five seconds. The lifetime peak remains available for learning. Strict mode
retains the lifetime peak. Growth increases reservations immediately; incomplete
or stale samples retain the previous effective allowance. Actual usage above the
estimate is always counted.
Explicit `--memory` reservations and uncertain or orphaned groups retain their
full allowance. This permits more concurrency without raising your configured
memory cap or admitting at red pressure; later allocation bursts can still raise
pressure, so this is a throughput tradeoff, not a guarantee against overload.

Since v0.16.3, headroom queue notices show available memory, unused running
reservations, the next request and the emergency margin. Docker's ceiling is never
a reservation. Regex end anchors, read-only regex ranges in `sed`, `pgrep`, `tr`
pipelines and redirected `memcap wait` commands use lightweight inspection.
Legacy managed waits exclude their own verified process ancestry when observing
pending jobs; observing completion still does not establish workload success.

Local queue events now carry a random numeric job reference to correlate admission
and completion, plus `signal` and `completion_kind` (1=application exit, 2=signal,
3=supervisor cancellation). A signal does not identify its sender. Stalled events
include headroom arithmetic and `reason_code`: 0 unknown, 1 budget, 2 headroom,
3 slots, 4 pressure/measurement, 5 invalid measurement, 6 fairness, 7 startup,
8 stabilization, 9 paging. Public reports distinguish the independent reporting
probe from the queue sample and include effective reservation totals when known.
See [the September 24 feedback audit](docs/feedback-2026-09-24.md) for coverage
and the release's issue dispositions.

Reservations larger than the entire budget fail immediately. Waiting defaults
to 1,800 seconds, then exits **75** without launching. Notices go to stderr;
the command retains stdin, stdout, stderr and exit status. `--wait-forever` polls
until admission or cancellation without a queue deadline. Admissions rotate
between sessions, across finite jobs and persistent resources. A session that
just started work goes behind other waiting sessions; requests within a session
keep their enqueue order. A request that does not fit can be passed initially,
but after 60 seconds at the front of the rotation it holds back new admissions
while finite jobs are running. This lets a larger request accumulate capacity.
At most 64 requests may wait. Rotation history is shared and survives completed
jobs; older requests without a session key are grouped by project.

Rotation governs new admissions; it does not suspend builds, interrupt deploys,
or make unavailable RAM available. Active persistent resources and unmanaged
work can still prevent admission. Old runners retain their scheduling code until
they finish; start new queued work with the upgraded executable.

**Persistent resources:** `--resource NAME` reserves memory without occupying
a finite-job slot. A second request for the same name in the same canonical
directory reports the existing job/PID and exits successfully without executing
another command. It does not attach to its output. Use distinct names for
different services. This only deduplicates registered resources; check and reuse
unmanaged servers already running before enabling the queue. Ordinary background
children keep their reservation after the launch shell exits. A dead supervisor
with surviving children retains capacity and appears as `orphaned`.

**Automatic hooks:** `memcap integrate` installs and updates the entries for you.
For manual configuration, generate JSON with the following commands and merge it
into existing settings (the generator prints JSON; it does not change files):

```bash
memcap agent-hooks claude --queue
memcap agent-hooks codex --queue
```

Existing feedback hooks are included. The added synchronous `PreToolUse` hook
matches `Bash`, including Codex unified exec, and immediately rewrites launches
through the runner. Admission waiting happens in the runner; the Stop hook
separately waits locally to reduce model polling. Enable Codex's
hooks feature as required by your installed version and restart/resume sessions
to load changes. Preserve other hooks and permissions. Verify the rewritten
tool call in a sandboxed session before rollout.

Claude rewrites leave the normal permission decision in place. Codex requires
an `allow` decision with rewritten input: in `bypassPermissions` sessions the
hook rewrites automatically; in other modes it denies the original call and
instructs the agent to submit the queued command through normal approvals.
Hook timeouts can fail open in the host agent, so hooks are a guardrail rather
than a complete enforcement boundary.

Lightweight reads/searches, basic Git inspection, memcap diagnostics and GitHub
run viewing/watching stay outside the expensive-work queue. Pipelines and chains
qualify only when every stage is recognized as lightweight; ordinary file
redirections, quoted search patterns/globs, and directory-prefixed search globs
are supported. `grep`, `egrep`, and `fgrep` searches are included. Substitutions
(except numeric `$?`), arbitrary sed scripts, background launches and unknown or
expensive stages remain queued. Simple npm/pnpm/yarn dev/start and Vite
launches get resource keys. Shell text, cwd and tool options are preserved.
Claude's hook sets `run_in_background` and `--wait-forever`: the existing task
polls automatically and starts once capacity is available. It tells Claude to
wait with `TaskOutput`, read the final result, and avoid duplicate submissions.
The generated `Stop` hook uses `memcap feedback --wait` with a 75-second timeout.
It waits locally up to 60 seconds before asking the agent to continue while finite
jobs are queued/running, and wakes early when they finish. This wait holds no
lifecycle lock or pending-activity marker, so cleanup can continue. Other lifecycle
hooks and file reads remain immediate; persistent resources do not hold a
conversation open.
The host agent still controls cancellation, session exit and tool deadlines;
this does not keep an exited Claude process alive. Codex tools retain their
normal session polling and explicit timeouts. Nested managed commands share a
verified ancestor reservation to avoid slot deadlock.

**Worker controls:** default two. Recognized Jest/Vitest/Playwright test commands,
simple package scripts calling them, Go builds/tests and Cargo jobs receive
bounded worker arguments. Lower explicit limits are preserved. Go, Cargo,
CMake, BLAS/OpenMP and Vitest environment limits reach subprocesses. Arbitrary
scripts, explicit overrides inside scripts, MCP servers, double-fork/`setsid`
children, and externally owned simulators/VMs are not universally intercepted
or contained. This is admission control, not an OS memory limit. Existing
oversized-job enforcement remains the fallback for individual runaway jobs.

Cancellation removes a waiting request without launching. A running supervisor
requests cancellation only for recorded group members through `mc_kill_pids`,
with fresh ownership/start checks and agent-CLI/ancestry exclusions. Dry run
suppresses signals too, but **`memcap run` still runs the requested command**.
`memcap off` bypasses admission and suppresses cancellation enforcement until
resumed: it is an explicit opt-out from queue protection.

Optional `memcap.conf` settings (legacy/strict fallback defaults; new `init` uses
the adaptive profile documented below):

```bash
QUEUE_POLICY=strict
QUEUE_MAX_JOBS=2
QUEUE_WORKERS=2
QUEUE_JOB_GB=2
QUEUE_HEADROOM_GB=3
QUEUE_POLL_SEC=2
QUEUE_WAIT_SEC=1800
QUEUE_MAX_PRESSURE=green
```

New runners load queue settings when they start. Already-waiting runners retain
their pressure policy until they finish or are explicitly canceled. Do not create
a duplicate to pick up new settings; confirm cancellation and process exit before
any replacement. The combined budget is resampled from configuration while they wait.

Private reservations live in the state directory's `queue/`. Do not delete it
while managed work runs; that discards reservations. `memcap status` displays a
queue summary once used. The 60-second cleanup watchdog remains independent;
queued runners check admission every two seconds without model calls. Agent guidance
requests one blocking poll of up to 60 seconds while pending, with no repeated
output-file reads or holding messages. Queue reminders print at most once per minute. No jobs are suspended or
retried automatically, and normal cleanup protections are unchanged.

**Stalled standalone simulator boots:** the watchdog cancels a registered
`simctl boot UUID` attempt after three minutes, checked on its normal ~60-second
interval. This also covers jobs launched by older memcap runners. It requires
a live, same-user agent owner, matching supervisor/start identities, and a fully
registered group containing only the Apple boot command, shells and narrow
head/sleep helpers. Active builds, bootstatus waits, unrelated/detached children,
unknown identities and other sessions are retained. It does not reset devices or
terminate simulator services. Every signal uses the existing kill choke point,
with fresh authorization before TERM and escalation. A notice describes the
preparation failure; the supervisor releases its reservation only after the
managed group exits. Remaining simulator-service memory stays in the host budget.

## Automatically retire unused helpers

The optional garbage collector runs on each watchdog pass, even below the RAM
cap. It releases disposable helper processes after completed work instead of
waiting for the machine to reach red pressure. It requires Python 3.9+ and the
generated lifecycle hooks (`Stop`, `SubagentStart`, `SubagentStop`, and the
existing prompt/tool hooks). Agent CLIs and memcap's ancestry are never targets.

`GC_MODE=observe` is the default: track and log eligible processes without
stopping them. Set `GC_MODE=on` to enable automatic collection, or `off` to
disable this collector. `GC_IDLE_SEC=600` is the default grace (minimum 300).
`memcap gc` reports the mode, tracked sessions and candidate idle clocks.

Eligible resources are:

- Dedicated Playwright Chrome/headless browser groups whose owning agent has
  completed its turn and has no active subagents or unrecognized background
  work. The MCP server stays open; a later browser request must reopen a browser.
- Recognized Node/Vite development servers, including registered queue
  resources, after their owner finishes. An active recorded session in the
  same project, or any TCP client connection, protects a shared server.
- Same-user, childless iOS runtime processes reparented to PID 1, only after a
  successful device query shows all devices shut down and no mobile build or
  simulator UI is present. Root-owned system services are never targeted.

All candidates also require quiet CPU across the observation period, unchanged
process identities and membership, and a successful network inspection with no
active TCP connections. Unknown descendants, queued/build processes, resumed
prompts, or helper activity reset eligibility. An open session without completion
hooks is **unknown**, not idle. Language servers, MCP servers, ordinary Chrome
tabs and Docker containers/VMs are retained: automatic restart/ownership is not
established for those by this collector.

Every termination goes through `mc_kill_pids`, with fresh authorization before
TERM and escalation, plus the existing pause and ancestry protections. Batches
are bounded to roughly 32 processes (keeping a browser group intact). The audit
log records the kind, owning candidate PID, grace and actions. `MC_DRY_RUN=1`
previews actions without signals. State is private under `idle-gc/`; prompts and
tool payloads are not stored. A failed lifecycle hook leaves a marker under
`gc-activity-pending/` and inhibits collection rather than trusting old idle
state. Investigate the hook error before clearing a failed marker.

For a 24 GB Mac prioritizing throughput, a starting configuration is:

```bash
TOTAL_BUDGET_GB=20
QUEUE_POLICY=adaptive
QUEUE_MAX_JOBS=12
QUEUE_WORKERS=8
QUEUE_JOB_GB=1
QUEUE_HEADROOM_GB=2
QUEUE_MAX_PRESSURE=yellow
GC_MODE=on
GC_IDLE_SEC=600
```

Eight is a ceiling, not eight reserved build slots: the combined-memory and
headroom checks still decide how many jobs fit. Yellow is allowed; red prevents
new admissions. These are admission estimates, not OS memory limits, so rapid
growth inside already-running jobs can still overshoot. Cleanup targets known
idle resources; it does not promise to prevent every possible red-pressure event.

## Install

```bash
brew install alextitov19/memcap/memcap
memcap init
```

`memcap init` detects your total RAM, core count, and which coding agents are
installed, proposes a shared total and Docker VM ceiling, and writes `~/.config/memcap/memcap.conf`. It
will offer to install and start the background service (`memcap service
install`), which writes and loads memcap's own LaunchAgent — the thing that
calls `memcap watch` on a recurring cycle — at
`~/Library/LaunchAgents/com.alextitov19.memcap.plist`. You can decline and
install it later with `memcap service install`, or skip enforcement entirely by
answering "no" to the enforce prompt, which leaves memcap paused from the
start. (Earlier versions had Homebrew manage this via `brew services start
memcap`; `brew upgrade` was found to remove that plist outright, so memcap now
installs and owns it directly — `brew services start/stop memcap` is no longer
part of the supported workflow.)

One consequence of owning the label: **`brew services info memcap` reports
`Running: false` on a perfectly healthy install**, because Homebrew only tracks
plists it created itself and memcap's is not one of them. `memcap status` is the
answer to "is it running", and it names the label on its `background service`
row so the two reports can be told apart rather than believed in turn.

Everything is accept-by-Enter. The proposed defaults scale with the machine:

| Machine | Reserved for you | Total cap | Docker slice |
| ------- | ---------------- | --------- | ------------ |
| 16 GB   | 6 GB             | 10 GB     | 4 GB         |
| 24 GB   | 8 GB             | 16 GB     | 6 GB         |
| 32 GB   | 11 GB            | 21 GB     | 8 GB         |
| 64 GB   | 16 GB            | 48 GB     | 12 GB        |

Once the service is running it persists across reboots. There is nothing to
re-initialize.

## What it kills, and why that is safe

memcap runs unattended with permission to send `SIGTERM`/`SIGKILL` to processes on
your machine. That is worth being specific about before anything else. It only ever
acts through an oversized-job check and three cleanup tiers.

**Oversized live jobs — Claude and Codex children.** A subprocess exceeding
`AGENT_JOB_MAX_GB` (default **4 GB per process**, `0` disables) can be terminated
even while its agent session is alive, mobile tooling is active, or it is younger
than the cleanup age gates. This includes Python tests and build workers. The
watcher confirms the candidate with two fresh `top` footprint readings; it never
uses RSS fallback to authorize this kill. A missing row elsewhere in the process
table does not veto a candidate whose own footprint can be confirmed.

The nearest live ancestor must be a Claude or Codex executable, not an argument
mentioning one. Before TERM and KILL, memcap rechecks the process and agent start
identities and ownership. Agent CLIs, memcap's ancestry, Docker, and classified
simulator/browser resources stay excluded. Only the oversized process is selected;
this is not an aggregate limit on a job's whole worker pool. It is sampled on the
watcher's roughly 60-second cycle, so fast allocations can overshoot between checks.
This policy deliberately allows interrupting an active job to protect the host.

**Feedback to the agents.** `memcap integrate` installs the hooks and global
bootstrap guidance, and `memcap doctor` checks them. For manual setup,
`memcap agent-hooks codex` or `memcap agent-hooks claude`
prints hook entries for `memcap feedback`. Merge their `hooks` arrays into
`~/.codex/hooks.json` or `~/.claude/settings.json`, preserving existing hooks.
The generated command uses the memcap installation it was generated from; generate
it again if that installation moves. Feedback requires `jq`; enforcement does not.
Codex requires reviewing and trusting a new hook in `/hooks` before it runs.

Session-start and prompt hooks supply operating guidance automatically: keep
polling existing queued tasks, continue independent work, avoid duplicate retries,
and distinguish simulator readiness from test assertions. This ships with memcap;
users do not need to copy instructions into their agent's Markdown files.
For a separate Claude profile (`CLAUDE_CONFIG_DIR`), merge the generated hooks
into that profile's `settings.json` too. Existing hooked installations pick up
updated feedback from the installed executable; new hook registrations may require
a new session. Queue output also includes polling and expired-waiter instructions
when agent hooks are absent.

When tool results mention a simulator boot timeout, memory allocation failure or
memcap queue delay, feedback adds a timestamped, read-only diagnostic report. It
samples pressure, tracked memory and estimated available memory; checks queue
supervisor identities; and, for simulator preparation messages, lists device-state
counts. It includes up to three recent project-scoped oversized-job termination
requests without repeating command arguments. A shared 1.5-second probe deadline
keeps context bounded; missing or unreliable measurements are reported as unknown.
This extra context requires Python 3 as well as jq.

Output text is a trigger for investigation, **not a diagnosis**. The report labels
measurements as current, not evidence of conditions at the earlier failure time.
It asks the agent to establish which tests actually executed, wait for relevant
conditions before retrying, and check other sessions' ownership before simulator
recovery. It never boots, resets or erases devices, changes limits, deletes queue
records, or automatically reruns a failed build. Tool output is not persisted or
echoed into the report. The host agent still controls polling, cancellation and
whether it follows the guidance.

The suggested read-only commands `xcrun simctl list devices --json`,
`docker stats --no-stream`, `docker ps` (also `-a`/`--all`), `docker buildx ls`, and
`ps -Ao pid,ppid,command` do not reserve build slots. Streaming stats, simulator
changes, builder bootstrap/stop and unknown command chains still use normal queue
classification. Docker's VM subtotal alone does not identify an admission blocker;
feedback asks agents to use the runner's reason and preserve other sessions' work.

Hooks deliver a notice on the next tool/prompt lifecycle event: PID, measured
footprint, configured limit, and instructions to split batches/tests, reduce
workers, and investigate unbounded allocation. They explicitly discourage retrying
the same job or bypassing the cap. Notices say **termination requested**, not that
the process certainly exited. No terminal keystrokes or transcript edits are used.
Delivery is scoped to the hook's canonical project directory and deduplicated per
session; another agent in that project can see the notice too. Unknown process cwd
cannot be routed. The latest 32 job records are private, expire from hook delivery
after 24 hours, and include redacted command identities. The audit log remains the
fallback when hooks are absent or cannot run.

Hook formats: [Codex hooks](https://learn.chatgpt.com/docs/hooks) and
[Claude Code hooks](https://code.claude.com/docs/en/hooks).

**Tier 1 — orphans.** A process is only touched here if its parent is already dead
(`ppid == 1`), its command line matches a known dev-server pattern (`vite`, `next`,
`nodemon`, `uvicorn`, and similar), it lives under a sweep root memcap has learned
(see below), and it is older than `TIER1_MIN_AGE_SEC` (default 300 seconds). A dead
parent means no live terminal or session owns the process anymore — there is nothing
left for it to be doing.

That age gate arrived in 0.4.0. Until then tier 1 had no age check of any kind,
while the age gate described under tier 2 below was treated as the thing that keeps
a build from ever being the victim — a guarantee that actually held in only one of
the two tiers that can kill a build. The dev-server pattern also matches `esbuild`, `webpack`, `rollup` and `tsx`,
so `npm run build &` reparented to init was an instant target. The asymmetry decides
the default: a leak is a persistent condition, so waiting five minutes to reap one
costs nothing, while killing a three-second-old build destroys work that cannot be
recovered.

One limitation is worth stating plainly rather than papering over. `ppid == 1` is
also what `nohup` and `disown` produce, so a deliberately daemonized production
server is indistinguishable from a leak by the process table alone, and the age gate
does not help — such a server is old by definition. A real kill of `npm exec next
start -p 3100` on an ad-hoc port is the counter-example. If you daemonize something
you care about under a directory an agent session has worked in, run it somewhere
memcap has not learned as a sweep root.

A root matches either the orphan's command line or the orphan's own canonicalized
working directory — the second catches a project sitting behind a symlink, but costs
an `lsof` call, so it is budgeted per pass by `TIER1_MAX_CWD_LOOKUPS` (default 64).
Exhausting that budget means some orphans are matched on argv alone: fewer kills,
which is the safe direction to fail in, and memcap logs when it happens.

**Tier 2 — over-budget dev servers.** Only reached when agents are still over
budget after tier 1 has run. Only processes older than `TIER2_MIN_AGE_SEC` (default
300 seconds) are eligible. A dev server runs for hours; a `vite build` or test run
lasts seconds. If every candidate is younger than the age gate, memcap logs that,
sends a notification, and kills nothing on that pass.

Tier 2 also declines outright while either mobile veto holds — active mobile tooling,
or hands-on mobile work, the same two checks tier 3 uses and describes below. Before
0.4.0 it consulted neither, and the production logs show what that cost: memcap
declined to reclaim a simulator at 13:03:47 because the developer was driving it,
then killed the Metro bundler feeding that same simulator at 13:03:48.

Candidates are ranked by their whole subtree's footprint rather than their own,
because the kill takes the subtree: ranking on a process's own memory let a fat
worker outrank the server that owned the worker pool, which is memcap fighting a
supervisor that respawns. The whole live agent tree is off limits here, not just the
agent CLI — six of the ten real tier 2 kills in the audited window were
`chrome-devtools-mcp` watchdogs running as grandchildren of a live `claude` session.
`TIER2_ENABLED=0` switches the tier off entirely and leaves over-budget handling to
tier 1 and tier 3.

A candidate whose subtree holds a simulator or browser that has not cleared tier 3's
idle grace is skipped, and the next candidate down the ranking is considered instead.
Tier 3 decides when that population is reclaimable by measuring CPU flatness; tier 2
has no idleness test at all, and a browser subtree ranks first precisely because it is
the biggest thing on the machine. In production it took a Playwright driver, a headed
Chrome and its six helpers in one event. "Orphaned" is not "idle" — a Playwright run
whose shell has exited is reparented to init while its tests are still running — so a
sim with no idle stamp blocks too: unobserved is not proven idle.

**Tier 3 — idle simulators.** iOS Simulators, Android emulators, Playwright
browsers, and Maestro processes. Idle is measured directly, not inferred: memcap
samples each tracked simulator's own accumulated CPU time, the same way `top`
would show you a process is doing nothing. A booted-but-unused simulator burns
approximately zero CPU; once one has stayed flat across `SIM_ACTIVE_CPU_SEC`
(default 2) of real work for the full `SIM_IDLE_GRACE_SEC` grace period, it is
reclaimed. Real work resets that pid's own clock, so a simulator mid-test is
never mistaken for an idle one no matter how long an agent session has been open.

A booted iOS device isn't reclaimed by signalling a process — it's shut down with
`xcrun simctl shutdown <UDID>` — so it's judged one device at a time, and the
evidence has to be about that device. The only process that qualifies is the
device's own `launchd_sim`, whose command line names the device's data directory
and therefore carries its UDID; memcap shuts down a booted device only when it can
find that pid in the current snapshot _and_ that pid has cleared its own idle
grace. A booted device memcap can't map to a `launchd_sim` is left alone and
logged, and so is one whose `launchd_sim` is tracked but still inside its grace.
Both directions of not-knowing mean not touching it.

That is a fix, not a design note. Through 0.5.1 the rule was one machine-wide flag
set by any idle process matching `launchd_sim` _or_ `SimulatorTrampoline`, followed
by `simctl shutdown all`. `SimulatorTrampoline` is a CoreSimulator helper with no
device affinity that survives every boot and shutdown — 40 hours old and 11
CPU-seconds total on the author's machine, same pid across a device booting and
being shut down — so "a simulator is idle" was permanently true, and any device
that showed up `Booted` was taken on the next pass. Between 2026-08-27 and 08-29
that shut down the simulator a live Maestro run was driving, 38 times in bursts a
minute apart: Maestro re-booted the device, memcap shut it down again, until the
user stopped the daemon with `memcap off`. `SimulatorTrampoline`'s memory still
counts toward the budget; it is simply never evidence about a device. And the
shutdown's exit status is now logged, because the old line was written before the
command ran and named no device — `actions.log` could not tell a device memcap had
taken down from one it had failed to.

Protection here has three bands, because the populations genuinely differ. A
simulator or browser held open by a live MCP-style server under an agent session is
exempt for as long as its holder lives — seven "idle Chrome processes" are typically
one browser a Playwright or devtools MCP server is holding across requests, idle by
design between calls, and the reap would only be discovered when the next navigation
failed. A sim-classified process that belongs to a live agent session but is not
server-held gets a longer clock instead of immunity: `TIER3_AGENT_TREE_GRACE_SEC`
(default 1800, and never allowed to be shorter than `SIM_IDLE_GRACE_SEC`). Blanket
immunity for the whole tree would be indistinguishable from a dead tier — on a
machine that runs agents all day, every reclaimable browser is an agent descendant,
because that is how it was launched. Anything with no live owner keeps the ordinary
grace. Every exclusion is logged with its reason, which is the real difference from
the original bug: when tier 3 reclaims nothing, it now says why.

memcap versions before 0.3.0 used "no agent session is alive" as a stand-in for
"a simulator is in use," because simulators can't be attributed to a session by
process tree (CoreSimulatorService owns them, not the session that booted one).
That proxy never actually released on a machine that keeps an agent session
open continuously — which is the normal case — so tier 3 fired zero times in
1,643 real opportunities on the author's own machine while counting simulator
memory against the budget the whole time. `TIER3_REQUIRE_NO_SESSION=1` restores
that original, maximally conservative behavior for anyone who wants it back.

Two vetoes still block a reap outright: hands-on mobile work is a plain
presence check, because Xcode, Android Studio, and Simulator.app are apps a
human has open and CPU is not the signal there. Active mobile tooling
(`maestro`, `xcodebuild`, `expo`, `react-native`, `detox`) is CPU-checked the
same way simulators are, not a bare presence check — matching one of those
processes by name alone vetoed tier 3 permanently the moment it runs as a
background service rather than a foreground command, which is exactly how
`maestro`'s own MCP server behaves: it idles for days between requests, so an
existence check treated it as permanently "driving a simulator" and
reproduced the same dead-tier-3 bug this whole redesign exists to fix, with a
different permanent veto standing in for the old one. Tooling that stays
CPU-flat for `MOBILE_TOOLING_IDLE_SEC` (default 300) no longer blocks the reap;
an actual `maestro` flow, `xcodebuild`, or `detox` run burns real CPU and keeps
vetoing for as long as it does. That default was 60 until 0.6.0, on the theory
that a CLI tool going quiet for a minute is likelier idle than a simulator is.
Nine days of production logs disagreed twice over. A minute is _shorter than one
enforcement pass_ — launchd's 60-second interval measures ~64 seconds in
practice — so 60 bought no hysteresis at all: a `maestro` MCP server that
handled a single request switched the veto on, and the very next pass switched
it off again, which is how 544 "declining — active mobile tooling detected"
lines got into nine days of one machine's `actions.log`, alternating
minute-to-minute with the hands-on veto. And the premise was wrong anyway: a
Maestro run goes quiet for a minute between flows, so a quiet minute is not
evidence of idleness — it is the middle of a test suite. The window has to
outlast a pass by enough to be a judgement rather than a coin flip.

One consequence worth knowing: `expo run:ios`,
`react-native run-ios`, and Maestro's iOS flows all launch the simulator
through Simulator.app's own UI, so any of them keeps tier 3 switched off for
as long as Simulator.app stays open — including well after the command that
launched it has exited — not just while you're actively looking at it.
Neither veto, nor a pass that simply hasn't cleared the idle grace yet,
erases a simulator's accumulated idle history — only an actual reclaim does.

**Simulator memory counts toward the combined cap, but only tier 3 can reclaim
it.** A booted simulator or a Playwright-driven browser is counted into the same
agent-side total tier 1 and tier 2 measure, because it is agent-adjacent work and
should not be invisible to the budget. Tier 1's soft trigger and tier 2's kill
decision are measured net of simulator memory (agents' own footprint, not what a
booted simulator or headless browser is using): sims still count toward
`status`'s combined figure and are reclaimed by tier 3 once genuinely idle, they
just cannot be the reason a dev server gets killed.

Five guarantees hold across the three cleanup tiers, enforced at a single choke point
(`mc_kill_pids`) that every kill routes through:

- It never kills an agent CLI itself, or anything in memcap's own process
  ancestry — that check happens once, in the choke point, not per tier. For tiers 1
  and 2, which choose their victim by inference, protection covers the whole live
  agent process tree: every MCP server, hook, and tool subprocess under a session.
  Tier 3 deliberately uses the narrower scope (the agent CLIs themselves), because it
  does not infer — it acts on an explicit list of browser and emulator binaries, on
  measured CPU flatness, and on the three bands above.
- It never reaps a process its own vetoes count as evidence of active work. A
  resource cannot be both proof that someone is working and reclaimable garbage. The
  rule is enforced once, at the choke point, over whatever the veto matchers return,
  rather than by excluding one vendor from one pattern: the first thing tier 3
  selected once it was unblocked was a browser held open by a live `@playwright/mcp`
  server, in the same pass that was counting `maestro` servers as proof that mobile
  work was happening.
- Nothing runs at all while `memcap off` is set — including a manual
  `memcap clean`. Pausing is absolute, not "paused except when you ask directly."
- `MC_DRY_RUN=1` reports exactly what would be killed and why, without killing
  anything.
- Every kill is logged to `actions.log` with the reason and the process line, so
  after the fact you can see exactly what happened and why. Between `SIGTERM` and
  `SIGKILL`, a survivor's identity is re-confirmed by start time and argv and re-run
  through the protection filter, so a pid recycled inside that two-second window
  cannot be killed in the original's place.

The oversized-job check uses that same choke point, pause, dry-run, identity and
agent-CLI protections. It explicitly overrides live-child and mobile-tooling
protection for a confirmed oversized Claude/Codex child; cleanup rules above retain
their existing age, orphan and idle gates.

**Sweep roots are learned, not configured.** memcap never asks you which
directories are safe to clean. Instead, while an agent session is alive, it
records that session's working directory as a sweep root. A root only qualifies
if it resolves — canonicalized, so `..` segments and symlinks cannot be used to
escape it — to a path at least two levels below your home directory, and it is
re-validated against that rule again at sweep time, not just when it was
recorded, so a directory that gets replaced by a symlink afterward cannot be used
to redirect a kill.

**Docker is different.** memcap does not kill Docker's VM; it sets a memory and
CPU ceiling on it (`memcap docker apply`), which is the only hard limit in the
whole system — the hypervisor enforces it, unlike the soft, self-policed budget
everywhere else. Applying that ceiling requires quitting and restarting Docker
Desktop, so memcap declines to do it while containers are running rather than
interrupting them; run `memcap docker apply` again when it's convenient.
`memcap docker apply --force` overrides that refusal — it applies the ceiling
even with containers running, which restarts Docker and stops them, so use it
deliberately rather than as the default. One thing worth knowing in advance:
right after a restart, `docker images` and
`docker ps -a` can return empty for several minutes while a large image store
reloads. That is not data loss, and restarting Docker again to "fix" it only
makes the wait longer.

**`DOCKER_BUDGET_GB` is a request until applied to Docker's own settings.**
In shared mode, neither the requested nor actual VM ceiling is reserved against
agents: measured Docker footprint counts toward `TOTAL_BUDGET_GB`. Status reports
the actual ceiling separately when it can read it. A mismatch is configuration
information, not an admission reason.

The following historical diagnostic applies to explicit `BUDGET_MODE=split`,
where the fixed agent slice is calculated from the configured Docker ceiling:

```
  docker VM + helpers              7.28 GB / 6 GB ceiling ENFORCED (config asks for 4 GB)
  ...
  Docker is enforcing a 6 GB VM ceiling, not the 4 GB in your config — the agent
  budget is computed from a number Docker is not honoring. Fix with: memcap docker apply
```

It is reported rather than silently adopted. 16 GB total with 4 GB for Docker is the
policy you chose; quietly enforcing against 6 instead would be memcap choosing a
different one. `watch` logs the same line, throttled, so "why was I over budget all
week" is answerable after the fact.

**Run `memcap status` once from a terminal, or the background service cannot see any
of this.** macOS protects `~/Library/Group Containers`, where Docker Desktop keeps
`settings-store.json`, and a LaunchAgent is denied access to it: from the daemon that
file tests as present and every read of it fails with "Operation not permitted", while
the same read from your own terminal succeeds. So `watch` uses the last value
`memcap status` (or `memcap docker apply`) managed to read — kept as `docker-ceiling`
in the state directory — and says that is what it is doing:

```
  Docker is enforcing a 6 GB VM ceiling, not the 4 GB in your config — ... (Docker's
  settings file is unreadable from the background service; this is the value memcap
  last read from a terminal, 3h ago — run 'memcap status' to refresh it)
```

Until something has run from a terminal there is no such value, and rather than guess,
`watch` logs once that the check is blind:

```
  watch: cannot read Docker's settings store from the background service — macOS denies
  launchd agents access to ~/Library/Group Containers — so the VM-ceiling check is blind
  here until 'memcap status' has been run once from a terminal
```

Worth knowing because the quiet version of this shipped: v0.5.1 added the drift warning
above and it logged **zero** times in eight days and roughly ten thousand service
passes, while `status` showed the drift every single time it was typed. A read that
fails on a permission error looked exactly like a machine with no Docker Desktop on it.

`memcap docker apply` writes five settings, not just the two implied above —
all five are printed in the command's own output so nothing here is a surprise:
`DOCKER_BUDGET_GB` → `MemoryMiB`, `DOCKER_CPUS` → `Cpus`, plus a fixed 2 GB of
swap (`SwapMiB`), Resource Saver turned on (`ResourceSaverEnabled`), and
auto-pause after 30 seconds idle (`AutoPauseTimeoutSeconds`). The last two are
Docker Desktop features worth having on their own — Resource Saver idles the VM
down when nothing is running, and auto-pause suspends it during inactivity —
they just were not previously mentioned anywhere. If you had deliberately set
your own swap size, `memcap docker apply` overwrites it to 2 GB.

## Checking it is actually running

memcap is a background service, and a stopped service looks exactly like a quiet
one: `memcap status` used to still print a budget, nothing errored, and no
notification appeared. The author's own machine went 28 hours without
enforcement before this was noticed, by chance.

`status` now reports this itself. Every completed `watch` pass — including a
paused one, and one that declined to run because `memcap.conf` is misconfigured
— stamps a heartbeat, and `status` renders how long ago that was:

```
  last enforcement pass            12s ago
```

Past `STALE_PASS_SEC` (default 300, four to five real ticks — see the
measured cadence below) with no `memcap off` in effect, or if it has never run since install,
`status` says so plainly and gives the exact command to fix it:

```
  last enforcement pass            3d ago
  MEMCAP IS PROBABLY NOT RUNNING -- memcap service install
```

`memcap service install` is idempotent — safe to run whether the LaunchAgent
was never installed, was unloaded somehow, or is fine already — see "Install"
above and `memcap service status` below.

`actions.log` carries an hourly liveness line of its own — `watch: alive (memcap
0.6.0, 48 passes in 3604s — one every 75s)`, written every `LIVENESS_SEC` (default
3600). It names the build that wrote it, because the version was the one thing nine
days of log auditing could not recover from the log itself. Version 0.3.0 removed the
periodic line that made the author's 28-hour outage visible at all, so the same outage
would have been indistinguishable from a quiet week; this is that signal, back
deliberately.

The line states its own window because the raw count is not self-interpreting. A
`StartInterval` of 60 seconds is a request, not a guarantee: launchd coalesces timers,
and two consecutive intervals measured on an awake production machine were **73
seconds** apart, giving 46–58 passes in an hour rather than 60. Read against an assumed
60, a perfectly healthy daemon looks like it is stalling — so the elapsed window and
the derived interval are printed rather than left to be inferred.

A paused service with a fresh heartbeat still reads as paused, not dead — the
heartbeat answers "is the daemon ticking," a different question from "is it
enforcing," which the `ENFORCEMENT PAUSED` line already covers on its own. Note
that a fresh heartbeat only means a pass _ran_, not that anything needed doing
— silence in `actions.log` during a genuinely idle stretch is still normal;
it's a stale heartbeat spanning time you know you were working that indicates
a problem. `memcap service status` and `tail`ing `actions.log` remain useful
for a deeper look, but you shouldn't need them just to answer "is this
running."

## Notifications

memcap notifies you when it kills something to stay inside the budget, and when it
declines to. Those notifications used to arrive wearing **Script Editor's** icon,
because a notification's icon is the icon of the app that posts it: AppleScript's
`display notification` has no icon parameter, and a plain `osascript` call is
attributed to Script Editor. Notifications about processes memcap had just killed
looked like they came from a text editor nobody had opened.

So `memcap init` compiles memcap one of its own — a two-line AppleScript applet
carrying an icon rendered from a single emoji, built out of tools every Mac already
has (`osacompile`, `sips`, `iconutil`, `codesign`), which is what keeps a binary
`.icns` out of a repo that is otherwise entirely shell. It also gets memcap its own
row in System Settings → Notifications, so you can silence _memcap_ rather than
silencing Script Editor for everything.

Pick the icon with `NOTIFY_ICON` in your config, then rebuild and preview it:

```bash
memcap notify
```

`NOTIFY_ICON=none` removes the bundle and goes back to plain notifications.

None of this is load-bearing. A machine that cannot build the bundle — an ssh
session with no window server, `osacompile` unavailable — logs why in
`actions.log` and posts exactly the way every version before this did. The bundle
is also not retried on every pass once it has failed for a given icon: the attempt's
outcome is recorded, and a changed `NOTIFY_ICON`, an upgraded memcap, or `memcap
notify` are what ask for another. The first notification may need one "Allow" in
System Settings → Notifications.

## `memcap off`: the panic switch

If memcap ever does something you don't want, or you just want it out of the way:

```bash
memcap off
```

This pauses everything — the background watchdog (`watch`) and a manually-run
`memcap clean` both refuse to act while paused. Nothing is killed, logged as
killed, or swept until you run:

```bash
memcap on
```

`off`/`on` just toggle a marker file (`~/.local/state/memcap/paused`); they don't
touch your config or uninstall anything.

## Commands

| Command                         | What it does                                                                                                                                                                                                                                                      |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `memcap init`                   | Interactive setup: detect the machine, write the config, optionally start the service.                                                                                                                                                                            |
| `memcap status`                 | One-shot snapshot of current agent/Docker footprint against budget, and free system memory.                                                                                                                                                                       |
| `memcap watch`                  | Runs a single enforcement pass (tiers as needed). This is what the background service calls repeatedly.                                                                                                                                                           |
| `memcap clean`                  | Manual sweep: tier 1 (orphans) and tier 3 (idle sims) only. No-ops while paused.                                                                                                                                                                                  |
| `memcap notify`                 | Rebuild the notification bundle and post a sample, so you can see the icon. Run it after changing `NOTIFY_ICON`.                                                                                                                                                  |
| `memcap off`                    | Pause switch. See above.                                                                                                                                                                                                                                          |
| `memcap on`                     | Resume enforcement.                                                                                                                                                                                                                                               |
| `memcap profile [name]`         | List the budget profiles (`balanced`, `stacks`, `mobile`), or switch to one — rewrites `DOCKER_BUDGET_GB` in the config.                                                                                                                                          |
| `memcap docker apply [--force]` | Push `DOCKER_BUDGET_GB`/`DOCKER_CPUS` to the Docker Desktop VM ceiling, plus swap/Resource Saver/auto-pause — see Docker section above; restarts Docker Desktop to do it. Normally refuses while containers are running; `--force` overrides that and stops them. |
| `memcap service install`        | Write and load memcap's own LaunchAgent. Idempotent — safe to re-run after a config change or just to confirm it's loaded. Migrates away from an older Homebrew-owned LaunchAgent if one is found.                                                                |
| `memcap service uninstall`      | Unload and remove memcap's own LaunchAgent (and a lingering Homebrew-owned one, if present). A no-op if nothing is installed.                                                                                                                                     |
| `memcap service status`         | Report whether memcap's LaunchAgent is installed and loaded.                                                                                                                                                                                                      |
| `memcap uninstall`              | Remove memcap's own LaunchAgent and state. Keeps your config. See Uninstall below.                                                                                                                                                                                |
| `memcap integrate [selectors]` | Install/update detected or selected global Claude/Codex hooks and managed instructions, preserving other settings and backing up changed files. |
| `memcap doctor [selectors]` | Check integration/version/timeout problems and Codex runtime trust when available. `--no-runtime` leaves trust explicitly unverified. |
| `memcap version`                | Print the installed version (`memcap 0.6.0`). Also `--version`/`-v`. The same string appears in `status`'s header and in `actions.log`'s liveness line, so a log excerpt says which build wrote it.                                                               |
| `memcap help`                   | Usage summary.                                                                                                                                                                                                                                                    |

## Configuration

Config lives at `~/.config/memcap/memcap.conf`, written by `memcap init` and never
touched by `brew upgrade`. Edit it freely; every key below falls back to a
computed default if it is absent or commented out.

| Key                          | Default           | What it does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ---------------------------- | ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BUDGET_MODE` | `shared` | Share measured Docker and agent usage under the total; `split` retains the legacy fixed watchdog slice. |
| `TOTAL_BUDGET_GB`            | computed at init  | Combined ceiling for agents + Docker + sims. Computed as `total RAM − reserve`, where `reserve` is 35% of total RAM clamped to 6–16 GB, and the result is floored at 40% of the machine so small laptops still get a usable budget.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `DOCKER_BUDGET_GB`           | computed at init  | Docker's VM memory ceiling in GB — the one hard limit in the system, applied by `memcap docker apply`. Computed as 40% of `TOTAL_BUDGET_GB` clamped to 2–12 GB, then capped further so agents always keep at least 2 GB.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `DOCKER_CPUS`                | 55% of core count | Docker's VM CPU ceiling, set alongside the memory ceiling.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `SOFT_TRIGGER`               | `0.80`            | Fraction of the agents' budget that, once crossed, triggers a tier-1 sweep before anything is killed outright.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `MIN_FREE_PCT`               | `15`              | If system-wide free memory drops below this percentage, a tier-1 sweep runs regardless of whether the agent budget itself has been crossed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `TIER1_MIN_AGE_SEC`          | `300`             | Minimum age, in seconds, before an orphaned dev server is eligible for tier 1. Tier 1 had no age gate at all before 0.4.0, while the dev-server pattern matches `esbuild`, `webpack`, `rollup` and `tsx` — so a backgrounded `npm run build` whose parent shell had exited was an instant kill target. A leak is a persistent condition, so five minutes of patience costs nothing; a three-second-old build is unrecoverable.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `TIER1_MAX_CWD_LOOKUPS`      | `64`              | How many orphans per pass may fall back to resolving their own working directory (an `lsof` call, ~36 ms each) when their command line does not textually contain a sweep root. That fallback is what catches a project behind a symlink; the cap is what stops a 388-orphan leak from spending 14 seconds inside a 60-second interval. Exhausting it means some orphans are matched on argv alone — fewer kills, and a logged line saying so.                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `TIER2_MIN_AGE_SEC`          | `300`             | Minimum age, in seconds, a dev server must have reached before tier 2 will consider killing it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `TIER2_ENABLED`              | `1`               | Set to `0` to disable tier 2 entirely: memcap still measures and still reports being over budget, but never kills a dev server to get back under it, leaving that to tier 1 (orphans) and tier 3 (idle simulators). Provided because tier 2 is the one tier that acts on inference against live, parented processes — in eleven days of production logs its ten kills reclaimed 126 MB against overages of 0.5–6 GB.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `SIM_IDLE_GRACE_SEC`         | `600`             | How long a tracked simulator, emulator, or Playwright browser must show flat CPU (see `SIM_ACTIVE_CPU_SEC`) before tier 3 will shut it down, with no active-mobile-tooling or hands-on-mobile veto in effect. Each tracked process earns its own clock, starting the moment memcap first sees it, not a single clock shared by every simulator on the machine — booting a second simulator by hand does not inherit however long an unrelated, already-idle process has been sitting there. Each pid earns, and spends, its own clock: an idle one is reclaimed while a freshly-booted one alongside it is not, and for a booted iOS device the clock that counts is its own `launchd_sim`'s and no other process's. The clock for a process resets the moment its own CPU time advances meaningfully; a veto blocking the actual reap never erases accumulated idle history the way an unconditional wipe once did. |
| `SIM_ACTIVE_CPU_SEC`         | `2`               | How many CPU-seconds a tracked simulator or active-mobile-tooling process must accumulate since its clock last reset before memcap considers it "in use" and resets the clock again. A booted-but-unused simulator, or an idle `maestro` MCP server, burns approximately zero CPU, so this is deliberately small — real work should register almost immediately, biasing toward not reclaiming when in doubt.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `MOBILE_TOOLING_IDLE_SEC`    | `300`             | How long `maestro`, `xcodebuild`, `expo`, `react-native`, or `detox` must show flat CPU (see `SIM_ACTIVE_CPU_SEC`) before it stops vetoing tier 3. Must outlast one enforcement pass to mean anything: a pass takes ~64 seconds in practice, so the pre-0.6.0 default of 60 let a single handled request flip the veto on and the next pass flip it straight back off — 544 flapping decline lines in nine days of production logs. It is also a claim about the tool, not just the log: a `maestro` run goes quiet between flows, and a quiet minute there is the middle of a test suite rather than an idle process.                                                                                                                                                                                                                                                                                               |
| `TIER3_REQUIRE_NO_SESSION`   | `0`               | Set to `1` to restore memcap's pre-0.3.0 behavior: tier 3 never reaps while any agent session is alive, full stop, regardless of CPU idleness. The original design, kept as an opt-in for anyone who wants the maximally conservative posture — see the Tier 3 section above for why it's no longer the default.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `TIER3_AGENT_TREE_GRACE_SEC` | `1800`            | The longer idle clock applied to a simulator or browser that belongs to a live agent session but is not held open by a server (see the three bands in Tier 3 above). Clamped never to be shorter than `SIM_IDLE_GRACE_SEC`, since a lower value would make agent-owned browsers the _first_ thing reclaimed rather than the last.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `EXTRA_AGENTS`               | empty             | Extra agent binary names to recognize, beyond the built-in list (`claude codex cursor-agent aider gemini amp opencode goose crush`). Names must be letters, digits, `_` or `-`; anything else is dropped with a logged line rather than spliced into the classification regex. That validation is not cosmetic: an unvalidated `a\|` previously matched **every process on the machine**, making all of them agent-classified and every cwd a sweep root, while a `foo,bar` silently matched nothing and left the user believing they had added protection they did not have.                                                                                                                                                                                                                                                                                                                                        |
| `LOG_THROTTLE_SEC`           | `1800`            | How long a repeating per-pass status line (tier 3 declining, or the combined cap being exceeded) is suppressed after it first logs, so a condition that holds across many consecutive polls doesn't drown `actions.log`'s kill records. Killed-process records are never throttled. Set to `0` to log every occurrence, e.g. while debugging. A state change — the condition stopping and later holding again — always gets its own line even inside the window.                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `LIVENESS_SEC`               | `3600`            | How often `watch` writes an `alive (N passes since last mark)` line to `actions.log`, and resets the pass counter. This is the signal that distinguishes a quiet week from a dead daemon — the author's 28-hour outage was noticed only because a periodic line happened to exist at the time. Set to `0` to log one every pass.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `ROOT_TTL_DAYS`              | `14`              | How many days a learned sweep root is kept after a live agent session was last seen in it. Roots are re-registered every pass while a session sits in one, so a wrongly-dropped root returns within a single 60-second pass — which makes a short TTL cheap to be wrong about, while a kept one costs measurable time on every orphan scan forever.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `ROOT_MAX`                   | `64`              | Hard cap on retained roots, newest first. Tier 1 costs roughly 5 ms per (orphan × root) pair, so an unbounded list is a latent performance failure: 388 orphans against 40 roots already exceeds the 60-second service interval.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `MEASURE_MISSING_PCT_MAX`    | `10`              | What share of processes may be missing a `top` footprint row before memcap treats the measurement as faulty rather than merely noisy. A few missing rows happen on every busy pass and are worth ~0.03% of the total; a wholesale fallback to `ps` RSS understates the combined figure by ~42%. One threshold for both would light permanently, which is the same as no signal at all.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `NOTIFY_ICON`                | `🧠`              | The emoji memcap renders into the icon its notifications carry (see Notifications above). Must be at most 32 bytes and contain no control characters — it is passed to `sips` and written into `actions.log`, and a multi-codepoint emoji like 👨‍👩‍👧‍👦 is a legitimate choice, so the limit is measured in bytes rather than in whatever the current locale calls a character. `none` disables the bundle and returns to plain Script Editor notifications. Changing this rebuilds on the next pass.                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `STALE_PASS_SEC`             | `300`             | How long since the last completed `watch` pass before `status` reports the service as probably not running, rather than just "quiet." Four to five real ticks: the LaunchAgent asks for 60 seconds, but launchd's timer coalescing makes the measured interval 60–75 seconds, so 300 seconds is long enough to absorb a missed tick without a false alarm.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |

## Pressure diagnostics

`memcap status` reports host disk availability and used swap as well as the
classified memory pools. By default, the watcher warns below 10 GB available disk
or at 8 GB used swap. Swap grows dynamically on macOS: zero free space inside the
currently allocated swapfiles does not by itself mean exhaustion. Failed probes
are reported as unknown, never as a healthy zero.

During combined-budget, low-free-memory, or host disk/swap pressure, memcap retains
twelve snapshots under `~/.local/state/memcap/pressure/`, one every five minutes
while pressure persists. Each has at most twenty processes and bounded command
and ancestry fields, including processes outside memcap's reclaim scope. The
records use the same footprint snapshot as enforcement (and label any RSS fallback),
with PID, parent chain, separately sampled start identity and protection reason.
Read the latest saved record with:

```sh
memcap diagnostics
```

The directory is private (0700), records are 0600, and common credential arguments
are redacted. Command arguments can still contain sensitive data; inspect a record
before sharing it. PID identity is sampled after the memory table, so a process
that exits or a reused PID may have an unavailable or different start identity.
Snapshots provide evidence, not retrospective proof of ownership.

| Setting | Default | Meaning |
| --- | --- | --- |
| `HOST_MIN_DISK_GB` | `10` | Warn below this available disk space on the home volume; `0` disables this threshold. |
| `HOST_MAX_SWAP_GB` | `8` | Warn at or above this used host swap; `0` disables this threshold. |
| `PRESSURE_SNAPSHOT_SEC` | `300` | Minimum interval during sustained pressure; `0` captures each pass. Retention is always twelve records. |

These settings take effect through defaults on upgrade; existing config is not
rewritten. Disk/swap thresholds are diagnostics, not permission to kill protected
processes or delete files. Generic Python workers attached to live agents remain
protected; detached workers are visible in snapshots but are not automatically
made orphan candidates. Memcap's budget is a cleanup policy, not a hard OS memory
limit covering every process.

Combined over-cap warnings are emitted even when tier 2 is selected but cannot
reclaim anything. The `over-budget` outcome refers to the sampled usage before
that cleanup, not a claim about memory actually freed. If Docker's configured and
observed ceilings differ, status shows the resulting allowances and current
headroom; it never silently reduces the agent budget or restarts Docker.

Mobile vetoes name their blockers. A proven idle browser owned by a different
known agent from every mobile blocker may be reclaimed under the existing idle
and held-resource rules. Same-session and unknown ownership retain the veto, as
do mobile simulators and devices. Tier 2 continues to protect mobile bundlers.

## Files on disk

Config, at `~/.config/memcap/memcap.conf` (override with `MEMCAP_CONFIG_HOME`):
your settings, as described above. Not removed by `memcap uninstall`.

State, at `~/.local/state/memcap/` (override with `MEMCAP_STATE_HOME`):

- `actions.log` — an append-only record of memcap's enforcement decisions, not
  only kills: it also logs a tier-2 pass that found nothing old enough to kill,
  a tier-3 `simctl shutdown <UDID>` (naming the device, the `launchd_sim` that
  spoke for it, and whether the shutdown succeeded or the rc and error if it
  didn't), a booted device memcap declined to touch because nothing in the
  snapshot maps to it, a tier-3 reclaim's audit detail (which pid,
  how long it was idle, its flat CPU baseline), tier 3 declining because active
  mobile tooling or hands-on mobile work is in progress (or, with
  `TIER3_REQUIRE_NO_SESSION=1`, because an agent session is alive), `watch`
  refusing to act against a misconfigured budget, and the combined cap being
  exceeded — attributed to Docker sitting over its ceiling, to simulator memory
  tier 2 correctly won't touch, or to both, since only the second of those is
  something a tier can reclaim. If you're wondering why memcap did or didn't do
  something, this is where to look. Kill
  records are logged every time; the two lines that would otherwise repeat on
  every single pass — tier 3 declining, and the combined cap being exceeded —
  are throttled to at most one per `LOG_THROTTLE_SEC` so they don't drown the
  kill records in a long-running install.
- `log-throttle/` — one stamp per throttled log key (see `LOG_THROTTLE_SEC`
  above), cleared the moment that key's condition stops holding so the next
  occurrence logs immediately rather than waiting out a stale window.
- `docker-ceiling` — `<MiB> <epoch>`: the VM ceiling Docker's own settings last
  reported, and when it was read. Written by `memcap status` and by `memcap
  docker apply`, which run from a terminal and can open Docker's settings file;
  read by the background service, which cannot — macOS denies LaunchAgents
  access to `~/Library/Group Containers`. Without it `watch` has no way to tell
  whether the ceiling in your config is the one Docker is enforcing (see the
  Docker section above). Delete it and the next `memcap status` writes it again.
- `roots` — the learned sweep roots, newest first, one `EPOCH<TAB>PATH` row per line.
  Plain-path lines written by older versions are still read. A row you add by hand is
  kept if it resolves somewhere safe, whether or not it is already canonical.
- `last-pass` — epoch seconds of the last completed `watch` pass, written on
  every path through `watch` including paused and misconfigured-budget early
  returns. What `status` reads to report the service as running, stale, or
  never started — see "Checking it is actually running" above.
- `paused` — present exactly when `memcap off` is in effect; its absence means
  enforcement is active.
- `.notified` — a timestamp used to rate-limit desktop notifications to at most
  one every 5 minutes.
- `Notifier.app` — the generated bundle memcap posts notifications through, so they
  carry its icon rather than Script Editor's (see "Notifications" above). Regenerated
  from `NOTIFY_ICON` whenever that changes; delete it and memcap rebuilds it on the
  next pass, or falls back to plain notifications if it cannot.
- `notify-message` — the text of the notification being posted. The applet reads it
  from here rather than taking it as an argument, because `open --args` does not
  reach an applet's `run` handler on macOS 26.
- `notifier-stamp` — which icon the bundle was built from, which memcap built it,
  and whether that build succeeded. What stops a ~4-second rebuild from running on
  every 60-second pass.
- `sims-idle/` — one file per tracked simulator/emulator/browser process
  (named by pid), holding when its clock last (re)started and its CPU-time
  baseline at that moment; tier 3 waits out `SIM_IDLE_GRACE_SEC` of flat CPU
  from each pid's own stamp before reaping. The clock resets whenever that
  pid's CPU time advances by `SIM_ACTIVE_CPU_SEC` or more since the stamp;
  vetoes (active mobile tooling, hands-on mobile work) block a reap without
  touching this file, so idle time keeps accumulating honestly through a
  decline rather than being erased.
- `tooling-idle/` — the same shape as `sims-idle/`, one file per pid matching
  `maestro`/`xcodebuild`/`expo`/`react-native`/`detox`, tracking flat CPU
  against `MOBILE_TOOLING_IDLE_SEC` so a background service (a `maestro` MCP
  server, say) stops vetoing tier 3 once it's demonstrably idle rather than
  vetoing forever just for existing.

LaunchAgent, at `~/Library/LaunchAgents/com.alextitov19.memcap.plist` (override
the directory with `MEMCAP_LAUNCHAGENT_DIR`): written and owned by `memcap
service install`, which is what `memcap init` calls. Never
`homebrew.mxcl.memcap.plist` — that label and file belong to Homebrew's own
copy from older versions, which `memcap service install` detects and migrates
away from.

All of the above is removed by `memcap uninstall`.

## Uninstall

```bash
memcap uninstall
```

This unloads and removes memcap's own LaunchAgent
(`~/Library/LaunchAgents/com.alextitov19.memcap.plist`) — and a lingering
Homebrew-owned one from an older install, if it finds one — then deletes
everything under `~/.local/state/memcap/`: the action log, the learned sweep
roots, the heartbeat, and the pause marker. It deliberately leaves your config
at `~/.config/memcap/memcap.conf` in place; it prints that path so you know
where it is, and does not delete it for you, because you may be about to
reinstall rather than leave for good. Remove it yourself if you want a clean
slate, then:

```bash
brew uninstall memcap
```

## Development

```bash
brew install bats-core shellcheck
git clone https://github.com/alextitov19/memcap.git && cd memcap
bats tests/            # the full suite
shellcheck bin/memcap libexec/*.sh tests/*.bats tests/*.bash
```

CI runs exactly three checks on a macOS runner, and a pull request must pass all
three: `shellcheck`, a `bash -n` parse check against the system bash 3.2, and
`bats tests/`.

Three constraints are easy to trip over and are enforced by those checks:

- **bash 3.2 only.** macOS ships bash 3.2 and memcap targets it directly, so no
  `mapfile`, `readarray`, `declare -A`, `local -n`, `${var,,}`, or `&>>`.
- **The suite must pass with no Docker Desktop installed.** CI runners have none.
  Verify with `env HOME="$(mktemp -d)" bats tests/`, and use the
  `MC_DOCKER_RUNTIME` escape hatch rather than depending on the host.
- **Never let a test reach a real kill.** Enforcement tests set `MC_DRY_RUN=1` in
  `setup()`; keep it that way. A test run must not be able to terminate a
  developer's dev server or quit their Docker.

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the
workflow, and [AGENTS.md](AGENTS.md) if you are pointing a coding agent at this
repository.

## License

MIT — see [LICENSE](LICENSE).

## Adaptive throughput (0.16)

`memcap init` now writes the adaptive settings above. Existing configurations that
omit `QUEUE_POLICY` keep `strict`; upgrading never silently migrates owner policy
or resumes a paused installation. The owner may choose `QUEUE_POLICY=adaptive`
and the 12-slot / 8-worker / 1-GB profile above in `memcap.conf`.

Adaptive admission treats `TOTAL_BUDGET_GB` as a **planning target**. A 24-GB Mac
can have charged footprints above 20 GB while pressure remains green or yellow:
compressed/swapped accounting, Docker guest usage and the VM ceiling are different
measurements. Memcap keeps the footprint measurement, but no longer treats it as
an unconditional veto in adaptive mode. Docker is included through host pressure
and headroom; its configured ceiling is not a reservation. No Docker restart or
ceiling change is part of this feature.

New heavy work requires a fresh reliable sample, permitted pressure and estimated
physical headroom covering outstanding reservations, its startup request and a
512-MiB emergency margin (or a smaller explicit `QUEUE_HEADROOM_GB`). Red blocks
new starts. Sustained yellow plus swap-out >=128 MiB/s for 10 seconds adds a
six-second recovery window. Existing swap volume alone does not block work.
Unknown paging counters remain unknown; pressure and footprint signals are still
required. Starts are spaced by at least two seconds across **all sessions**.
These are initial tuning parameters, not a guarantee that running or unmanaged
work can never push the machine into red.

Recognized searches, reads, status and remote SSM control remain outside the heavy
queue. Managed work uses the configured startup prior, with smaller priors for
recognized small tool families and at least 4 GB for direct Xcode/Swift builds.
Successful, sufficiently sampled runs teach private estimates keyed by project,
command, executable metadata, dependency manifests, worker allocation and common
cache-directory presence. This cache distinction is a heuristic, not detection of
every build system's cold state. Recent peak observations include a 25% margin;
estimates decrease at most 10% per completed run. Cancelled, failed or incomplete
samples never teach a lower value. Explicit `--memory` retains its allowance.
Short jobs may finish between samples and do not teach an estimate.

Worker limits are chosen at launch from the CPU pool and contending sessions,
up to `QUEUE_WORKERS`. Smaller explicit limits survive. Already-running jobs
are not interrupted or dynamically resized. Session rotation spans job types;
aged large jobs get bounded drain windows (six seconds in each thirty), then
smaller fitting jobs can progress. Arbitrary running processes are never suspended
or killed merely to rotate a slot. Dead waiters are removed only after identity
checks; a dead supervisor with surviving children keeps its reservation.

Sampling has its own nonblocking lock. Queue status and wait operations do not
acquire the admission lock or create jobs. The supervisor waits internally; agents
use their existing task's blocking poll once a minute, or `memcap wait --session
--timeout 60` when native polling is unavailable. No drain ticks or duplicate jobs.

`memcap queue --summary` identifies older supervisors that still have pre-upgrade
code loaded. New commands and stable hooks use the new installation. Let existing
tasks finish; upgrading cannot rewrite their in-memory code. Never duplicate
pending tasks just to pick up the update. A machine restart is not required for
new commands.

Numeric local events (sample, admission, completion) are private, rotated and
bounded to approximately 32 MiB / 24 hours. Reports include fresh paging rates,
sample age, policy and legacy-supervisor counts when available, without raw
commands, paths or process identifiers. Publication still requires the existing
one-time opt-in; there is no memcap report quota. Report excessive waits and
lightweight queuing even if work eventually succeeds.

Adaptive mode also prevents tier-2 cleanup solely for exceeding the footprint
target while pressure is green/yellow. Orphan/ownership/age protections, explicit
oversized-child limits, idle-helper policy and the pause switch remain in force.
See [validation and incident coverage](docs/adaptive-throughput-validation.md).

### Inspection and memory interpretation (0.16.1)

Bare filename globs such as `rg -n pattern *.go` and the narrow
`f=$(rg -l pattern src); sed -n 1,200p $f` form no longer reserve a heavy-job slot.
The shell expands arguments once; an internal guard checks those exact arguments
before executing the read. A filename that becomes an execution option (for
example `--pre=helper.go`) still enters normal admission. Mixed scripts containing
builds or unknown execution remain managed. Existing automatic wrappers receive
this reclassification when newly invoked, without resubmitting pending jobs.

Docker’s configured maximum is **not reserved RAM**. Container memory, the VM’s
charged footprint, and resident host memory measure different things. Subtracting
container usage from VM footprint does not establish how much quitting Docker
would free. Accumulated swap is not current swap traffic. Do not stop another
project’s stack on the strength of those numbers.

When the owner has paused memcap, its displayed planning target is not an
admission refusal. Agents should continue authorized work under that selected
state using fresh pressure, available memory and device readiness as evidence.
Neither a target overage nor accumulated swap alone proves a simulator cannot
boot; green/yellow pressure does not guarantee future growth will remain safe.

If a memcap Stop hook has already blocked ending the turn, use the existing
task's blocking poll or `memcap wait JOB_ID --timeout 60`, once per minute.
Do not try to end that turn merely to receive a completion notification: this
hook cannot suspend and resume the agent. Native notifications are preferred
when the client can await them without entering a blocked Stop. Read the final
output and status before dependent work; never submit a duplicate job.

Running supervisors retain their job and reservation if a process-identity query
is temporarily unavailable. They retry fresh observation, and retry failed guarded
cancellation instead of silently leaving the workload behind. These retries release
the registry lock; unavailable identities never authorize a signal or free capacity.

`memcap wait` observes managed jobs only. If it reports no pending work, do not
loop on it for an unmanaged native background task: use that task’s completion
notification and read its final output and exit status. Updated hook context
conveys this guidance to existing sessions on their next hook invocation; an
already-running supervisor retains its loaded code.

### Native execution while paused (0.16.2)

An owner pause now leaves new Claude/Codex tool requests unchanged: no forced
background mode, no queue message, and no worker cap injected by memcap. Explicit
`memcap run` and newly invoked cached wrappers execute their original command
before accessing the queue registry. A contended registry lock cannot delay a new
paused command. When an owner pause releases a registered waiter, it remains
supervised until completion but receives no new worker limits; that unrestricted
run does not teach a limited-worker estimate. Previously launched processes keep
the arguments/environment they already received.

Session guidance refreshes once after each owner pause/resume, even without a
version change. A native background task is not a memcap queue entry: use its
completion notification or native blocking poll, then read its final output and
exit status. An empty memcap registry cannot tell you that task’s result. Existing
managed tasks still need their actual results before dependent work.

While active, finite `aws logs tail` calls, GitHub workflow control (including
`R=owner/repo && gh workflow run ... -R $R`), single-file `rm file` followed by
inspection, and `wc -l ./pages/{A,B}.tsx` avoid heavy reservations. Pathname brace
expansion uses the same expanded-argument validation as globs. `--follow`,
recursive removal, unknown substitutions and stages that build/run code retain
normal admission and permission handling.

As of v0.16.4, current-home paths (`~/…`), literal path aliases such as
`SP=/tmp/logs; cat $SP/test.log`, bounded file-search/log-excerpt forms, AWS identity,
ADB device lists, simulator device lists and GitHub run cancellation also keep
native tool behavior. Compound commands are already checked stage by stage;
every stage must qualify. General Python/awk scripts, Git commits (which can run
hooks), opaque shell helpers and mixed build commands still require admission.

As of v0.16.5, GitHub issue/PR inspection, finite jq selectors/formatters and
literal `F=/path` and `D=/path` aliases also retain native execution. Bounded quoted `cat`
heredoc writes/appends are accepted when the prefix and header are proven safe.
v0.16.7 also accepts a lightweight suffix such as `echo ok` or `cat note.md`,
after checking every suffix stage. Additional heredocs and executable suffixes
remain managed. Filename pipes using the supported
`xargs -I{} rg` or quoted `while read` form validate each expanded child argv;
filenames that become execution options still require admission. General Python,
Perl, jq generators/modules, unquoted heredocs and mixed workloads remain managed.
Bounded literal inspection loops and direct CloudWatch alarm listing also qualify.
Use native editing tools for small edits when available.

Direct `gh pr checks --watch --interval 60` keeps native execution too. Combining
it with `git push`, a rebase, or a build makes the entire shell command managed:
Git mutations can execute local hooks. Run independent status inspection as its
own command; this does not exempt the mutation or its hooks from admission.

Literal `cd`/environment/redirection wrappers around known dev servers and
streaming `adb logcat` retain their persistent-resource classification. They still
need admission and memory accounting, but do not hold finite-work Stop hooks open.
Existing runners retain their original classification until they finish.

Full operational hook guidance is sent once per session/version/pause state.
SessionStart refreshes it after compaction or resume as well. Later prompts get a
short reminder. Successful inspection of source or logs does
not diagnose quoted error strings as a new workload failure; actual runner queue
notices and failed workload diagnostics remain supported.

`memcap wait` accepts memcap job IDs, not Claude task IDs. Invalid IDs return
usage guidance immediately, as do missing arguments and invalid timeouts. Pending output distinguishes running work from queued
work and gives its age. Prefer native task completion notifications; the fallback
wait remains bounded to 60 seconds. It does not capture or publish workload output.
Reduced automatic adaptive allowances survive sampler contention and incomplete
observations without returning to the original startup estimate. Fresh complete
measurements are still required to shrink; explicit/strict/orphaned floors remain.

Report each incident once, even across upgrades or newly available symptom labels.
Submit `memcap report` directly; a reporting loop or a report combined with a script
may itself need admission. Routine queue transitions provide brief reporting and
completion reminders without repeating the full diagnostic probe and guidance.
