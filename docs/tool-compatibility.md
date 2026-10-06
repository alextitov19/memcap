# Installed tool compatibility

Docker Desktop and OrbStack can coexist during migration. Their matched host
process footprints count together; the selected Docker context does not hide the
other engine. Global pressure/headroom admission still applies. memcap never
switches contexts, migrates data or restarts an engine automatically.

Runtime detection reads bounded local Docker metadata, honoring DOCKER_CONTEXT,
then DOCKER_HOST, then currentContext. Known local OrbStack and Desktop sockets
identify the runtime. Remote/custom/malformed contexts do not fall back to Desktop
for settings changes. Desktop's old ceiling is not shown as OrbStack's limit.
This reads configuration, not a live guarantee that an engine is running. Old
disposable environments retain their recorded endpoint; switching engines cannot
authorize stopping unrelated containers on the new engine.

The classifier inspects the underlying command for `mise exec`/`mise x` with `--`,
literal mise tasks in mise.toml/.mise.toml, ordinary justfile recipes/dependencies,
Hyperfine commands and setup/prepare/cleanup commands, Docker global context/host
options, and supported `orb` local-machine commands. It does not execute these
tools to determine demand. Edited task files participate in fingerprints.

Additional positive workload evidence includes gotestsum tests, staticcheck,
deadcode, Air compilation, Trivy scans, k6 local runs, Whisper model runs, ffmpeg
encoding, Fastlane lanes, CocoaPods installs/updates, sqlc generation, VHS renders,
and local EAS builds. Help/version and supported inspection operations remain
native. `gotestsum --raw-command -- ...` follows its actual child command.
An unfamiliar filename or syntax alone still does not justify queueing.

These are memory-demand heuristics, not proof of peak memory, permission grants,
or universal coverage of plugin/task languages. Dynamic imports, task templates,
unrecognized options and custom plugin behavior can remain unknown/native;
observed high usage can promote exact workloads. TOML task inspection requires
Python 3.11+ (the owner's mise Python 3.12 and Homebrew Python satisfy this);
older interpreters retain unknown/native fallback. Do not label all commands of
a task manager heavy merely because one recipe builds something.

Mise activation changes new shell PATH values. Existing agents retain their
inherited environment; memcap does not rewrite shell or agent profiles. Direct
Go/Node/Python commands retain their existing classification regardless of the
version manager's installation path.

Homebrew's database `goose` formula is distinct from the Goose agent (`goose-cli`).
The classifier excludes positively identified DB installations from agent roots,
while preserving their protection when launched under a real agent. Unknown
Goose installations retain protection. The binary is never executed for detection.

Validation uses fake process tables, temporary Docker metadata and command-text
classification; it does not start workloads, restart Docker, boot devices or
interrupt a migration. A passing compatibility suite is not a live container
migration benchmark.
