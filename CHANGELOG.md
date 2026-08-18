# Changelog

## Unreleased

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
