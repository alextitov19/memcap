# Lightweight classification, v0.17.0

The September 25 reports showed that isolated command exceptions were leaving
ordinary work in admission. The owner requested substantially less restrictive
classification. This release expands command families and composes proven
lightweight producers with runtime validation of their consumers.

## Measured baseline

The committed corpus in `tests/test_lightweight_families.py` covers 25 direct
combinations and nine guarded combinations. Comparing v0.16.7's source commit
`e1470dc` with the initial broader classifier (`ca3b74e`) on the same Mac,
without executing any remote command:

| Corpus result | v0.16.7 | v0.17.0 |
| --- | ---: | ---: |
| Commands requiring workload admission | 34/34 | 0/34 |
| Native or argument-guarded commands | 0/34 | 34/34 |
| Median classification time, ten whole-corpus runs | 4.81 ms | 2.85 ms |

This corpus deliberately represents missing support; it is not a percentage of
all user commands or a measured end-to-end latency claim. The timing predates the
additional script-helper recognition; the final regression still verifies all
34 shapes avoid admission. The practical change
is removal of workload admission for these shapes. Real Bash/zsh tests verify
single producer execution, matching output/status, and no queue directory for
supported inspection. Execution-producing arguments reach a recording fallback.

## Issue coverage

- #138: direct and nested lightweight `$()` producers; actual consumer argv is
  checked after the shell expands it. No arbitrary producer execution exemption.
- #139: supported Benmore remote inspection/control families. Local build,
  deploy and push helpers remain managed; follow modes are excluded.
- #140: small Python HTML-to-text reads. The actual reported command passes
  syntax and file-size checks. Runtime validation happens in its execution cwd;
  isolated imports prevent cwd modules from substituting for the standard library.
- #142: finite Bash/sh helpers containing supported lightweight commands, local
  data assignments, streaming transforms and remote SSM calls. The helper is read
  and checked again at execution time, then the validated text is executed with
  its original positional arguments. Each external command's expanded arguments
  go through the same runtime policy. Loops, dynamic command names, startup
  settings, unknown commands and unavailable/oversized script files stay managed.
- Earlier #132: recursive filesystem cleanup followed by status now qualifies.
  Its prior not-planned disposition is superseded by the owner's broader policy.

Local aliases now use ordinary variable names, while startup/lookup variables
and exported settings remain excluded. Simple filesystem operations are a memory
classification decision, not permission to delete files or change other projects.
No host memory configuration, pressure policy, Docker setting or kill path changes.

## Validation record

The initial new suite failed 35 assertions against v0.16.7. A later targeted run
caught an assignment such as `BASH_ENV=/tmp/file` being mistaken for the `file`
utility after basename extraction. Unapproved leading assignments are now
rejected before command-family classification; the regression remains.

The first baseline measurement attempt failed because the local checkout did
not yet have the release tag. The successful comparison uses the immutable
release commit above. Neither failed attempt is reported as a successful check.

Full release validation and CI results are recorded in the release notes. File
size and text-growth estimates are admission decisions, not kernel hard limits;
an input can still change after its metadata is read. Unknown Python syntax or
large/unavailable inputs retain normal admission.

The script-file regression additionally covers output/exit status under Bash and
zsh, a script changed between classification and execution, FIFO/oversized paths,
expanded execution options, and shell startup variables. This is a bounded syntax
proof, not a promise that every custom shell program is lightweight.

The first full run after adding helper guidance had one failure: repeated hook
context grew to 1,565 characters against its existing 1,500-character limit.
Guidance was shortened; the limit was preserved. The helper entry-path negative
control fails when recognition is disabled, and the actual reported helper passes
content validation without executing its remote commands.
