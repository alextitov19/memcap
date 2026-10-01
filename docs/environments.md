# Shared memory and disposable test environments

All Claude and Codex sessions share one Mac. A test's memory requirement includes
its Docker services, simulator/browser helpers and compiler/test workers. Starting
a 12 GiB stack and then queueing its tests can block that session and everyone else.

For an **exclusive disposable Compose test stack**, reserve the combined demand
before creating any container:

```sh
memcap environment run --memory 12 --compose compose.test.yaml -- go test ./integration
```

The number is an estimate of the stack **plus** the test, not a Docker VM setting
or a kernel hard cap. Choose it from actual workload measurements. The ordinary
queue admits the whole operation; only then does Compose start, using a unique
`memcap-…` project. Images must already exist (`--no-build --pull never`). The
workload inherits `COMPOSE_PROJECT_NAME` and `MEMCAP_ENVIRONMENT`, uses the same
managed lease, and cannot deadlock on a second reservation for its own tests.
Other sessions still use the shared admission policy. A request that cannot fit
stays pending rather than forcing the Mac into red.

The Compose behavior follows Docker's documented
[create without starting](https://docs.docker.com/reference/cli/docker/compose/up/)
and [start with readiness waiting](https://docs.docker.com/reference/cli/docker/compose/start/)
options. The runner records container identities between those steps.

After the workload ends, the enforcement choke point stops only containers with
the verified project label and immutable creation identity. Containers and volumes
are retained; no `down -v`, prune, Docker Desktop restart or VM reconfiguration is
performed. A stopped container can be inspected manually. Memory admission always
uses a fresh host measurement afterward: stopping a container does not promise
that its full VM footprint was returned to macOS.

If another session needs the stack, it must first run
`memcap environment claim TOKEN`. Release that claim when finished with
`memcap environment release TOKEN`. The originating session can `pin TOKEN` and
`unpin TOKEN`. Claims and pins prevent automatic disposal; they do not grant
permission to launch arbitrary work. `memcap environment list` shows private
ownership records. Unpinning after completion permits cleanup once the original
workload has recorded completion and the grace/identity checks succeed.

For abrupt termination, watchdog recovery requires the worker and originating
agent to be gone, no surviving registered process group, a continuous two-minute
grace, unchanged local Docker endpoint/container identities, and no live claims
or pin. Missing ownership and failed inspection retain the stack. Existing stacks
are **not** adopted using project names or old command text. Shared production-like
development stacks need explicit lifecycle design, not automatic shutdown.
When the worker recorded that its workload finished before leaving, the parent
agent may remain alive; releasing the last claim still permits cleanup after the
same grace and fresh identity checks.

This workflow prevents hold-and-wait for new disposable environments. It does not
freeze/resume arbitrary live stacks: their dependencies and persistent state are
not knowable from process idleness. If a stack is needed by the queued task,
restarting it before independently admitting that task would recreate the block.

Updates to managed guidance are delivered by the installed hook at the next
session/version refresh; regenerate agent integration when upgrading hook commands
or timeout settings. Keep separate Claude profiles selected explicitly. Existing
sessions may require reload; Codex hook trust remains user controlled. Old queued
supervisors retain their code and jobs: never duplicate them to adopt an upgrade.
