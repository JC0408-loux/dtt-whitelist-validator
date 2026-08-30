"""ESIF request layer for the DTT web server.

The DTT page at http://localhost:8888/index.html is only a shell: it opens a
WebSocket to ws://localhost:8888/echo and drives everything through a handful
of ESIF commands.  Messages are framed as "<message id>:<command>" going out
and "<message id>:<payload>" coming back, with unsolicited "update:" and
"status:" pushes mixed in.  Talking to that socket directly means the
validator never has to open a browser, so it cannot disturb the foreground
window it is trying to measure.
"""

import xml.etree.ElementTree as ET

from .wsclient import WebSocketClient, WebSocketError, WebSocketTimeout

CMD_GET_GROUPS = "dptf ui getgroups"
CMD_GET_MODULES = "dptf ui getmodulesingroup {group}"
CMD_GET_MODULE_DATA = "dptf ui getmoduledata {group} {module}"

DEFAULT_POLICY_MODULE = "Adaptive Performance Policy"

# Which headers a WebSocket handshake needs varies between DTT versions, and a
# server that dislikes one can simply go quiet rather than answer, so the
# variants are tried in turn instead of assuming one is right. The first that
# works is remembered for the rest of the run.
HANDSHAKE_VARIANTS = [
    {
        "name": "standard",
        "origin": "http://{host}:{port}",
        "headers": {},
    },
    {
        "name": "browser-like headers",
        "origin": "http://{host}:{port}",
        "headers": {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0"),
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-WebSocket-Extensions": "permessage-deflate; client_max_window_bits",
        },
    },
    {
        "name": "Connection: keep-alive, Upgrade",
        "origin": "http://{host}:{port}",
        "headers": {"Connection": "keep-alive, Upgrade"},
    },
    {
        "name": "no Origin header",
        "origin": None,
        "headers": {},
    },
    {
        "name": "Origin without port",
        "origin": "http://{host}",
        "headers": {},
    },
    {
        "name": "with Sec-WebSocket-Protocol",
        "origin": "http://{host}:{port}",
        "headers": {"Sec-WebSocket-Protocol": "esif"},
    },
]

# Remembered across sessions so only the first connection pays for probing.
_preferred_variant = None


def variant_headers(variant, host, port):
    origin = variant["origin"]
    if origin:
        origin = origin.format(host=host, port=port)
    return origin, dict(variant["headers"])


class EsifError(Exception):
    pass


class EsifSession:
    def __init__(self, host="localhost", port=8888, path="/echo", timeout=10.0,
                 variant=None):
        self.host = host
        self.port = port
        self.path = path
        self.timeout = timeout
        self.variant = variant
        self.variant_used = None
        self._ws = None
        self._next_id = 1

    def _open(self, variant, timeout):
        origin, headers = variant_headers(variant, self.host, self.port)
        client = WebSocketClient(self.host, self.port, self.path, timeout,
                                 origin=origin, extra_headers=headers)
        client.connect()
        return client

    # -- lifecycle ---------------------------------------------------------

    def connect(self):
        global _preferred_variant

        order = list(HANDSHAKE_VARIANTS)
        if self.variant is not None:
            order = [self.variant]
        elif _preferred_variant is not None:
            order = [_preferred_variant] + [v for v in order if v is not _preferred_variant]

        # A server that ignores a handshake it dislikes costs the full timeout,
        # so each attempt gets a short one and only the last is given longer.
        attempt_timeout = min(self.timeout, 4.0)
        failures = []

        for index, variant in enumerate(order):
            timeout = self.timeout if index == len(order) - 1 else attempt_timeout
            try:
                self._ws = self._open(variant, timeout)
            except (OSError, WebSocketError) as exc:
                failures.append((variant["name"], str(exc).splitlines()[0]))
                continue
            self.variant_used = variant
            if self.variant is None:
                _preferred_variant = variant
            return self

        self._ws = None
        detail = "; ".join("{0}: {1}".format(name, error) for name, error in failures)
        raise EsifError(
            "cannot open a WebSocket to ws://{0}:{1}{2}. Tried {3} handshake "
            "variant(s) -- {4}".format(
                self.host, self.port, self.path, len(failures), detail)
        )

    def close(self):
        if self._ws is not None:
            self._ws.close()
            self._ws = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *_exc):
        self.close()

    # -- requests ----------------------------------------------------------

    def request(self, command, timeout=None):
        if self._ws is None:
            raise EsifError("not connected")
        timeout = self.timeout if timeout is None else timeout

        message_id = self._next_id
        self._next_id += 1
        prefix = "{0}:".format(message_id)

        try:
            self._ws.send_text(prefix + command)
        except (OSError, WebSocketError) as exc:
            raise EsifError("failed to send '{0}': {1}".format(command, exc))

        # Skip "update:" / "status:" pushes and replies to earlier requests.
        while True:
            try:
                message = self._ws.recv_text(timeout)
            except WebSocketTimeout:
                raise EsifError("timed out waiting for a reply to '{0}'".format(command))
            except WebSocketError as exc:
                raise EsifError("connection lost during '{0}': {1}".format(command, exc))
            if message.startswith(prefix):
                return message[len(prefix):]

    # -- higher level ------------------------------------------------------

    def get_groups(self):
        return _parse_id_name(self.request(CMD_GET_GROUPS), "group")

    def get_modules(self, group_id):
        xml = self.request(CMD_GET_MODULES.format(group=group_id))
        return _parse_id_name(xml, "module")

    def find_module(self, module_name=DEFAULT_POLICY_MODULE):
        """Locate a module by name, returning (group_id, module_id)."""
        wanted = module_name.strip().lower()
        for group_id, _group_name in self.get_groups():
            for module_id, name in self.get_modules(group_id):
                if name.strip().lower() == wanted:
                    return group_id, module_id
        raise EsifError(
            "module '{0}' not found in the DTT UI. Is the policy enabled on "
            "this platform?".format(module_name)
        )

    def get_module_data(self, group_id, module_id, timeout=None):
        return self.request(
            CMD_GET_MODULE_DATA.format(group=group_id, module=module_id), timeout
        )


def _parse_id_name(xml_text, child_tag):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise EsifError("unexpected reply from DTT: {0}".format(exc))
    items = []
    for node in root.findall(child_tag):
        node_id = node.findtext("id")
        name = node.findtext("name")
        if node_id is not None:
            items.append((node_id.strip(), (name or "").strip()))
    return items
