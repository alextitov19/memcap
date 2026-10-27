# Observation and timing follow-up

Scope: investigate the first v0.25 jobs, fix demonstrated defects, validate and
publish the next release, preserve local before/after evidence. Keep live
admission, deadlines, ownership and signal policy unchanged.

1. Preserve the v0.25 baseline and trace the three incomplete jobs. Distinguish
   Docker/external compiler inputs (unsupported scope) from the plain Go build's
   inspection limit. Raw commands and project paths stay in private evidence.
2. Reproduce loss of an already identity-verified high reading when a process
   exits before the paired read. Preserve that upward evidence without granting
   completeness, lowering a request or authorizing a signal. Unpaired readings
   without a prior matching kernel identity remain unattributed.
3. Reproduce compiler inspection wasting its time/byte budget on source contents
   that are never used in the reusable key. Inspect a bounded metadata envelope
   for source files; retain content hashing/limits for configuration, source
   growth gates, symlink/regular-file checks, time/file-count limits and cold priors.
4. Resolve #334: local event offset gaps line up with macOS sleep records,
   including Thermal Emergency Sleep. Add monotonic elapsed timing that includes
   sleep beside awake timing. Preserve existing timeout clocks; distinguish old
   wall/monotonic discrepancies without asserting historical sleep from clocks
   alone. Timestamp durations before emission. Never use timing telemetry for
   admission, reservation or process ownership.
5. Run the new regressions against the old implementation, then targeted checks,
   controlled before/after probes and deliberate regression controls. Complete
   implementation before one final whole-diff review.
6. Run all repository gates: grouped and individual ShellCheck, individual Bash
   parses, normal Bats and isolated-HOME/no-Docker Bats. All heavy work goes through
   live memcap; fixtures use temporary state/HOME and dry-run enforcement.
7. Publish through protected-main CI, update Homebrew, install with old Cellar
   versions retained, verify config identity/integrations/recorder and save
   installed-package evidence. No claimed production learning gain from a small
   uncontrolled cohort or from merely retaining an incomplete high observation.

Evidence: private release-history/2026-10-04-morning and before-v0.26.0 snapshots;
new diagnostic artifacts under release-history/v0.26.0-validation.

Initial findings: all three observed jobs had process churn; no evidence yet
justifies converting those incomplete samples to complete. One workload includes
external Docker allocations and another specifies external TypeScript config.
The timestamp offset shifted by about 5,014 seconds across #334's first window;
the largest individual gap matches a 1,013-second sleep in macOS power logs.
