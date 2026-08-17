# AGENTS.md

Guidance for coding agents working in this repository. Human contributors want
[CONTRIBUTING.md](CONTRIBUTING.md) instead; everything there applies here too.

## What this tool is

memcap is a macOS daemon that sends `SIGTERM`/`SIGKILL` to processes unattended,
on other people's machines, every 60 seconds. A defect here does not produce a
stack trace — it kills a developer's work, or silently stops protecting them.
Both failure directions have already happened in this project's history.

Weight your judgment accordingly: prefer the change that fails closed, and treat
"the tests pass" as necessary rather than sufficient.

## Before you finish

```bash
shellcheck bin/memcap libexec/*.sh tests/*.bats tests/*.bash
for f in bin/memcap libexec/*.sh tests/*.bash; do /bin/bash -n "$f" || break; done
bats tests/
env HOME="$(mktemp -d)" bats tests/
```

Run all four. Report the actual counts, and report failures as failures.

## Safety rules while working in this repo

The machine you are running on very likely has memcap installed and enforcing,
plus real containers and real dev servers belonging to someone's job.

- **Never run `memcap watch`, `memcap clean`, or `memcap uninstall` for real.**
  Always `MC_DRY_RUN=1`. A test run must never terminate a real process.
- **Never run `memcap docker apply`**, never `--force`, never quit or start
  Docker, and never edit `settings-store.json`. That path restarts Docker
  Desktop and stops running containers.
- **Sandbox every CLI invocation** with `MEMCAP_CONFIG_HOME` and
  `MEMCAP_STATE_HOME`. Do not write to the real `~/.config/memcap/` or
  `~/.local/state/memcap/` — the state directory is a live audit log.
- **`export MC_DRY_RUN=1` belongs in `setup()`** of any test file that reaches
  enforcement. Do not remove it. It exists because a test suite run twice reached
  a real tier-2 kill decision on a live machine, stopped only by the age gate.

## Traps this codebase has already fallen into

Each of these shipped, passed review, and was found later. They are the reason
the constraints exist; do not re-derive them the hard way.

**`bash -n file1 file2` parses only `file1`.** The CI parse check silently
covered nothing for eleven changes. Loop over files one at a time. More
generally: when you add a check, verify it can actually _fail_ — write a
deliberate error and confirm it goes red. Five separate variants of "a check
that cannot fail" reached `main` in this repo.

**`ps -o etimes=` is Linux-only.** On macOS it prints the entire keyword list to
stdout and exits 0, so the variable holds a long non-numeric string, `[ -z ]` is
false, and the numeric comparison fails open. The tier-2 age gate — the only
thing protecting a running build from being killed — was inert for eight days.
Use `mc_etime_secs`.

**`ps` RSS is the wrong metric everywhere.** It counts shared pages once per
process and under-counts compressed memory. A booted simulator's 266 processes
summed to 16.18 GB of RSS against 6.42 GB of actual footprint, which left the
watchdog permanently over budget and firing every 60 seconds. Use
`mc_ps_snapshot`.

**Match `argv[0]`, not the whole command line.** Matching anywhere in the command
line classified `rg ms-playwright` as a browser and made it a kill candidate.

**Simulators are not descendants of the agent.** iOS Simulators belong to
`CoreSimulatorService` and Android emulators daemonize, so a process-tree walk
reports 0 GB while they hold 5 GB. They are matched by command pattern instead.

**Canonical and raw paths are different strings.** `/tmp` and `/var` are symlinks
on macOS. Recorded sweep roots are canonicalized; process command lines are not.
Comparing one against the other fails silently, and silent failure in tier 1
means the tool simply stops working with no error anywhere.

**A test that stubs a function must define the stub _after_ sourcing.** Sourcing
redefines it. And a function stub cannot reach a subprocess at all — `bin/memcap`
re-sources its own libraries, discarding any override. Use an environment
variable (`MC_DOCKER_RUNTIME`, `MC_NO_TOP`, `MC_DRY_RUN`).

## Layout

| Path                  | Contents                                                           |
| --------------------- | ------------------------------------------------------------------ |
| `bin/memcap`          | Dispatcher. Sources `libexec/*.sh`, routes subcommands.            |
| `libexec/common.sh`   | Config/state paths, logging, throttling, pause check, notify.      |
| `libexec/budget.sh`   | Budget arithmetic. Pure functions, heavily unit-tested.            |
| `libexec/measure.sh`  | `mc_ps_snapshot` — the footprint-corrected process table.          |
| `libexec/classify.sh` | Splits a process table into agent/docker/orphan/sim buckets.       |
| `libexec/roots.sh`    | Learned sweep roots: canonicalize, safety-check, persist.          |
| `libexec/enforce.sh`  | The three tiers, and `mc_kill_pids` — the single kill choke point. |
| `libexec/docker.sh`   | Runtime detection and the VM ceiling.                              |
| `tests/`              | bats suite. `helper.bash` holds the assertion helpers.             |

Every kill routes through `mc_kill_pids`. If you are adding a code path that
terminates a process and it does not go through that function, that is the bug.

## Assertions

Use `assert_contains` / `assert_not_contains` / `assert_matches` from
`tests/helper.bash`. Do not write bare `[[ ]]` conditions as assertions — a bare
`[[ ]]` as the final statement of a bats test reports a pass on failure. The
whole suite was once audited and rewritten for exactly this.

A test that cannot fail is worse than no test: it reports safety that does not
exist.

## Reporting

State what you verified and how. If you ran the suite locally but not in the
no-Docker configuration, say that. If CI status is unknown to you, say it is
unknown rather than inferring it from a local pass — this project shipped five
consecutive red CI runs while local commands were green.
