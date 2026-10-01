# Nested worker controls

The red-pressure incident showed why a scheduler allocation and an applied tool
limit are different. A compound shell launched a Jest workload with a 1 GiB
startup estimate. Its measured group footprint later reached 13.44 GiB. The
registry reported four allocated workers, but the compound shell prevented the
existing argv limiter from reaching Jest.

Managed launches install a small Node preload through the inherited environment.
It recognizes the actual executable entrypoint and package identity for Jest,
Vitest and Playwright test CLIs. It caps their worker arguments before their CLI
parses them, including when an intervening shell, package script or helper starts
the process. It leaves the shell text, pipelines, quoting, outputs and exit status
alone. Lower explicit limits and serial Jest mode are retained. Other Node
applications and Playwright installation commands receive no added arguments.

The preload is scoped to managed launches. Native lightweight calls and commands
launched while memcap is paused retain their original environment. Existing
running jobs retain their original launch settings; installation does not restart,
cancel, or claim to retrofit them.

This is a worker-control mechanism, not a kernel memory cap. Programmatic runner
APIs, tools outside the supported entrypoints and children that intentionally
replace their environment remain outside this mechanism. A worker can itself use
substantial memory. Recorded `workers` means allocated concurrency, not a count
of observed child processes or proof that every descendant respects it.

Adaptive allocation also considers physical startup headroom: after its existing
margin and outstanding reservations, it selects at most one worker per available
GiB, with one as the minimum execution concurrency. That minimum grants no
admission: all ordinary memory and pressure checks still apply. This is a startup
heuristic, not a claim that each worker consumes at most one GiB. Strict mode
retains its configured allocation. Estimates are conditioned on the selected
worker count and control strategy.

Admission events record `worker_control_version` and `node_worker_limit`. The
latter is the installed runtime ceiling for supported CLI entrypoints, not a
measurement of actual child count or coverage of programmatic APIs.

Compare the new worker-control strategy separately from older uncapped runs.
Keep the observed pre-fix peak and raw incident evidence: a smaller worker count
does not prove a proportional memory reduction. Yellow admission still requires
physical headroom and current pressure checks; no live budgets are increased by
this fix.
