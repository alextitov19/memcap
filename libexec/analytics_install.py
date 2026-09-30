"""Explicit, reversible local analytics installation; no enforcement changes."""
import json
import os
from pathlib import Path
import plistlib
import secrets
import stat
import subprocess
import sys

from analytics_store import private_directory
from integrate import Edit, IntegrationError, atomic_write, commit, json_object

LABEL = "com.memcap.analytics"


def initialize(directory):
    private_directory(directory)
    for name, value in (("key", secrets.token_bytes(32)), ("token", secrets.token_hex(32).encode())):
        path = directory / name
        if path.is_symlink():
            raise ValueError("unsafe analytics secret path")
        if not path.exists():
            atomic_write(path, value, 0o600)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("analytics secret must be a regular owned file")
        os.chmod(path, 0o600)
    if len((directory / "key").read_bytes()) != 32 or len((directory / "token").read_text().strip()) != 64:
        raise ValueError("invalid existing analytics secrets; existing history preserved")
    atomic_write(directory / "enabled", b"1\n", 0o600)


def telemetry_environment(token, port):
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": f"http://127.0.0.1:{port}/v1/logs",
        "OTEL_EXPORTER_OTLP_LOGS_HEADERS": "Authorization=Bearer " + token,
        "OTEL_LOG_USER_PROMPTS": "0", "OTEL_LOG_ASSISTANT_RESPONSES": "0",
        "OTEL_LOG_TOOL_DETAILS": "0", "OTEL_LOG_TOOL_CONTENT": "0",
        "OTEL_LOG_RAW_API_BODIES": "0", "OTEL_LOG_MANAGED_SETTINGS": "0",
        "OTEL_METRICS_INCLUDE_ACCOUNT_UUID": "false",
    }


def configure_profiles(directories, token, port):
    expected = telemetry_environment(token, port)
    edits, results = [], []
    for directory in directories:
        edit = Edit.read(Path(directory) / "settings.json")
        data = json_object(edit.before.decode(), edit.path) if edit.before else {}
        env = data.get("env", {})
        if not isinstance(env, dict):
            raise IntegrationError("Claude env must be an object; no profiles written")
        # A pre-existing exporter, beta destination or dynamic auth helper belongs
        # to the owner. Do not silently reroute it or attach our local credential.
        effective_env = {**os.environ, **env}
        conflict = data.get("otelHeadersHelper") or any(
            (k.startswith("OTEL_EXPORTER_") or k in {"OTEL_LOGS_EXPORTER", "BETA_TRACING_ENDPOINT", "CLAUDE_CODE_ENABLE_TELEMETRY"})
            and (k not in expected or str(v) != expected[k]) for k, v in effective_env.items())
        conflict = conflict or any(k.startswith("OTEL_LOG_") and str(v).lower() not in {"0", "false", ""} for k, v in effective_env.items())
        if conflict:
            results.append({"profile": str(directory), "native_telemetry": "existing_exporter_preserved", "hooks": "unchanged"})
            continue
        merged = {**data, "env": {**env, **expected}}
        if merged != data:
            edit.after = (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode()
        edits.append(edit)
        results.append({"profile": str(directory), "native_telemetry": "configured_for_next_start", "hooks": "unchanged"})
    commit(edits)
    return results


def install_service(directory, executable, port):
    target = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    edit = Edit.read(target)
    spec = dict(Label=LABEL, ProgramArguments=[str(executable), "analytics", "_collect", "--port", str(port)],
                RunAtLoad=True, KeepAlive={"SuccessfulExit": False}, ThrottleInterval=30,
                ProcessType="Background", LowPriorityIO=True, Nice=10,
                EnvironmentVariables={"PATH": str(Path(sys.executable).parent) + ":/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"})
    # Isolated tests and explicit alternate state remain isolated across launchd.
    if "MEMCAP_STATE_HOME" in os.environ:
        spec["EnvironmentVariables"]["MEMCAP_STATE_HOME"] = os.environ["MEMCAP_STATE_HOME"]
    if "MEMCAP_CONFIG_HOME" in os.environ:
        spec["EnvironmentVariables"]["MEMCAP_CONFIG_HOME"] = os.environ["MEMCAP_CONFIG_HOME"]
    edit.after = plistlib.dumps(spec)
    changed = commit([edit])
    domain = f"gui/{os.getuid()}"
    exists = subprocess.run(["launchctl", "print", domain + "/" + LABEL], capture_output=True, timeout=10).returncode == 0
    if exists and changed:
        subprocess.run(["launchctl", "bootout", domain + "/" + LABEL], check=True, capture_output=True, timeout=10)
        exists = False
    if not exists:
        subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True, capture_output=True, timeout=10)
    else:
        subprocess.run(["launchctl", "kickstart", domain + "/" + LABEL], check=True, capture_output=True, timeout=10)
    return str(target)
