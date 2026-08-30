"""A stand-in for the DTT web server, driven by captured status XML.

The fixtures are real captures from a laptop, so the simulator only has to
recompute the parts that depend on the workload hint: each Workload minterm's
result, the logical operation results above it, and which action set therefore
wins.  That is enough to exercise the client, the parser and the whole test
loop on a machine that has no Intel DTT.
"""

import asyncio
import threading
import time
import xml.etree.ElementTree as ET

import websockets

GROUPS_XML = """<groups>
    <group><id>0</id><name>Policies</name></group>
    <group><id>1</id><name>Participants</name></group>
</groups>"""

MODULES_XML = {
    "0": """<modules>
    <module><id>0</id><name>Adaptive Performance Policy</name></module>
    <module><id>1</id><name>Application Optimization Policy</name></module>
</modules>""",
    "1": "<modules></modules>",
}

HINT_BY_PROCESS = {}


def _compare(value, comparison, argument):
    if comparison == "==":
        return value == argument
    if comparison == "!=":
        return value != argument
    try:
        left, right = float(value), float(argument)
    except (TypeError, ValueError):
        return False
    return {
        "<": left < right, "<=": left <= right,
        ">": left > right, ">=": left >= right,
    }.get(comparison, False)


class DttSimulator:
    def __init__(self, template_path, extra_fixtures=()):
        self.template = ET.parse(template_path).getroot()
        self.workload = "X"
        self.power_source = "AC"
        self.lock = threading.Lock()

        # Real DTT swaps the applied requests (PL1MAX, PL1MIN, ...) when the
        # action set changes, so captures of each state supply those values.
        self.requests_by_action_set = {}
        for path in (template_path,) + tuple(extra_fixtures):
            root = ET.parse(path).getroot()
            names = {
                entry.findtext("action_id"): entry.findtext("action_set")
                for entry in root.findall("./actions_table/actions_table_entry")
            }
            active = names.get((root.findtext("active_action") or "").strip())
            directory = root.find("request_directory")
            if active and directory is not None:
                self.requests_by_action_set[active] = ET.tostring(
                    directory, encoding="unicode"
                )

        self.hint_by_process = {}
        for group in self.template.findall("./workload_hint_configuration/workload_group"):
            hint = group.findtext("id").strip()
            for app in group.findall("./applications/application"):
                for name in (app.text or "").split(";"):
                    name = " ".join(name.split()).lower()
                    if name:
                        self.hint_by_process[name] = hint

    def set_foreground(self, process_name):
        with self.lock:
            self.workload = self.hint_by_process.get((process_name or "").lower(), "X")

    def status_xml(self):
        with self.lock:
            workload, power = self.workload, self.power_source

        root = ET.fromstring(ET.tostring(self.template, encoding="unicode"))

        for cond in root.findall("./conditions_directory/condition"):
            kind = cond.findtext("condition_type")
            if kind == "Workload":
                cond.find("current_value").text = workload
            elif kind == "Power Source":
                cond.find("current_value").text = power

        action_names = {
            entry.findtext("action_id"): entry.findtext("action_set")
            for entry in root.findall("./actions_table/actions_table_entry")
        }

        active = None
        for entry in root.findall("./conditions_table/conditions_table_entry"):
            all_true = True
            for op in entry.findall("logical_operation"):
                op_result = True
                for minterm in op.findall("minterm"):
                    condition = minterm.findtext("condition")
                    if condition == "Workload":
                        result = _compare(workload, minterm.findtext("comparison"),
                                          minterm.findtext("argument"))
                        minterm.find("result").text = "true" if result else "false"
                    elif condition == "Power Source":
                        result = _compare(power, minterm.findtext("comparison"),
                                          minterm.findtext("argument"))
                        minterm.find("result").text = "true" if result else "false"
                    else:
                        result = minterm.findtext("result") == "true"
                    op_result = op_result and result
                op.find("result").text = "true" if op_result else "false"
                all_true = all_true and op_result
            if all_true and active is None:
                active = entry.findtext("action_id")

        root.find("active_action").text = active or ""

        active_name = action_names.get(active, "")
        captured = self.requests_by_action_set.get(active_name)
        if captured is not None:
            existing = root.find("request_directory")
            root.remove(existing)
            root.append(ET.fromstring(captured))
        else:
            for request in root.findall("./request_directory/request"):
                if request.findtext("code") == "IEOT":
                    request.find("argument").text = active_name

        return ET.tostring(root, encoding="unicode")


class MockDttServer:
    """Serves the simulator over the same protocol shape as esif_ws."""

    def __init__(self, simulator, host="127.0.0.1", port=0):
        self.simulator = simulator
        self.host = host
        self.port = port
        self.requests = []
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
        self._stop = None

    async def _handler(self, connection):
        async for message in connection:
            message_id, _, command = message.partition(":")
            self.requests.append(command)
            if command == "dptf ui getgroups":
                payload = GROUPS_XML
            elif command.startswith("dptf ui getmodulesingroup"):
                payload = MODULES_XML.get(command.rsplit(" ", 1)[-1], "<modules></modules>")
            elif command.startswith("dptf ui getmoduledata"):
                payload = self.simulator.status_xml()
            else:
                payload = "There is no such command"
            await connection.send("{0}:{1}".format(message_id, payload))

    def start(self):
        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            async def serve():
                self._stop = asyncio.Event()
                async with websockets.serve(
                    self._handler, self.host, self.port, max_size=None
                ) as server:
                    self.port = server.sockets[0].getsockname()[1]
                    self._ready.set()
                    await self._stop.wait()

            try:
                loop.run_until_complete(serve())
            finally:
                loop.close()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            raise RuntimeError("mock DTT server did not start")
        return self

    def stop(self):
        # Idempotent: tests stop the server explicitly and again on cleanup.
        if self._loop and self._stop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *_exc):
        self.stop()


class FakeLauncher:
    """Stands in for WindowsLauncher, with a configurable hint debounce."""

    def __init__(self, simulator, debounce=0.0, fail_to_launch=(),
                 never_foreground=()):
        self.simulator = simulator
        self.debounce = debounce
        self.fail_to_launch = set(fail_to_launch)
        self.never_foreground = set(never_foreground)
        self.timers = []

    def _set_foreground_after_debounce(self, process_name):
        if self.debounce <= 0:
            self.simulator.set_foreground(process_name)
            return
        timer = threading.Timer(
            self.debounce, self.simulator.set_foreground, args=(process_name,)
        )
        timer.daemon = True
        timer.start()
        self.timers.append(timer)

    def launch(self, app):
        name = app["process_name"]
        if name in self.fail_to_launch:
            raise RuntimeError("simulated launch failure for " + name)
        return {"process_name": name}

    def wait_for_foreground(self, handle, timeout):
        name = handle["process_name"]
        if name in self.never_foreground:
            raise RuntimeError("{0} never reached the foreground".format(name))
        self._set_foreground_after_debounce(name)
        return time.monotonic()

    def close(self, handle, grace):
        self._set_foreground_after_debounce(None)
        return ""

    def go_baseline(self):
        for timer in self.timers:
            timer.cancel()
        self.timers = []
        self.simulator.set_foreground(None)
        return True

    def foreground_process_name(self):
        return "dtt-wl-validator.exe"


class PickyServer:
    """A raw TCP server that answers HTTP but is fussy about handshakes.

    Stands in for a DTT web server that goes silent on a handshake it does not
    like, which is what makes the failure hard to read: a rejection closes the
    connection, but an unrecognised request just waits for more data.
    """

    def __init__(self, required_header=None, host="127.0.0.1", port=0):
        self.required_header = required_header  # (name, value) or None = never accept
        self.host = host
        self.port = port
        self.requests = []
        self._socket = None
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        import socket

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.host, self.port))
        self._socket.listen(8)
        self.port = self._socket.getsockname()[1]
        self._socket.settimeout(0.2)

        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def _serve(self):
        import socket

        while not self._stop.is_set():
            try:
                connection, _address = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(connection,),
                             daemon=True).start()

    def _handle(self, connection):
        import base64
        import hashlib
        import socket

        connection.settimeout(10)
        data = b""
        try:
            while b"\r\n\r\n" not in data:
                chunk = connection.recv(4096)
                if not chunk:
                    return
                data += chunk
        except (socket.timeout, OSError):
            return

        text = data.decode("latin-1")
        self.requests.append(text)
        headers = {}
        for line in text.split("\r\n")[1:]:
            name, _, value = line.partition(": ")
            if value:
                headers[name.lower()] = value

        if headers.get("upgrade", "").lower() != "websocket":
            body = b"<html>ok</html>"
            connection.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: " +
                str(len(body)).encode() + b"\r\n\r\n" + body)
            connection.close()
            return

        if self.required_header is not None:
            name, value = self.required_header
            if headers.get(name.lower()) == value:
                key = headers.get("sec-websocket-key", "")
                accept = base64.b64encode(hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
                ).digest()).decode()
                connection.sendall(
                    ("HTTP/1.1 101 Switching Protocols\r\n"
                     "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                     "Sec-WebSocket-Accept: {0}\r\n\r\n".format(accept)).encode())
                self._stop.wait(5)
                connection.close()
                return

        # Go quiet, the way the real server does on an incomplete request.
        self._stop.wait(30)
        connection.close()

    def stop(self):
        self._stop.set()
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *_exc):
        self.stop()
