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

The point is not to make your machine slower or your agents weaker. It is to
guarantee that when you join a video call, there is RAM left to join it with.

```
$ memcap status
memcap — /Users/you/.config/memcap/memcap.conf

  agents + everything they spawn   6.93 GB / 10 GB budget
    of which leaked/orphaned       0.00 GB
    of which sims/playwright       0.93 GB
    net of sims (drives tier 2)    6.00 GB
  docker VM + helpers              6.47 GB / 6 GB ceiling
  ---------------------------------------------------------
  combined                        13.40 GB / 16 GB budget
  system memory available          25%
```

## Requirements

- **macOS** (Apple Silicon or Intel). Linux is explicitly out of scope — the
  mechanics differ entirely, and a port would share almost no code.
- **Homebrew**, for install and for the background service.
- **`jq`**, installed automatically as a formula dependency.
- **Docker Desktop is optional.** Without it, memcap skips the Docker module and
  budgets agents only. OrbStack, Colima, and Podman are measured but cannot be
  capped — no VM ceiling exists to set.

Only the system `bash` (3.2) is required at runtime; no newer shell is needed.

## Install

```bash
brew install alextitov19/memcap/memcap
memcap init
```

`memcap init` detects your total RAM, core count, and which coding agents are
installed, proposes a budget split, and writes `~/.config/memcap/memcap.conf`. It
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
acts through three tiers, and each one is deliberately narrow.

**Tier 1 — orphans.** A process is only touched here if its parent is already dead
(`ppid == 1`), its command line matches a known dev-server pattern (`vite`, `next`,
`nodemon`, `uvicorn`, and similar), and it lives under a sweep root memcap has
learned (see below). A dead parent means no live terminal or session owns the
process anymore — there is nothing left for it to be doing.

**Tier 2 — over-budget dev servers.** Only reached when agents are still over
budget after tier 1 has run. Only processes older than `TIER2_MIN_AGE_SEC` (default
300 seconds) are eligible. A dev server runs for hours; a `vite build` or test run
lasts seconds. If every candidate is younger than the age gate, memcap logs that,
sends a notification, and kills nothing on that pass.

**Tier 3 — idle simulators.** iOS Simulators, Android emulators, Playwright
browsers, and Maestro processes. Idle is measured directly, not inferred: memcap
samples each tracked simulator's own accumulated CPU time, the same way `top`
would show you a process is doing nothing. A booted-but-unused simulator burns
approximately zero CPU; once one has stayed flat across `SIM_ACTIVE_CPU_SEC`
(default 2) of real work for the full `SIM_IDLE_GRACE_SEC` grace period, it is
reclaimed. Real work resets that pid's own clock, so a simulator mid-test is
never mistaken for an idle one no matter how long an agent session has been open.

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
CPU-flat for `MOBILE_TOOLING_IDLE_SEC` (default 60 — shorter than
`SIM_IDLE_GRACE_SEC`, since a quiet minute is likelier idle for a CLI tool or
server than for a simulator) no longer blocks the reap; an actual `maestro`
flow, `xcodebuild`, or `detox` run burns real CPU and keeps vetoing for as
long as it does. One consequence worth knowing: `expo run:ios`,
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

Four guarantees hold across all three tiers, enforced at a single choke point
(`mc_kill_pids`) that every kill routes through:

- It never kills an agent CLI itself, or anything in memcap's own process
  ancestry — that check happens once, in the choke point, not per tier.
- Nothing runs at all while `memcap off` is set — including a manual
  `memcap clean`. Pausing is absolute, not "paused except when you ask directly."
- `MC_DRY_RUN=1` reports exactly what would be killed and why, without killing
  anything.
- Every kill is logged to `actions.log` with the reason and the process line, so
  after the fact you can see exactly what happened and why.

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

Past `STALE_PASS_SEC` (default 300, five ticks of the 60-second service
interval) with no `memcap off` in effect, or if it has never run since install,
`status` says so plainly and gives the exact command to fix it:

```
  last enforcement pass            3d ago
  MEMCAP IS PROBABLY NOT RUNNING -- memcap service install
```

`memcap service install` is idempotent — safe to run whether the LaunchAgent
was never installed, was unloaded somehow, or is fine already — see "Install"
above and `memcap service status` below.

A paused service with a fresh heartbeat still reads as paused, not dead — the
heartbeat answers "is the daemon ticking," a different question from "is it
enforcing," which the `ENFORCEMENT PAUSED` line already covers on its own. Note
that a fresh heartbeat only means a pass _ran_, not that anything needed doing
— silence in `actions.log` during a genuinely idle stretch is still normal;
it's a stale heartbeat spanning time you know you were working that indicates
a problem. `memcap service status` and `tail`ing `actions.log` remain useful
for a deeper look, but you shouldn't need them just to answer "is this
running."

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
| `memcap off`                    | Pause switch. See above.                                                                                                                                                                                                                                          |
| `memcap on`                     | Resume enforcement.                                                                                                                                                                                                                                               |
| `memcap profile [name]`         | List the budget profiles (`balanced`, `stacks`, `mobile`), or switch to one — rewrites `DOCKER_BUDGET_GB` in the config.                                                                                                                                          |
| `memcap docker apply [--force]` | Push `DOCKER_BUDGET_GB`/`DOCKER_CPUS` to the Docker Desktop VM ceiling, plus swap/Resource Saver/auto-pause — see Docker section above; restarts Docker Desktop to do it. Normally refuses while containers are running; `--force` overrides that and stops them. |
| `memcap service install`        | Write and load memcap's own LaunchAgent. Idempotent — safe to re-run after a config change or just to confirm it's loaded. Migrates away from an older Homebrew-owned LaunchAgent if one is found.                                                                |
| `memcap service uninstall`      | Unload and remove memcap's own LaunchAgent (and a lingering Homebrew-owned one, if present). A no-op if nothing is installed.                                                                                                                                     |
| `memcap service status`         | Report whether memcap's LaunchAgent is installed and loaded.                                                                                                                                                                                                      |
| `memcap uninstall`              | Remove memcap's own LaunchAgent and state. Keeps your config. See Uninstall below.                                                                                                                                                                                |
| `memcap help`                   | Usage summary.                                                                                                                                                                                                                                                    |

## Configuration

Config lives at `~/.config/memcap/memcap.conf`, written by `memcap init` and never
touched by `brew upgrade`. Edit it freely; every key below falls back to a
computed default if it is absent or commented out.

| Key                        | Default           | What it does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| -------------------------- | ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TOTAL_BUDGET_GB`          | computed at init  | Combined ceiling for agents + Docker + sims. Computed as `total RAM − reserve`, where `reserve` is 35% of total RAM clamped to 6–16 GB, and the result is floored at 40% of the machine so small laptops still get a usable budget.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `DOCKER_BUDGET_GB`         | computed at init  | Docker's VM memory ceiling in GB — the one hard limit in the system, applied by `memcap docker apply`. Computed as 40% of `TOTAL_BUDGET_GB` clamped to 2–12 GB, then capped further so agents always keep at least 2 GB.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `DOCKER_CPUS`              | 55% of core count | Docker's VM CPU ceiling, set alongside the memory ceiling.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `SOFT_TRIGGER`             | `0.80`            | Fraction of the agents' budget that, once crossed, triggers a tier-1 sweep before anything is killed outright.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `MIN_FREE_PCT`             | `15`              | If system-wide free memory drops below this percentage, a tier-1 sweep runs regardless of whether the agent budget itself has been crossed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `TIER2_MIN_AGE_SEC`        | `300`             | Minimum age, in seconds, a dev server must have reached before tier 2 will consider killing it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `SIM_IDLE_GRACE_SEC`       | `600`             | How long a tracked simulator, emulator, or Playwright browser must show flat CPU (see `SIM_ACTIVE_CPU_SEC`) before tier 3 will shut it down, with no active-mobile-tooling or hands-on-mobile veto in effect. Each tracked process earns its own clock, starting the moment memcap first sees it, not a single clock shared by every simulator on the machine — booting a second simulator by hand does not inherit however long an unrelated, already-idle process has been sitting there. Tier 3 only acts once every currently-tracked process has individually cleared the grace, so one freshly-booted simulator holds the whole pass back rather than being swept in early alongside an older one. The clock for a process resets the moment its own CPU time advances meaningfully; a veto blocking the actual reap never erases accumulated idle history the way an unconditional wipe once did. |
| `SIM_ACTIVE_CPU_SEC`       | `2`               | How many CPU-seconds a tracked simulator or active-mobile-tooling process must accumulate since its clock last reset before memcap considers it "in use" and resets the clock again. A booted-but-unused simulator, or an idle `maestro` MCP server, burns approximately zero CPU, so this is deliberately small — real work should register almost immediately, biasing toward not reclaiming when in doubt.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `MOBILE_TOOLING_IDLE_SEC`  | `60`              | How long `maestro`, `xcodebuild`, `expo`, `react-native`, or `detox` must show flat CPU (see `SIM_ACTIVE_CPU_SEC`) before it stops vetoing tier 3. Shorter than `SIM_IDLE_GRACE_SEC` by default — a CLI tool or background server going quiet for a minute is likelier genuinely idle than a simulator is.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `TIER3_REQUIRE_NO_SESSION` | `0`               | Set to `1` to restore memcap's pre-0.3.0 behavior: tier 3 never reaps while any agent session is alive, full stop, regardless of CPU idleness. The original design, kept as an opt-in for anyone who wants the maximally conservative posture — see the Tier 3 section above for why it's no longer the default.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `EXTRA_AGENTS`             | empty             | Extra agent binary names to recognize, beyond the built-in list (`claude codex cursor-agent aider gemini amp opencode goose crush`). Also spliced into the process-classification regex, so avoid regex metacharacters in the names you add.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `LOG_THROTTLE_SEC`         | `1800`            | How long a repeating per-pass status line (tier 3 declining, or the combined cap being exceeded) is suppressed after it first logs, so a condition that holds across many consecutive polls doesn't drown `actions.log`'s kill records. Killed-process records are never throttled. Set to `0` to log every occurrence, e.g. while debugging. A state change — the condition stopping and later holding again — always gets its own line even inside the window.                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `STALE_PASS_SEC`           | `300`             | How long since the last completed `watch` pass before `status` reports the service as probably not running, rather than just "quiet." Five ticks of the default 60-second service interval — long enough to absorb one missed tick without a false alarm.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |

## Files on disk

Config, at `~/.config/memcap/memcap.conf` (override with `MEMCAP_CONFIG_HOME`):
your settings, as described above. Not removed by `memcap uninstall`.

State, at `~/.local/state/memcap/` (override with `MEMCAP_STATE_HOME`):

- `actions.log` — an append-only record of memcap's enforcement decisions, not
  only kills: it also logs a tier-2 pass that found nothing old enough to kill,
  a tier-3 `simctl shutdown all`, a tier-3 reclaim's audit detail (which pid,
  how long it was idle, its flat CPU baseline), tier 3 declining because active
  mobile tooling or hands-on mobile work is in progress (or, with
  `TIER3_REQUIRE_NO_SESSION=1`, because an agent session is alive), `watch`
  refusing to act against a misconfigured budget, and the combined cap being
  exceeded by simulator memory that tier 2 correctly won't touch. If you're
  wondering why memcap did or didn't do something, this is where to look. Kill
  records are logged every time; the two lines that would otherwise repeat on
  every single pass — tier 3 declining, and the combined cap being exceeded —
  are throttled to at most one per `LOG_THROTTLE_SEC` so they don't drown the
  kill records in a long-running install.
- `log-throttle/` — one stamp per throttled log key (see `LOG_THROTTLE_SEC`
  above), cleared the moment that key's condition stops holding so the next
  occurrence logs immediately rather than waiting out a stale window.
- `roots` — the learned sweep roots, one canonicalized path per line.
- `last-pass` — epoch seconds of the last completed `watch` pass, written on
  every path through `watch` including paused and misconfigured-budget early
  returns. What `status` reads to report the service as running, stale, or
  never started — see "Checking it is actually running" above.
- `paused` — present exactly when `memcap off` is in effect; its absence means
  enforcement is active.
- `.notified` — a timestamp used to rate-limit desktop notifications to at most
  one every 5 minutes.
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
