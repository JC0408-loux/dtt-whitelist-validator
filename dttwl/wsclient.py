"""Minimal RFC 6455 WebSocket client built on the standard library only.

The validator has to run from a single PyInstaller executable on a factory
machine with no network access, so pulling in a third-party WebSocket library
is not an option.  Only the subset of the protocol the DTT web server actually
uses is implemented: an unencrypted client connection, text frames, and the
control frames needed to stay well behaved.
"""

import base64
import hashlib
import os
import socket
import struct
import time

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WebSocketError(Exception):
    pass


class WebSocketTimeout(WebSocketError):
    pass


class WebSocketClient:
    def __init__(self, host, port, path="/", connect_timeout=5.0, origin=None,
                 extra_headers=None):
        """`origin` of None omits the header; `extra_headers` adds or overrides.

        Servers differ in what they require of a handshake, so the exact header
        set is left to the caller rather than fixed here.
        """
        self.host = host
        self.port = port
        self.path = path
        self.connect_timeout = connect_timeout
        self.origin = origin
        self.extra_headers = dict(extra_headers or {})
        self._sock = None
        self._buf = b""
        self.handshake_request = ""
        self.handshake_response = ""

    def build_handshake(self, key):
        headers = [
            ("Host", "{0}:{1}".format(self.host, self.port)),
            ("Upgrade", "websocket"),
            ("Connection", "Upgrade"),
            ("Sec-WebSocket-Key", key),
            ("Sec-WebSocket-Version", "13"),
        ]
        if self.origin:
            headers.append(("Origin", self.origin))

        overrides = {name.lower(): value for name, value in self.extra_headers.items()}
        merged = []
        for name, value in headers:
            merged.append((name, overrides.pop(name.lower(), value)))
        for name, value in self.extra_headers.items():
            if name.lower() in overrides:
                merged.append((name, value))

        lines = ["GET {0} HTTP/1.1".format(self.path)]
        lines += ["{0}: {1}".format(name, value) for name, value in merged]
        return "\r\n".join(lines) + "\r\n\r\n"

    # -- lifecycle ---------------------------------------------------------

    def connect(self):
        self._sock = socket.create_connection((self.host, self.port), self.connect_timeout)
        self._sock.settimeout(self.connect_timeout)
        try:
            self._handshake()
        except BaseException:
            # Callers try several handshakes in a row; without this each
            # rejected attempt would leak its socket.
            try:
                self._sock.close()
            finally:
                self._sock = None
            raise

    def _handshake(self):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = self.build_handshake(key)
        self.handshake_request = request
        self._sock.sendall(request.encode("ascii"))

        header = self._read_until(b"\r\n\r\n", self.connect_timeout)
        self.handshake_response = header.decode("latin-1", "replace").strip()
        status_line = header.split(b"\r\n", 1)[0].decode("latin-1")
        if "101" not in status_line:
            raise WebSocketError(
                "handshake rejected: {0}\n{1}".format(status_line,
                                                      self.handshake_response)
            )

        expected = base64.b64encode(
            hashlib.sha1((key + GUID).encode("ascii")).digest()
        ).decode("ascii")
        accept = ""
        for line in header.decode("latin-1").split("\r\n")[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "sec-websocket-accept":
                accept = value.strip()
        if accept != expected:
            raise WebSocketError("bad Sec-WebSocket-Accept in handshake response")

    def close(self):
        if self._sock is None:
            return
        try:
            self._send_frame(OP_CLOSE, b"")
        except OSError:
            pass
        try:
            self._sock.close()
        finally:
            self._sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_exc):
        self.close()

    # -- public I/O --------------------------------------------------------

    def send_text(self, text):
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def recv_text(self, timeout):
        """Return the next text message, answering control frames on the way."""
        deadline = time.monotonic() + timeout
        pieces = []
        pending_opcode = None

        while True:
            opcode, payload, fin = self._read_frame(deadline)

            if opcode == OP_PING:
                self._send_frame(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                raise WebSocketError("connection closed by server")

            if opcode in (OP_TEXT, OP_BINARY):
                pending_opcode = opcode
                pieces = [payload]
            elif opcode == OP_CONT:
                pieces.append(payload)
            else:
                raise WebSocketError("unsupported opcode 0x%x" % opcode)

            if fin:
                data = b"".join(pieces)
                if pending_opcode == OP_BINARY:
                    return data.decode("utf-8", "replace")
                return data.decode("utf-8", "replace")

    # -- framing -----------------------------------------------------------

    def _send_frame(self, opcode, payload):
        if self._sock is None:
            raise WebSocketError("not connected")
        header = bytearray()
        header.append(0x80 | opcode)
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def _read_frame(self, deadline):
        first = self._read_exactly(2, deadline)
        fin = bool(first[0] & 0x80)
        opcode = first[0] & 0x0F
        masked = bool(first[1] & 0x80)
        length = first[1] & 0x7F

        if length == 126:
            length = struct.unpack(">H", self._read_exactly(2, deadline))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exactly(8, deadline))[0]

        mask = self._read_exactly(4, deadline) if masked else None
        payload = self._read_exactly(length, deadline) if length else b""
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload, fin

    # -- socket helpers ----------------------------------------------------

    def _read_exactly(self, count, deadline):
        while len(self._buf) < count:
            self._fill(deadline)
        data, self._buf = self._buf[:count], self._buf[count:]
        return data

    def _read_until(self, marker, timeout):
        deadline = time.monotonic() + timeout
        while marker not in self._buf:
            self._fill(deadline)
        index = self._buf.index(marker) + len(marker)
        data, self._buf = self._buf[:index], self._buf[index:]
        return data

    def _fill(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WebSocketTimeout("timed out waiting for data")
        self._sock.settimeout(remaining)
        try:
            chunk = self._sock.recv(65536)
        except socket.timeout:
            raise WebSocketTimeout("timed out waiting for data")
        if not chunk:
            raise WebSocketError("connection closed by server")
        self._buf += chunk
