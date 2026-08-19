# memcap — design

**Date:** 2026-08-12
**Status:** approved, ready for implementation planning
**Repo:** `github.com/alextitov19/memcap` · tap: `github.com/alextitov19/homebrew-memcap`

## Problem

AI coding agents leak long-lived processes. A session that restarts a dev server on
each retry leaves the old ones orphaned; simulators and Playwright browsers survive a
crashed test run; Docker Desktop's VM balloon grows and never gives memory back. On a
machine with limited RAM the result is swap thrash and, eventually, a hard shutdown.

This happened on the author's 24 GB Mac on 2026-08-04: 388 orphaned `tsx` dev servers
holding 2.9 GB, a Docker VM ceiling of 12 GB (half the machine), and 20.3 GB of 21.5 GB
swap in use. The prototype built in response works well and has run since. memcap
packages it so other people can install it in one command.

## Goals

- `brew install` → `memcap init` → never think about it again, including across reboots.
- Sensible suggested defaults derived from the machine, all accept-by-Enter.
- Guarantee headroom for the human: browser, Slack, video calls.
- Degrade gracefully on machines unlike the author's.

## Non-goals

- Linux. The mechanics differ entirely (cgroups v2, systemd, no Docker Desktop VM).
- Capping processes the user started themselves. Only agent-spawned work is in scope.
- Being a general-purpose resource manager with user-defined process groups. That
  trades away install-and-forget, which is the whole point.

## Decisions

| Question        | Decision                                                                                  |
| --------------- | ----------------------------------------------------------------------------------------- |
| Audience        | Public, any Mac dev                                                                       |
| Scope           | AI coding agents and what they spawn (plus the Docker VM and simulators they drive)       |
| Default posture | Full enforcement on install — all three tiers                                             |
| Docker          | Opt-in at init; deferred if containers are running; other runtimes counted but not capped |
| Integration     | Daemon only. No edits to shell rc files or agent config files.                            |
| Implementation  | Bash, packaged; command surface designed to survive a later Go rewrite                    |

## Architecture

Two repos:

- `alextitov19/memcap` — `bin/memcap` (dispatcher) and `libexec/*.sh` (logic).
- `alextitov19/homebrew-memcap` — the formula.

> **Reversed 2026-08-17 (service ownership).** This section originally read: the
> formula declares a `service do` block, `brew services start memcap` generates
> `~/Library/LaunchAgents/homebrew.mxcl.memcap.plist`, and "Homebrew owns the
> daemon lifecycle; memcap does not hand-write a plist." That was a deliberate
> choice at the time (see "Why daemon-only" below) to avoid managing plists on
> other people's machines.
>
> **`brew upgrade` was found to remove that plist outright**, not just unload
> it — confirmed twice, including a 28-hour outage on the author's own machine
> with no paused marker, no crash evidence, and the plist simply gone. Because
> `RunAtLoad` lived in a plist that no longer existed, the daemon did not come
> back at login either. Reporting that correctly (the v0.1.4 heartbeat) is not
> the same as having a daemon.
>
> memcap now installs and owns its own LaunchAgent (`libexec/service.sh`,
> `memcap service install|uninstall|status`), under a label Homebrew never
> created and therefore cannot delete (`com.alextitov19.memcap`, never
> `homebrew.mxcl.memcap`). `memcap init` installs it; `memcap uninstall` removes
> it. The formula's `service do` block is dropped, so `brew services
start/stop memcap` is no longer part of the supported workflow — leaving it
> in would let a user re-arm the exact mechanism this replaces, and running
> both at once means two `watch` passes racing every 60 seconds against
> separate snapshots. An existing Homebrew-owned plist from a v0.1.x install is
> detected and migrated away from (`brew services stop`, then the plist file
> removed) the first time `memcap service install` runs.
>
> The plist content itself was already correct and is unchanged: `RunAtLoad`
> true, `StartInterval` 60, `ProgramArguments` pointing at the stable
> `$(brew --prefix)/opt/memcap/bin/memcap` symlink (never a versioned Cellar
> path) so it survives `brew upgrade` swapping the binary underneath it, and
> logs at `$(brew --prefix)/var/log/memcap.{log,err}`. Only who owns the file
> changed.

The formula installs scripts into `libexec` and symlinks `bin/memcap`. It no
longer declares a `service do` block — see the reversal note above.

### Why daemon-only

The prototype also installed `SessionEnd` hooks into three agent config files and shell
wrapper functions in `.zshrc`. Those made cleanup instant rather than within 60 seconds.
For reclaiming RAM that difference is not observable, and the cost on other people's
machines is high: JSON-merging into settings files the user owns, detecting their shell,
and reversing it all on uninstall. The daemon alone catches the same orphans, simulators
and budget overruns on its next poll.

## Components

| Component                                   | Responsibility                                                                                         |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `memcap init`                               | Detect machine, propose defaults, write config, optionally apply Docker cap, install its own service   |
| `memcap watch`                              | One enforcement pass. Invoked by the service every 60s.                                                |
| `memcap status`                             | Report current usage against budget, and whether the service itself is running (see `last-pass` above) |
| `memcap clean`                              | Sweep leftovers immediately                                                                            |
| `memcap profile [name]`                     | List or switch Docker/agent splits                                                                     |
| `memcap docker apply`                       | Apply the VM ceiling when convenient                                                                   |
| `memcap service install\|uninstall\|status` | Install, remove, or check memcap's own LaunchAgent -- see the reversal note above                      |
| `memcap off` / `on`                         | Pause/resume enforcement without uninstalling                                                          |

### Files on disk

| Path                                                  | Contents                                                                                                                                      |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `~/.config/memcap/memcap.conf`                        | Config. Shell-sourceable `KEY=value`, hand-editable, never touched by `brew upgrade`.                                                         |
| `~/.local/state/memcap/actions.log`                   | Every kill, with pid, RSS and full command. What `memcap status --log` tails.                                                                 |
| `~/.local/state/memcap/roots`                         | Learned sweep roots (see below).                                                                                                              |
| `~/.local/state/memcap/last-pass`                     | Epoch seconds of the last completed `watch` pass. What `status` reads to report the service as running, stale, or never started.              |
| `~/.local/state/memcap/paused`                        | Presence of this file means `memcap off`. Checked first on every pass.                                                                        |
| `~/Library/LaunchAgents/com.alextitov19.memcap.plist` | memcap's own LaunchAgent, written and owned by `memcap service install` -- see the reversal note above.                                       |
| `$(brew --prefix)/var/log/memcap.{log,err}`           | Daemon stdout/stderr, still at the same path Homebrew's `service do` block used to configure, now written because memcap's own plist says so. |

State lives under `~/.local/state` rather than beside the config so that deleting state
never destroys settings.

### Agent detection

Matched on `argv[0]` basename against a shipped list: `claude`, `codex`, `cursor-agent`,
`aider`, `gemini`, `amp`, `opencode`, `goose`, `crush`. Users extend it with
`EXTRA_AGENTS="foo bar"` in the config; nothing else about the tool assumes a
particular agent. Any process descending from a matched root is agent-owned.

The list is a starting point, not a closed set — an unknown agent simply means memcap
idles, which is the correct failure mode.

### Profiles

Named Docker/agent splits of the same total cap, switched with `memcap profile <name>`:

| Profile              | Docker     | Agents | For                                        |
| -------------------- | ---------- | ------ | ------------------------------------------ |
| `balanced` (default) | 40% of cap | 60%    | One stack plus a simulator and dev servers |
| `stacks`             | 65%        | 35%    | Several container stacks at once           |
| `mobile`             | 25%        | 75%    | Simulators, emulators, Playwright          |

Percentages, not absolute GB, so profiles work on any machine size. Switching only
rewrites the config; the Docker ceiling needs `memcap docker apply` (or a prompt to run
it), because raising the VM ceiling requires a restart that is refused while containers
are running.

## Budget model

Reserve for the human, then everything else shares the remainder:

```
reserve = min(max(total_gb * 0.35, 6), 16)
cap     = round(total_gb - reserve)          # floored at 40% of the machine
docker  = clamp(cap * 0.4, 2, 12)
docker  = min(docker, cap - 2)               # agents always keep at least 2 GB
```

The last line is not cosmetic: without it an 8 GB machine yields cap 3 and docker 4, so
the agent budget is negative and the watchdog treats agents as permanently over budget,
killing a dev server on every pass. It does not affect 16 GB and larger.

| Machine | Reserve | Cap | Docker slice |
| ------- | ------- | --- | ------------ |
| 16 GB   | 6       | 10  | 4            |
| 24 GB   | 8       | 16  | 6            |
| 32 GB   | 11      | 21  | 8            |
| 64 GB   | 16      | 48  | 12           |

The 24 GB row reproduces the author's hand-tuned configuration exactly, which is the
validation case for the formula.

## Enforcement tiers

Escalating, and no tier ever touches an agent CLI itself:

1. **Orphans** — `ppid == 1` and matching a dev-server pattern and inside a known sweep
   root. A dead parent means no live session and no terminal owns it. Provably safe;
   this tier alone resolved the 388-process incident.
2. **Over-budget dev servers** — only processes older than `TIER2_MIN_AGE_SEC` (default
   300). A dev server lives for hours; a `vite build` lives for seconds. The age gate is
   what keeps builds from ever being the victim. If every candidate is too young,
   memcap warns instead of killing.
3. **Idle simulators** — iOS Simulators, Android emulators, Playwright browsers and
   Maestro, reclaimed once genuinely idle, and never while Xcode, Android Studio,
   or Simulator.app is open.

   > **Reversed 2026-08-18 (idleness signal).** This originally read "only when
   > no agent session is alive" -- "an agent session is alive" was chosen as a
   > conservative stand-in for "a simulator is in use," because a simulator
   > can't be attributed to a session by process tree (CoreSimulatorService
   > owns it, not the session that booted it). Production data showed the
   > proxy never released: tier 3 fired zero times in 1,643 real
   > opportunities on a machine that always has an agent session open, while
   > `rm -rf` on every decline reset every sim's idle clock to zero on every
   > single pass -- tier 3 could not fire even in principle. Idleness is now
   > measured directly: each tracked sim's own accumulated CPU time is
   > sampled, and a pid that stays flat for the full grace period is
   > reclaimed regardless of whether a session is open, since a
   > booted-but-unused simulator burns approximately zero CPU either way.
   > Active mobile tooling actually driving a simulator (`maestro`,
   > `xcodebuild`, `expo`, `react-native`, `detox`) and hands-on mobile work
   > (Xcode/Android Studio/Simulator.app open, kept exactly as it was) remain
   > independent vetoes -- both are stronger, more certain evidence than a CPU
   > sample and block a reap outright.
   >
   > **Amended 2026-08-18 (the active-tooling veto swapped one permanent block
   > for another).** Matching a tool's mere existence reproduced the exact bug
   > this reversal exists to fix: `maestro`'s own MCP server idles for days
   > between requests, matched the maestro pattern, and permanently vetoed
   > tier 3 on the machine whose 1,643 dead declines motivated the whole
   > change -- caught only after shipping, on the real, live process, not in
   > any test. Active mobile tooling is now CPU-checked the same way
   > simulators are (a separate `tooling-idle/` stamp directory,
   > `MOBILE_TOOLING_IDLE_SEC` default 60s, shorter than a simulator's grace
   > since a quiet CLI tool or background server is more likely genuinely
   > idle than a quiet simulator). Hands-on mobile work is unchanged: a plain
   > presence check, because Xcode/Android Studio/Simulator.app are GUI apps a
   > human has open, and CPU is not the signal there.
   >
   > `TIER3_REQUIRE_NO_SESSION=1` restores
   > the original behavior for anyone who wants the maximally conservative
   > posture back.

### Sweep roots are learned, not configured

Each poll, the daemon records the working directory of any live agent session to a state
file. Sweep roots build themselves. This removes a config question no new user could
answer well and is strictly more accurate than a hardcoded path.

## Process classification

The heuristics are the product. Four rules, each earned from a real defect in the
prototype:

- **Simulators are matched by command pattern, not process tree.** iOS Simulators belong
  to `CoreSimulatorService` and Android emulators daemonize, so neither is a descendant
  of the agent. A tree-walk reports 0 GB while they consume 5 GB.
- **Match `argv[0]`, not the whole command line.** Matching anywhere in the command line
  classified `rg ms-playwright` as a browser and made it a kill candidate.
- **`ps` RSS is the wrong metric, everywhere — not just for Docker.** Two independent
  measurements on a real machine: the Docker VM reported 9.02 GB of RSS for a 6 GB VM
  because RSS counts file-backed pages of its disk image; and a booted iOS Simulator's
  266 processes summed to 16.18 GB of RSS against 6.42 GB of real footprint, because
  every process in a tree sharing the runtime's dyld cache counts those shared pages
  again. Both are the same error. `mc_ps_snapshot` therefore merges one
  `top -l 1 -stats pid,mem` sample into the `ps` structure, so the size column is
  physical footprint for every process and no component needs its own correction.
  Falls back to RSS per-process when top has no row, and entirely under `MC_NO_TOP=1`.
  Measured cost 0.43s per pass against 0.05s for bare `ps`.
- **Orphan status is the safety gate.** `ppid == 1` is what distinguishes garbage from a
  process a live session or a human terminal still owns.

## Safety

- Never killed: an agent CLI, anything in memcap's own ancestry, anything younger than
  the age gate, anything while `memcap off` is set.
- Every kill logged with pid, RSS and full command.
- Tier 2 and 3 actions raise a rate-limited macOS notification. Kills are never silent.
- `--dry-run` on every destructive command.

## Degradation

| Condition                  | Behavior                                                                                                                                                                                               |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| No Docker installed        | Docker module skipped; budget covers agents only                                                                                                                                                       |
| OrbStack / Colima / Podman | Usage counted, no ceiling possible; stated once at init                                                                                                                                                |
| Docker busy at init        | Restart deferred, prints `memcap docker apply`. Nothing is written: Docker rewrites its own settings file on quit, so a write made while it runs is clobbered by the very restart that would apply it. |
| Engine slow after restart  | Wait loop, then an explicit message that an empty `docker images` is a slow image-store load and **not** data loss                                                                                     |
| Intel Mac                  | Docker VM is hyperkit, not Virtualization.framework; match both                                                                                                                                        |
| No agents detected         | Installs fine; daemon idles until one appears                                                                                                                                                          |
| `top` fails                | Fall back to `ps` RSS, log the caveat                                                                                                                                                                  |

The "engine slow after restart" row is a real incident: quitting Docker Desktop to apply
a new ceiling left `docker images` and `docker ps -a` returning empty for several
minutes, which is indistinguishable from data loss and invites a second restart that
makes it worse.

## Migration for the author's machine

`memcap init` detects `~/.claude/agent-budget.conf`, imports its values, and offers —
without forcing — to remove the prototype's LaunchAgent, its three `SessionEnd` hooks
(`~/.claude/settings.json`, `~/.claude-personal/settings.json`, `~/.codex/hooks.json`)
and its `.zshrc` wrapper functions and aliases. Declining leaves both systems running,
which is redundant but harmless since every operation is idempotent.

## Testing

- **Fixture-based classification tests** are the core. Captured `ps -Ao pid,ppid,rss,command`
  output, including the 388-orphan shape, asserted against the classifier. Three
  regressions from the prototype become permanent test cases:
  - `rg ms-playwright` must not classify as a simulator
  - a live-parented dev server must not classify as an orphan
  - Maestro's JVM must classify as a simulator
- **Budget math tests** across 8/16/24/32/64 GB, asserting the 24 GB row reproduces the
  author's configuration.
- `shellcheck` on all scripts; `bats-core` as the test runner.
- GitHub Actions on a macOS runner: shellcheck, bats, and a
  `brew install --build-from-source` smoke test.
- The SIGKILL paths get a documented manual checklist. They cannot be safely unit-tested.

## Versioning

Semver tags on `alextitov19/memcap`; the formula pins a tarball sha256. `brew upgrade`
replaces scripts and never touches `~/.config/memcap/memcap.conf`.

## Open questions

None. All design decisions above are settled.
