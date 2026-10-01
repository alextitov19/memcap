"""Admission patience and host command deadlines are distinct from status polls."""

WAIT_SECONDS = 86400
TOOL_TIMEOUT_MS = WAIT_SECONDS * 1000
GUIDANCE = (
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
