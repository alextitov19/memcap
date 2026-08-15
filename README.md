# memcap

memcap is a macOS command-line tool and background service that keeps AI coding
agents, and the processes they spawn, inside a memory budget. It measures actual
physical footprint on a roughly 60-second cycle and, when agents drift over budget,
reaps processes in three narrowly-scoped tiers rather than a blanket sweep. It also
sets Docker Desktop's VM memory as a hard ceiling, since that is the one limit macOS
will actually enforce; everything else it does is a soft policy it polices itself.

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
browsers, and Maestro processes, and only when no agent session is alive. It never
runs while Xcode, Android Studio, or Simulator.app itself is open, so it will not
pull a device out from under you while you are testing by hand. One consequence
worth knowing: `expo run:ios`, `react-native run-ios`, and Maestro's iOS flows all
launch the simulator through Simulator.app's own UI, so any of them keeps tier 3
switched off for as long as Simulator.app stays open — including well after the
command that launched it has exited — not just while you're actively looking at it.

**Simulator memory counts toward the combined cap, but only tier 3 can reclaim
it.** A booted simulator or a Playwright-driven browser is counted into the same
agent-side total tier 1 and tier 2 measure, because it is agent-adjacent work and
should not be invisible to the budget. But tier 3 — the only tier that can shut a
simulator down — refuses outright whenever an agent session is alive, which is the
tool's normal operating state. So tier 1's soft trigger and tier 2's kill decision
are measured net of simulator memory (agents' own footprint, not what a booted
simulator or headless browser is using): sims still count toward `status`'s
combined figure and are still reclaimed by tier 3 once idle, they just cannot be
the reason a dev server gets killed.

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

## Install

```bash
brew install alextitov19/memcap/memcap
memcap init
```

`memcap init` detects your total RAM, core count, and which coding agents are
installed, proposes a budget split, and writes `~/.config/memcap/memcap.conf`. It
will offer to start the background service (`brew services start memcap`), which
is what calls `memcap watch` on a recurring cycle; you can decline and start it
later, or skip enforcement entirely by answering "no" to the enforce prompt, which
leaves memcap paused from the start.

## Commands

| Command | What it does |
|---|---|
| `memcap init` | Interactive setup: detect the machine, write the config, optionally start the service. |
| `memcap status` | One-shot snapshot of current agent/Docker footprint against budget, and free system memory. |
| `memcap watch` | Runs a single enforcement pass (tiers as needed). This is what the background service calls repeatedly. |
| `memcap clean` | Manual sweep: tier 1 (orphans) and tier 3 (idle sims) only. No-ops while paused. |
| `memcap off` | Pause switch. See above. |
| `memcap on` | Resume enforcement. |
| `memcap profile [name]` | List the budget profiles (`balanced`, `stacks`, `mobile`), or switch to one — rewrites `DOCKER_BUDGET_GB` in the config. |
| `memcap docker apply [--force]` | Push `DOCKER_BUDGET_GB`/`DOCKER_CPUS` to the Docker Desktop VM ceiling, plus swap/Resource Saver/auto-pause — see Docker section above; restarts Docker Desktop to do it. Normally refuses while containers are running; `--force` overrides that and stops them. |
| `memcap uninstall` | Stop the service and remove memcap's state. Keeps your config. See Uninstall below. |
| `memcap help` | Usage summary. |

## Configuration

Config lives at `~/.config/memcap/memcap.conf`, written by `memcap init` and never
touched by `brew upgrade`. Edit it freely; every key below falls back to a
computed default if it is absent or commented out.

| Key | Default | What it does |
|---|---|---|
| `TOTAL_BUDGET_GB` | computed at init | Combined ceiling for agents + Docker + sims. Computed as `total RAM − reserve`, where `reserve` is 35% of total RAM clamped to 6–16 GB, and the result is floored at 40% of the machine so small laptops still get a usable budget. |
| `DOCKER_BUDGET_GB` | computed at init | Docker's VM memory ceiling in GB — the one hard limit in the system, applied by `memcap docker apply`. Computed as 40% of `TOTAL_BUDGET_GB` clamped to 2–12 GB, then capped further so agents always keep at least 2 GB. |
| `DOCKER_CPUS` | 55% of core count | Docker's VM CPU ceiling, set alongside the memory ceiling. |
| `SOFT_TRIGGER` | `0.80` | Fraction of the agents' budget that, once crossed, triggers a tier-1 sweep before anything is killed outright. |
| `MIN_FREE_PCT` | `15` | If system-wide free memory drops below this percentage, a tier-1 sweep runs regardless of whether the agent budget itself has been crossed. |
| `TIER2_MIN_AGE_SEC` | `300` | Minimum age, in seconds, a dev server must have reached before tier 2 will consider killing it. |
| `SIM_IDLE_GRACE_SEC` | `600` | How long simulators, emulators, and Playwright browsers must sit idle — no agent session alive, Xcode/Android Studio/Simulator.app all closed — before tier 3 will shut them down. Each tracked process earns its own clock, starting the moment memcap first sees it idle, not a single clock shared by every simulator on the machine — booting a second simulator by hand does not inherit however long an unrelated, already-idle process has been sitting there. Tier 3 only acts once every currently-tracked process has individually cleared the grace, so one freshly-booted simulator holds the whole pass back rather than being swept in early alongside an older one. The clock for a process restarts if an agent session reappears or one of those apps opens, and every clock clears once a reap happens. |
| `EXTRA_AGENTS` | empty | Extra agent binary names to recognize, beyond the built-in list (`claude codex cursor-agent aider gemini amp opencode goose crush`). Also spliced into the process-classification regex, so avoid regex metacharacters in the names you add. |

## Files on disk

Config, at `~/.config/memcap/memcap.conf` (override with `MEMCAP_CONFIG_HOME`):
your settings, as described above. Not removed by `memcap uninstall`.

State, at `~/.local/state/memcap/` (override with `MEMCAP_STATE_HOME`):

- `actions.log` — an append-only record of memcap's enforcement decisions, not
  only kills: it also logs a tier-2 pass that found nothing old enough to kill,
  a tier-3 `simctl shutdown all`, tier 3 declining because an agent session is
  alive or hands-on mobile work is in progress, `watch` refusing to act against
  a misconfigured budget, and the combined cap being exceeded by simulator
  memory that tier 2 correctly won't touch. If you're wondering why memcap did
  or didn't do something, this is where to look.
- `roots` — the learned sweep roots, one canonicalized path per line.
- `paused` — present exactly when `memcap off` is in effect; its absence means
  enforcement is active.
- `.notified` — a timestamp used to rate-limit desktop notifications to at most
  one every 5 minutes.
- `sims-idle/` — one timestamp file per tracked simulator/emulator/browser
  process (named by pid), marking when memcap first saw it idle with no agent
  session alive; tier 3 waits out `SIM_IDLE_GRACE_SEC` from each pid's own
  stamp before reaping, and every stamp is cleared whenever that condition
  stops holding, or a reap happens.

All of the above is removed by `memcap uninstall`.

## Uninstall

```bash
memcap uninstall
```

This stops the background service and deletes everything under
`~/.local/state/memcap/` — the action log, the learned sweep roots, and the pause
marker. It deliberately leaves your config at `~/.config/memcap/memcap.conf` in
place; it prints that path so you know where it is, and does not delete it for
you, because you may be about to reinstall rather than leave for good. Remove it
yourself if you want a clean slate, then:

```bash
brew uninstall memcap
```

## Development

Requires `bats-core` and `shellcheck`:

```bash
brew install bats-core shellcheck
```

Run the tests:

```bash
bats tests/*.bats
```

Lint every shell file before committing:

```bash
shellcheck bin/memcap libexec/*.sh
```
