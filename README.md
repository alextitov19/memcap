# memcap

Keeps AI coding agents (and the processes they spawn) inside a RAM budget on macOS.

## Why

AI coding agents leak long-lived processes: orphaned dev servers from restarted
sessions, simulators and browsers that survive a crashed test run, a Docker Desktop
VM balloon that never gives memory back. On a RAM-constrained machine that ends in
swap thrash and, eventually, a hard shutdown. memcap watches for this and enforces
a budget so you don't have to think about it.

## Status

Early scaffold — command surface is being built out. See
`docs/superpowers/specs/2026-08-12-memcap-design.md` for the full design and
`docs/superpowers/plans/2026-08-12-memcap.md` for the implementation plan.

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

## Usage

```bash
memcap help
```
