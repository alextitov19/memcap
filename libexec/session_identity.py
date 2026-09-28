"""Use the same private scope for hook admission and completion observation."""
import hashlib
import json


def identity(payload):
    session = payload.get("session_id", "")
    if not isinstance(session, str):
        return ""
    agent = payload.get("agent_id")
    return (session, agent) if isinstance(agent, str) and agent else session


def identity_key(value):
    if isinstance(value, tuple):
        parent = hashlib.sha256(value[0].encode()).hexdigest()
        child = hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()
        return parent + "/" + child
    return hashlib.sha256(value.encode()).hexdigest() if value else ""


def key(payload):
    return identity_key(identity(payload))
