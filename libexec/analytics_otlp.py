"""Bounded loopback OTLP/HTTP JSON logs adapter. Raw bodies are never persisted.

POST /v1/logs: Authorization: Bearer <local token>, application/json, <=1 MiB.
200 acknowledges sanitized events accepted into the bounded queue, 400 malformed,
401 missing authentication, 413 oversized, 429 receiver full. No other endpoints.
"""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import queue
import threading
import time

from analytics_events import numeric


def attributes(items):
    if not isinstance(items, list):
        return {}
    result = {}
    for item in items[:256]:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            continue
        value = item.get("value", {})
        if not isinstance(value, dict):
            continue
        for kind in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if kind in value and isinstance(value[kind], (str, int, float, bool)):
                result[item["key"]] = value[kind]
                break
    return result


def decode_logs(body):
    """Only documented semantic numeric fields and opaque correlation inputs."""
    if not isinstance(body, dict) or not isinstance(body.get("resourceLogs", []), list):
        raise ValueError("invalid OTLP envelope")
    output = []
    for resource in body.get("resourceLogs", [])[:64]:
        common = attributes(resource.get("resource", {}).get("attributes", []))
        for scope in resource.get("scopeLogs", [])[:64]:
            for record in scope.get("logRecords", [])[:2048]:
                attrs = {**common, **attributes(record.get("attributes", []))}
                event_name = attrs.get("event.name") or record.get("eventName") or record.get("body", {}).get("stringValue")
                if isinstance(event_name, str) and not event_name.startswith("claude_code."):
                    event_name = "claude_code." + event_name
                mapping = {"claude_code.api_request": "api", "claude_code.api_error": "api",
                           "claude_code.tool_result": "native_tool", "claude_code.user_prompt": "hook",
                           "claude_code.api_retries_exhausted": "hook",
                           "claude_code.hook_execution_complete": "native_hooks"}
                event = mapping.get(event_name)
                if not event:
                    continue
                fields = {"source": "claude", "agent": "claude"}
                stamp = record.get("timeUnixNano")
                if isinstance(stamp, (str, int)) and str(stamp).isdigit():
                    fields["source_wall"] = int(stamp) / 1e9
                    fields["delivery"] = json.dumps([event_name, attrs.get("session.id"), attrs.get("event.sequence"),
                                                      attrs.get("process.pid"), stamp, attrs.get("request_id"), attrs.get("tool_use_id")], separators=(",", ":"))
                session = attrs.get("session.id")
                if isinstance(session, str):
                    # Match session_identity.key, then the local keyed hash.
                    fields["session"] = hashlib.sha256(session.encode()).hexdigest()
                for src, dst in (("prompt.id", "turn"), ("tool_use_id", "operation"),
                                 ("model", "model"), ("service.version", "agent_version"),
                                 ("tool_name", "tool")):
                    if src in attrs:
                        fields[dst] = attrs[src]
                query_source = attrs.get("query_source")
                fields["query_source"] = "main" if query_source == "repl_main_thread" else "auxiliary" if query_source in {"compact", "summarize", "title"} else "subagent" if query_source == "subagent" else "unknown"
                for src, dst in (("duration_ms", "duration_ms"), ("input_tokens", "input_tokens"),
                                 ("output_tokens", "output_tokens"), ("cache_read_tokens", "cache_read_tokens"),
                                 ("cache_creation_tokens", "cache_creation_tokens"), ("cost_usd", "api_cost_usd")):
                    try:
                        value = float(attrs[src])
                        if numeric(value):
                            fields[dst] = value
                    except (KeyError, TypeError, ValueError):
                        pass
                if "api_cost_usd" not in fields:
                    try:
                        value = float(attrs["cost_usd_micros"]) / 1e6
                        if numeric(value):
                            fields["api_cost_usd"] = value
                    except (KeyError, ValueError, TypeError):
                        pass
                if event == "native_hooks":
                    try:
                        value = float(attrs["total_duration_ms"])
                        if numeric(value):
                            fields["agent_hooks_ms"] = value
                    except (KeyError, ValueError, TypeError):
                        pass
                if event_name == "claude_code.user_prompt":
                    fields["phase"] = "UserPromptSubmit"
                elif event_name == "claude_code.api_retries_exhausted":
                    fields.update(phase="StopFailure", outcome="failed")
                elif event_name == "claude_code.api_error":
                    fields["success"] = 0
                elif event == "api":
                    fields["success"] = 1
                if event == "native_tool" and attrs.get("success") in (True, False, "true", "false"):
                    fields["success"] = int(attrs["success"] in (True, "true"))
                # Receiver timestamps measure arrival; source timestamp is display
                # context only. Never pretend delayed exports are monotonic spans.
                output.append((event, fields))
                if len(output) >= 2048:
                    return output
    return output


def server(port, token, inbox):
    class LoopbackServer(HTTPServer):
        def server_bind(self):
            # HTTPServer resolves its name through getfqdn(), which can block
            # collector startup on DNS. This endpoint is numeric loopback only.
            from socketserver import TCPServer
            TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address[:2]

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != "/v1/logs":
                self.send_error(404)
                return
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.send_error(401)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 1024 * 1024 or self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding"):
                    self.send_error(413)
                    return
                if "application/json" not in self.headers.get("Content-Type", ""):
                    self.send_error(415)
                    return
                events = decode_logs(json.loads(self.rfile.read(size)))
                # One batch per request, bounded both by queue and body limit.
                inbox.put_nowait(events)
            except queue.Full:
                self.send_error(429)
                return
            except (ValueError, TypeError, KeyError, AttributeError, OSError, RecursionError):
                self.send_error(400)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    http = LoopbackServer(("127.0.0.1", port), Handler)
    http.timeout = .5
    thread = threading.Thread(target=http.serve_forever, kwargs={"poll_interval": .5}, daemon=True)
    thread.start()
    return http, thread
