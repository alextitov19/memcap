# Contributing to memcap

Thanks for taking the time. memcap kills processes on other people's machines
unattended, so this guide is mostly about the parts where being careless would be
expensive.

## Reporting a bug

Open an issue using the **Bug report** template. The two things that matter most:

1. **`memcap status` output** and your `~/.config/memcap/memcap.conf`.
2. **The relevant lines from `~/.local/state/memcap/actions.log`.** memcap logs
   why it did or didn't act, not only what it killed. If memcap killed something
   it shouldn't have, or ignored something it should have caught, that file
   usually contains the answer.

If memcap killed something you needed, say so plainly and include the process's
command line. That is the highest-priority class of bug in this project.

**Something being killed right now?** `memcap off` halts all enforcement
immediately, including a manual `memcap clean`. You do not need to uninstall
anything, and you can file the issue afterward.

## Proposing a change

Open an issue before writing code for anything beyond a bug fix. memcap
deliberately does not want to become a general-purpose resource manager — see
"Scope" below — so it's worth agreeing an idea is in scope before you spend an
evening on it.

## The pull request workflow

You will not have push access, which is expected. The flow is:

1. **Fork** the repository.
2. **Branch** from `main` — `fix/orphan-match-symlink`, `feat/podman-ceiling`.
3. Make the change, **with tests**.
4. Run the full local check (below) until it is clean.
5. **Open a pull request against `main`.** CI runs automatically.
6. A maintainer reviews and merges. `main` is protected: it cannot be pushed to
   directly, and CI must be green before a merge is possible.

Small, focused pull requests get reviewed faster. A PR that fixes one bug and
also reformats four files is hard to review and will take longer.

## Before you open a PR

```bash
shellcheck bin/memcap libexec/*.sh tests/*.bats tests/*.bash
for f in bin/memcap libexec/*.sh tests/*.bash; do /bin/bash -n "$f" || break; done
bats tests/
env HOME="$(mktemp -d)" bats tests/      # must also pass with no Docker Desktop
```

All four must pass. CI runs the first three; the fourth is how you catch the
portability failures CI would catch for you a round later.

## Constraints that will fail your build

**bash 3.2.** macOS ships bash 3.2 (2007) and memcap targets it so it works out
of the box everywhere. No `mapfile`, `readarray`, `declare -A`, `local -n`,
`${var,,}`, `&>>`, or `;;&`. `bash -n` catches syntax; the semantic ones it does
not, so check before reaching for a modern builtin.

**Note that `bash -n file1 file2` only parses `file1`.** Loop over files
individually — this exact mistake let eleven changes reach `main` unchecked.

**Tests must pass without Docker Desktop.** CI runners have none, so
`mc_docker_runtime` returns `none` there. Use the `MC_DOCKER_RUNTIME` environment
variable to make runtime-dependent tests deterministic. A function stub will not
work for tests that invoke `bin/memcap` as a subprocess: it re-sources
`libexec/docker.sh`, which redefines the function and discards your override. An
environment variable survives into the subprocess.

**No test may reach a real kill, a real `docker` restart, or the real config.**
Enforcement tests export `MC_DRY_RUN=1` in `setup()`. Sandbox anything that
writes with `MEMCAP_CONFIG_HOME` and `MEMCAP_STATE_HOME`. Contributors run this
suite on working machines with real containers and real dev servers on them.

**`ps` RSS is the wrong metric.** It counts shared pages once per process (a
booted simulator's 266 processes sum to 16.18 GB of RSS against 6.42 GB of real
footprint) and under-counts compressed memory. Use `mc_ps_snapshot`, which merges
a `top` sample so every size is physical footprint.

**macOS is not Linux.** `ps -o etimes=` is Linux-only; macOS prints its entire
keyword list to stdout instead of erroring, so the value silently becomes a
non-numeric string and every numeric guard downstream falls open. Use
`mc_etime_secs`. This exact bug made the tier-2 age gate inert for eight days.

## Changes to enforcement

Anything touching `libexec/enforce.sh` — or any code that decides what gets
killed — is reviewed harder, and needs two extra things in the PR description:

- **What the change widens.** If more processes become eligible, say so
  explicitly and explain why nothing that is currently spared for a good reason
  loses that protection.
- **Which guarantees still hold.** The `ppid == 1` orphan gate, the
  `TIER2_MIN_AGE_SEC` age gate, `mc_root_is_safe`, the TOCTOU redirect check, the
  agent-CLI and self-ancestry exclusions in `mc_filter_protected`, and the
  `memcap off` pause. These are the reasons it is safe to let this tool run
  unattended.

A regression test that fails before your fix and passes after is worth more here
than any amount of explanation. Include the failing output in the PR.

## Scope

memcap intends to stay small. Things it deliberately does not do:

- **Linux.** Different mechanics end to end (cgroups v2, systemd, no Docker
  Desktop VM). Not a port — a different tool.
- **Capping processes you started yourself.** Only agent-spawned work is in
  scope.
- **User-defined process groups and general resource management.** That trades
  away install-and-forget, which is the whole point of the tool.

New agent CLIs are always welcome — the built-in list is a starting point, not a
closed set.

## Commit messages

Explain _why_, not only _what_. Reference the failure mode a change prevents. The
history is the only place some of these constraints are written down, and several
commits in it are the sole record of a bug that took days to find.

## License

Contributions are accepted under the [MIT License](LICENSE).
