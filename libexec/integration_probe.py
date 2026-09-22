"""Bounded read-only JSON-RPC over an existing Codex daemon's Unix WebSocket.

Never start/stop an agent or alter trust. Unavailable runtime means unverified.
No third-party packages; only text messages and RFC 6455 control frames are used.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import struct
import time

LIMIT = 4 * 1024 * 1024


class Connection:
    def __init__(self, client, deadline):
        self.client = client
        self.deadline = deadline

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError(
                "Codex hook probe timed out; trust is unverified. Check /hooks."
            )
        self.client.settimeout(remaining)

    def read(self, length):
        data = b""
        while len(data) < length:
            self.remaining()
            part = self.client.recv(length - len(data))
            if not part:
                raise ValueError(
                    "Codex closed the connection; hook trust is unverified"
                )
            data += part
        return data

    def upgrade(self):
        key = base64.b64encode(os.urandom(16)).decode()
        self.remaining()
        self.client.sendall(
            (
                "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\nSec-WebSocket-Key: "
                + key
                + "\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            if len(header) >= 16384:
                raise ValueError("Codex WebSocket header too large")
            header += self.read(1)
        lines = header.decode("ascii").split("\r\n")
        fields = dict(line.lower().split(":", 1) for line in lines[1:] if ":" in line)
        # Header names are case insensitive; accept values are case sensitive.
        accept = next(
            (
                line.split(":", 1)[1].strip()
                for line in lines
                if line.lower().startswith("sec-websocket-accept:")
            ),
            "",
        )
        expected = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()
        ).decode()
        if (
            not lines[0].startswith("HTTP/1.1 101 ")
            or accept != expected
            or fields.get("upgrade", "").strip() != "websocket"
        ):
            raise ValueError(
                "Codex WebSocket handshake unavailable; trust is unverified"
            )

    def frame(self, opcode, payload):
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            prefix = bytes([0x80 | opcode, 0x80 | length])
        elif length <= 65535:
            prefix = bytes([0x80 | opcode, 0xFE]) + struct.pack("!H", length)
        else:
            prefix = bytes([0x80 | opcode, 0xFF]) + struct.pack("!Q", length)
        self.remaining()
        self.client.sendall(
            prefix
            + mask
            + bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
        )

    def send(self, body):
        self.frame(1, json.dumps(body).encode())

    def receive(self):
        message = b""
        fragmented = False
        while True:
            first, second = self.read(2)
            opcode, final = first & 15, bool(first & 128)
            if first & 112 or second & 128:
                raise ValueError("Unexpected Codex WebSocket flags")
            length = second & 127
            if length == 126:
                length = struct.unpack("!H", self.read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.read(8))[0]
            if length > LIMIT or len(message) + length > LIMIT:
                raise ValueError("Codex hook response too large; trust is unverified")
            if opcode >= 8 and (not final or length > 125):
                raise ValueError("Invalid Codex WebSocket control frame")
            payload = self.read(length)
            if opcode == 8:
                raise ValueError("Codex closed the connection; trust is unverified")
            if opcode == 9:
                self.frame(10, payload)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1) or (opcode == 0) != fragmented:
                raise ValueError("Invalid Codex WebSocket text sequence")
            message += payload
            if final:
                value = json.loads(message)
                if not isinstance(value, dict):
                    raise ValueError("Invalid Codex hook response")
                return value
            fragmented = True


def codex_hooks(directory):
    path = Path(directory) / "app-server-control/app-server-control.sock"
    try:
        info = path.stat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("Codex control socket is not owned by this user")
    except FileNotFoundError as error:
        raise ValueError(
            "Codex daemon unavailable; hook trust is unverified. Review memcap hooks in /hooks."
        ) from error
    deadline = time.monotonic() + 3
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        try:
            client.connect(str(path))
        except OSError as error:
            raise ValueError(
                "Codex daemon unavailable; hook trust is unverified. Review memcap hooks in /hooks."
            ) from error
        connection = Connection(client, deadline)
        connection.upgrade()

        def rpc(ident, method, params):
            connection.send({"id": ident, "method": method, "params": params})
            while True:
                message = connection.receive()
                if message.get("id") == ident:
                    if "error" in message:
                        raise ValueError(
                            "Codex hook API unavailable; update Codex and check /hooks"
                        )
                    return message.get("result")

        rpc(
            1,
            "initialize",
            {
                "clientInfo": {"name": "memcap-doctor", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        connection.send({"method": "initialized"})
        result = rpc(2, "hooks/list", {"cwds": [str(Path.cwd())]})

    def walk(value):
        if isinstance(value, dict):
            if "key" in value and "currentHash" in value:
                yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    return list(walk(result))
