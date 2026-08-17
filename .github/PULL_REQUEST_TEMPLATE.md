<!-- Thanks for contributing. Keep this short — the checks matter more than the prose. -->

## What this changes

<!-- One or two sentences. Link the issue if there is one: Fixes #123 -->

## Why

<!-- The failure mode this prevents, or the behavior it enables. -->

## Verification

- [ ] `shellcheck bin/memcap libexec/*.sh tests/*.bats tests/*.bash`
- [ ] `bash -n` against system bash 3.2, **looping over files individually**
- [ ] `bats tests/`
- [ ] `env HOME="$(mktemp -d)" bats tests/` — passes with no Docker Desktop

Pass counts:

<!-- e.g. "151/151 both with and without Docker Desktop" -->

## If this touches enforcement

<!-- Delete this section if it does not touch libexec/enforce.sh or anything
     deciding what gets killed. -->

**What it widens:** <!-- Do more processes become eligible? If so, why does
nothing currently spared for a good reason lose that protection? -->

**Guarantees still holding:** <!-- ppid==1 orphan gate, TIER2_MIN_AGE_SEC age
gate, mc_root_is_safe, the TOCTOU redirect check, mc_filter_protected's agent-CLI
and self-ancestry exclusions, the `memcap off` pause. -->

**Regression test:** <!-- Paste the failing output from before the fix. A test
that fails before and passes after is worth more than any explanation. -->
