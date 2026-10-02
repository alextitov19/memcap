"""Admission patience and host command deadlines are distinct from status polls."""

WAIT_SECONDS = 86400
TOOL_TIMEOUT_MS = WAIT_SECONDS * 1000
GUIDANCE = (
    "For ordinary builds and tests, use automatic sizing: memcap run --wait 86400 -- COMMAND. "
    "Omit --memory unless a measured fixed reservation is intentional. A fixed --memory request remains a floor even when observed usage is smaller; "
    "never invent a large reservation from caution alone or lower a needed reservation to force admission. "
    "Memcap shares physical memory among all Claude and Codex sessions. Reserve an entire disposable test environment before bringing it up: "
    "memcap environment run --memory TOTAL_GIB --compose compose.yaml -- TEST_COMMAND. "
    "TOTAL_GIB includes Docker services plus the test/build process. Prepare images separately. "
    "This waits before starting the stack, runs the workload within the same admission, then stops its uniquely owned containers without deleting volumes. "
    "Do not start a large stack and then submit a separate queued test that depends on it: the stack can block its own test and other sessions. "
    "Reuse shared resources with explicit ownership; do not stop another session's stack. Pin or claim a disposable environment before sharing it. "
    "Large requests stay pending while verified finished-session resources are cleaned up. If the environment plus workload cannot fit, reduce the actual environment or report that capacity requirement; do not understate its reservation. "
    "Prefer native task completion notifications. If the host can resume on background completion, one memcap wait JOB_ID --until-complete may replace repeated status calls; it is observation only, not the workload's result. "
    "Hosts and Stop hooks that cannot resume must retain their bounded 60-second blocking fallback. "
    "Allow needed managed jobs up to 24 hours: use memcap run --wait 86400 when "
    "specifying admission patience. Managed Claude commands use background mode "
    "and timeout=86400000; selected profiles need BASH_MAX_TIMEOUT_MS at least "
    "86400000 (Claude Code 2.1.285+). The outer deadline includes both waiting "
    "and execution. Keep status polls at 60 seconds; Codex yield_time_ms is a "
    "response yield, not a job deadline. Do not abandon needed work after a "
    "ten-minute queue wait. Preserve explicit shorter runner deadlines and user "
    "cancellation. Keep the owning session/subagent alive until required work "
    "finishes. Existing host tasks retain original deadlines; expired tasks "
    "cannot resume. Confirm final task status before submitting a replacement. "
    "Reload agent sessions after integration changes to adopt the timeout ceiling."
)


def managed_input(original, agent):
    updated = dict(original)
    if agent == "claude":
        updated["run_in_background"] = True
        updated["timeout"] = TOOL_TIMEOUT_MS
    elif "timeout" in updated:
        # Do not invent fields for modern Codex tools or change response yields.
        updated["timeout"] = TOOL_TIMEOUT_MS
    return updated
