# v0.29 historical report audit

This ledger covers every open report at the October 7 backlog review. These
reports describe observations, not necessarily independent defects. The original
commands and decision histories are absent for many incidents. Do not equate
closing a historical report with retrospectively proving its cause or promising
that all future workload mixes fit available memory.

## Category contracts and release work

| Category | Verified behavior / release work | Regression coverage |
|---|---|---|
| lightweight-queued | Hooks and cached/explicit runners reclassify ordinary and unknown demand natively. Runner decision provenance is now retained. Fixed-memory requests and genuinely heavy mixed commands still require admission. | test_demand_policy.py, test_tool_compatibility.py, test_backlog.py, test_throughput.py |
| queue-stall | Fitting sessions rotate, subagents share turns, aged large jobs cannot block fitting peers when no finite work can drain. Fixes sampler handoff, child-birth learning loss and adaptive single-request hard-target rejection. | test_scheduler_lanes.py, test_adaptive_scheduler.py, test_backlog.py, test_productivity_027.py |
| polling-overhead | Wait remains read-only, returns when no owned work remains, names scope and blockers, supports native blocking completion and preserves bounded Stop fallback. Improve retained diagnostic evidence without adding an agent polling loop. | test_scheduler.py, test_productivity_027.py, test_completion_scope.py, feedback.bats |
| integration | Distinguish runtime mismatch, disabled hooks and modified/untrusted status. Stable hook commands remain compatible; trust approval remains an owner action. | test_integrate.py, test_backlog.py |
| unexpected-termination | Extend exact-device protection to direct live-agent drivers, not only MCP ancestry. Existing PID/UID/ancestry, age, pause and cancellation boundaries remain. Signals and application exits remain distinct evidence. | device_ownership.bats, enforce.bats, test_scheduler.py, test_idle_gc.py |
| measurement | Keep bad/stale/incomplete measurements from granting admission or lower estimates. Retain decision evidence preferentially and preserve bounded local report bundles and per-event coverage. | test_sampling_context.py, test_job_observation.py, test_backlog.py, measure.bats |
| missing-task-poll | Finite completion scopes retain parent/subagent ownership; bounded fallback is available without a native task API. Observation success is not workload success. | test_completion_scope.py, test_scheduler.py, test_productivity_027.py |
| queue-lock | Transient contention retains running supervision/reservations; corruption remains a distinct error. Sampler handoff remains outside registry locking. | test_open_issues.py, test_queue_storm.py, test_backlog.py |

## Release acceptance

Validation results and the release link are recorded in v029-execution.md. This
file is an inventory and contract map. Both full local configurations passed
656/656 tests; publication still requires the independent CI gate.
Historical reports without a reproducible cause receive an explicit historical /
unverified disposition, rather than a fabricated fix attribution. Any report
with a still-reproducing current defect must retain an open tracking issue.

New incidents retain a private bounded bundle when analytics is available. Raw
commands require separate owner-enabled temporary tracing; expiry remains visible
and never renews automatically. No command logs or private project paths belong
in public issue comments or release notes.

## Complete inventory

| Issue | Report category | Release-audit disposition |
|---|---|---|
| [#389](https://github.com/alextitov19/memcap/issues/389) | lightweight-queued | Historical/unverified; routing/evidence contract covered. |
| [#388](https://github.com/alextitov19/memcap/issues/388) | queue-stall | Historical/unverified; capacity/fairness contract covered. |
| [#387](https://github.com/alextitov19/memcap/issues/387) | integration | Historical/unverified; category contract covered. |
| [#386](https://github.com/alextitov19/memcap/issues/386) | lightweight-queued | Historical/unverified; category contract covered. |
| [#385](https://github.com/alextitov19/memcap/issues/385) | lightweight-queued | Historical/unverified; category contract covered. |
| [#384](https://github.com/alextitov19/memcap/issues/384) | lightweight-queued | Historical/unverified; category contract covered. |
| [#383](https://github.com/alextitov19/memcap/issues/383) | integration | Historical/unverified; category contract covered. |
| [#382](https://github.com/alextitov19/memcap/issues/382) | polling-overhead | Historical/unverified; category contract covered. |
| [#381](https://github.com/alextitov19/memcap/issues/381) | lightweight-queued | Historical/unverified; category contract covered. |
| [#380](https://github.com/alextitov19/memcap/issues/380) | integration | Historical/unverified; category contract covered. |
| [#379](https://github.com/alextitov19/memcap/issues/379) | measurement | Historical/unverified; category contract covered. |
| [#378](https://github.com/alextitov19/memcap/issues/378) | unexpected-termination | Historical/unverified; category contract covered. |
| [#377](https://github.com/alextitov19/memcap/issues/377) | polling-overhead | Historical/unverified; category contract covered. |
| [#375](https://github.com/alextitov19/memcap/issues/375) | queue-stall | Historical/unverified; category contract covered. |
| [#374](https://github.com/alextitov19/memcap/issues/374) | unexpected-termination | Historical/unverified; category contract covered. |
| [#373](https://github.com/alextitov19/memcap/issues/373) | integration | Historical/unverified; category contract covered. |
| [#372](https://github.com/alextitov19/memcap/issues/372) | integration | Historical/unverified; category contract covered. |
| [#371](https://github.com/alextitov19/memcap/issues/371) | lightweight-queued | Historical/unverified; category contract covered. |
| [#370](https://github.com/alextitov19/memcap/issues/370) | polling-overhead | Historical/unverified; category contract covered. |
| [#369](https://github.com/alextitov19/memcap/issues/369) | lightweight-queued | Historical/unverified; category contract covered. |
| [#368](https://github.com/alextitov19/memcap/issues/368) | lightweight-queued | Historical/unverified; category contract covered. |
| [#367](https://github.com/alextitov19/memcap/issues/367) | unexpected-termination | Historical/unverified; category contract covered. |
| [#365](https://github.com/alextitov19/memcap/issues/365) | lightweight-queued | Historical/unverified; category contract covered. |
| [#364](https://github.com/alextitov19/memcap/issues/364) | lightweight-queued | Historical/unverified; category contract covered. |
| [#363](https://github.com/alextitov19/memcap/issues/363) | missing-task-poll | Historical/unverified; category contract covered. |
| [#362](https://github.com/alextitov19/memcap/issues/362) | polling-overhead | Historical/unverified; category contract covered. |
| [#360](https://github.com/alextitov19/memcap/issues/360) | lightweight-queued | Historical/unverified; category contract covered. |
| [#359](https://github.com/alextitov19/memcap/issues/359) | lightweight-queued | Historical/unverified; category contract covered. |
| [#357](https://github.com/alextitov19/memcap/issues/357) | measurement | Historical/unverified; category contract covered. |
| [#356](https://github.com/alextitov19/memcap/issues/356) | queue-stall | Historical/unverified; category contract covered. |
| [#355](https://github.com/alextitov19/memcap/issues/355) | polling-overhead | Historical/unverified; category contract covered. |
| [#354](https://github.com/alextitov19/memcap/issues/354) | lightweight-queued | Historical/unverified; category contract covered. |
| [#353](https://github.com/alextitov19/memcap/issues/353) | polling-overhead | Historical/unverified; category contract covered. |
| [#352](https://github.com/alextitov19/memcap/issues/352) | integration | Historical/unverified; category contract covered. |
| [#351](https://github.com/alextitov19/memcap/issues/351) | lightweight-queued | Historical/unverified; category contract covered. |
| [#350](https://github.com/alextitov19/memcap/issues/350) | integration | Historical/unverified; category contract covered. |
| [#349](https://github.com/alextitov19/memcap/issues/349) | lightweight-queued | Historical/unverified; category contract covered. |
| [#347](https://github.com/alextitov19/memcap/issues/347) | lightweight-queued | Historical/unverified; category contract covered. |
| [#346](https://github.com/alextitov19/memcap/issues/346) | lightweight-queued | Historical/unverified; category contract covered. |
| [#345](https://github.com/alextitov19/memcap/issues/345) | lightweight-queued | Historical/unverified; category contract covered. |
| [#344](https://github.com/alextitov19/memcap/issues/344) | polling-overhead | Historical/unverified; category contract covered. |
| [#343](https://github.com/alextitov19/memcap/issues/343) | queue-stall | Historical/unverified; category contract covered. |
| [#342](https://github.com/alextitov19/memcap/issues/342) | integration | Historical/unverified; category contract covered. |
| [#340](https://github.com/alextitov19/memcap/issues/340) | polling-overhead | Historical/unverified; category contract covered. |
| [#339](https://github.com/alextitov19/memcap/issues/339) | queue-stall | Historical/unverified; category contract covered. |
| [#338](https://github.com/alextitov19/memcap/issues/338) | lightweight-queued | Historical/unverified; category contract covered. |
| [#336](https://github.com/alextitov19/memcap/issues/336) | lightweight-queued | Historical/unverified; category contract covered. |
| [#335](https://github.com/alextitov19/memcap/issues/335) | unexpected-termination | Historical/unverified; category contract covered. |
| [#333](https://github.com/alextitov19/memcap/issues/333) | polling-overhead | Historical/unverified; category contract covered. |
| [#332](https://github.com/alextitov19/memcap/issues/332) | polling-overhead | Historical/unverified; category contract covered. |
| [#331](https://github.com/alextitov19/memcap/issues/331) | queue-stall | Historical/unverified; category contract covered. |
| [#329](https://github.com/alextitov19/memcap/issues/329) | polling-overhead | Historical/unverified; category contract covered. |
| [#328](https://github.com/alextitov19/memcap/issues/328) | queue-stall | Historical/unverified; category contract covered. |
| [#327](https://github.com/alextitov19/memcap/issues/327) | lightweight-queued | Historical/unverified; category contract covered. |
| [#326](https://github.com/alextitov19/memcap/issues/326) | lightweight-queued | Historical/unverified; category contract covered. |
| [#325](https://github.com/alextitov19/memcap/issues/325) | lightweight-queued | Historical/unverified; category contract covered. |
| [#324](https://github.com/alextitov19/memcap/issues/324) | lightweight-queued | Historical/unverified; category contract covered. |
| [#323](https://github.com/alextitov19/memcap/issues/323) | queue-stall | Historical/unverified; category contract covered. |
| [#322](https://github.com/alextitov19/memcap/issues/322) | integration | Historical/unverified; category contract covered. |
| [#321](https://github.com/alextitov19/memcap/issues/321) | lightweight-queued | Historical/unverified; category contract covered. |
| [#320](https://github.com/alextitov19/memcap/issues/320) | polling-overhead | Historical/unverified; category contract covered. |
| [#317](https://github.com/alextitov19/memcap/issues/317) | lightweight-queued | Historical/unverified; category contract covered. |
| [#316](https://github.com/alextitov19/memcap/issues/316) | measurement | Historical/unverified; category contract covered. |
| [#315](https://github.com/alextitov19/memcap/issues/315) | lightweight-queued | Historical/unverified; category contract covered. |
| [#314](https://github.com/alextitov19/memcap/issues/314) | lightweight-queued | Historical/unverified; category contract covered. |
| [#313](https://github.com/alextitov19/memcap/issues/313) | missing-task-poll | Historical/unverified; category contract covered. |
| [#312](https://github.com/alextitov19/memcap/issues/312) | queue-stall | Historical/unverified; category contract covered. |
| [#311](https://github.com/alextitov19/memcap/issues/311) | polling-overhead | Historical/unverified; category contract covered. |
| [#310](https://github.com/alextitov19/memcap/issues/310) | lightweight-queued | Historical/unverified; category contract covered. |
| [#307](https://github.com/alextitov19/memcap/issues/307) | unexpected-termination | Historical/unverified; category contract covered. |
| [#306](https://github.com/alextitov19/memcap/issues/306) | lightweight-queued | Historical/unverified; category contract covered. |
| [#305](https://github.com/alextitov19/memcap/issues/305) | polling-overhead | Historical/unverified; category contract covered. |
| [#304](https://github.com/alextitov19/memcap/issues/304) | queue-stall | Historical/unverified; category contract covered. |
| [#303](https://github.com/alextitov19/memcap/issues/303) | queue-stall | Historical/unverified; category contract covered. |
| [#302](https://github.com/alextitov19/memcap/issues/302) | lightweight-queued | Historical/unverified; category contract covered. |
| [#301](https://github.com/alextitov19/memcap/issues/301) | queue-stall | Historical/unverified; category contract covered. |
| [#299](https://github.com/alextitov19/memcap/issues/299) | lightweight-queued | Historical/unverified; category contract covered. |
| [#298](https://github.com/alextitov19/memcap/issues/298) | polling-overhead | Historical/unverified; category contract covered. |
| [#297](https://github.com/alextitov19/memcap/issues/297) | lightweight-queued | Historical/unverified; category contract covered. |
| [#293](https://github.com/alextitov19/memcap/issues/293) | queue-stall | Historical/unverified; category contract covered. |
| [#292](https://github.com/alextitov19/memcap/issues/292) | lightweight-queued | Historical/unverified; category contract covered. |
| [#291](https://github.com/alextitov19/memcap/issues/291) | missing-task-poll | Historical/unverified; category contract covered. |
| [#289](https://github.com/alextitov19/memcap/issues/289) | integration | Historical/unverified; category contract covered. |
| [#288](https://github.com/alextitov19/memcap/issues/288) | unexpected-termination | Historical/unverified; category contract covered. |
| [#287](https://github.com/alextitov19/memcap/issues/287) | queue-lock | Historical/unverified; category contract covered. |
| [#286](https://github.com/alextitov19/memcap/issues/286) | queue-stall | Historical/unverified; category contract covered. |
| [#285](https://github.com/alextitov19/memcap/issues/285) | queue-lock | Historical/unverified; category contract covered. |
| [#284](https://github.com/alextitov19/memcap/issues/284) | lightweight-queued | Historical/unverified; category contract covered. |
| [#283](https://github.com/alextitov19/memcap/issues/283) | queue-stall | Historical/unverified; category contract covered. |
| [#282](https://github.com/alextitov19/memcap/issues/282) | integration | Historical/unverified; category contract covered. |
| [#281](https://github.com/alextitov19/memcap/issues/281) | polling-overhead | Historical/unverified; category contract covered. |
| [#280](https://github.com/alextitov19/memcap/issues/280) | unexpected-termination | Historical/unverified; category contract covered. |
| [#279](https://github.com/alextitov19/memcap/issues/279) | queue-stall | Historical/unverified; category contract covered. |
| [#278](https://github.com/alextitov19/memcap/issues/278) | queue-stall | Historical/unverified; category contract covered. |
| [#277](https://github.com/alextitov19/memcap/issues/277) | queue-stall | Historical/unverified; category contract covered. |
| [#276](https://github.com/alextitov19/memcap/issues/276) | queue-stall | Historical/unverified; category contract covered. |
| [#275](https://github.com/alextitov19/memcap/issues/275) | queue-stall | Historical/unverified; category contract covered. |
| [#274](https://github.com/alextitov19/memcap/issues/274) | integration | Historical/unverified; category contract covered. |
| [#273](https://github.com/alextitov19/memcap/issues/273) | polling-overhead | Historical/unverified; category contract covered. |
| [#272](https://github.com/alextitov19/memcap/issues/272) | lightweight-queued | Historical/unverified; category contract covered. |
| [#271](https://github.com/alextitov19/memcap/issues/271) | lightweight-queued | Historical/unverified; category contract covered. |
| [#270](https://github.com/alextitov19/memcap/issues/270) | lightweight-queued | Historical/unverified; category contract covered. |
| [#269](https://github.com/alextitov19/memcap/issues/269) | lightweight-queued | Historical/unverified; category contract covered. |
| [#267](https://github.com/alextitov19/memcap/issues/267) | polling-overhead | Historical/unverified; category contract covered. |
| [#262](https://github.com/alextitov19/memcap/issues/262) | integration | Historical/unverified; category contract covered. |
| [#259](https://github.com/alextitov19/memcap/issues/259) | queue-stall | Historical/unverified; category contract covered. |
