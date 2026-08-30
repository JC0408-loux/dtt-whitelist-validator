"""Layered connection checks.

"Cannot reach the DTT web server" is not much help when the page clearly
opens in a browser. These checks walk the same path the tool takes, one layer
at a time, so the report says which layer actually broke: the port, the web
server, the WebSocket upgrade, the ESIF command set, or the policy module.
"""

import http.client
import socket
import subprocess
import sys

from .esif import CMD_GET_GROUPS, EsifError, EsifSession
from .status import StatusParseError, parse_status

OK = "OK"
FAILED = "FAILED"
SKIPPED = "SKIPPED"


class Check:
    def __init__(self, name, state, detail=""):
        self.name = name
        self.state = state
        self.detail = detail

    def __repr__(self):
        return "Check({0!r}, {1!r})".format(self.name, self.state)


def run(config, timeout=5.0):
    """Return the checks in order, stopping at the first that fails."""
    dtt = config["dtt"]
    host = dtt["host"]
    port = int(dtt["port"])
    path = dtt["path"]
    module_name = dtt["policy_module"]

    checks = []

    def add(name, state, detail=""):
        checks.append(Check(name, state, detail))
        return state == OK

    def remaining(names):
        for name in names:
            checks.append(Check(name, SKIPPED))

    # 1. the port itself, one resolved address at a time
    # `localhost` resolves to both ::1 and 127.0.0.1 on Windows, and a server
    # bound to only one of them looks like a total failure otherwise.
    reachable, lines = probe_addresses(host, port, timeout)
    if not reachable:
        listening = listening_report(port)
        if listening:
            lines = lines + "\n" + listening
        add("TCP port {0}:{1} reachable".format(host, port), FAILED, lines)
        remaining(["HTTP server responds", "WebSocket upgrade accepted",
                   "ESIF commands answered", "Policy module found",
                   "Policy status readable"])
        return checks

    add("TCP port {0}:{1} reachable".format(host, port), OK, lines)
    # Everything below talks to the address that answered.
    host = reachable

    # 2. the web server behind it
    try:
        # Not index.html: it is several megabytes, and asking for it only to
        # close after a few bytes leaves the server holding the rest in a send
        # buffer for a socket that has gone. That kept a client slot busy and
        # made the WebSocket attempts that follow time out - the check itself
        # was causing the failure it then reported. A path that does not exist
        # proves the HTTP layer just as well and returns almost nothing.
        probe_path = "/dtt-wl-validator-probe"
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
        connection.request("GET", probe_path)
        response = connection.getresponse()
        response.read()
        connection.close()
        add("HTTP server responds", OK,
            "GET {0} -> {1}".format(probe_path, response.status))
    except OSError as exc:
        add("HTTP server responds", FAILED, str(exc))

    # 3. the WebSocket upgrade
    session = EsifSession(host, port, path, timeout)
    try:
        session.connect()
        add("WebSocket upgrade accepted", OK,
            "ws://{0}:{1}{2} using the '{3}' handshake".format(
                host, port, path, session.variant_used["name"]))
    except EsifError as exc:
        # connect() has already tried every handshake and reports what each one
        # did, so probing them again here would only double the number of
        # connections. DTT's web server keeps ten client slots and does not
        # time out a connection it considers incomplete, so a burst of probes
        # can exhaust it and manufacture the failure being investigated.
        add("WebSocket upgrade accepted", FAILED, _wrap_attempts(str(exc)))
        remaining(["ESIF commands answered", "Policy module found",
                   "Policy status readable"])
        return checks

    try:
        # 4. does it answer the commands the page uses
        try:
            reply = session.request(CMD_GET_GROUPS)
        except EsifError as exc:
            add("ESIF commands answered", FAILED, str(exc))
            remaining(["Policy module found", "Policy status readable"])
            return checks
        add("ESIF commands answered", OK,
            "{0} -> {1} bytes".format(CMD_GET_GROUPS, len(reply)))

        # 5. is the policy actually present on this platform
        try:
            group_id, module_id = session.find_module(module_name)
        except EsifError as exc:
            add("Policy module found", FAILED, str(exc))
            checks.append(Check("Policy status readable", SKIPPED))
            return checks
        add("Policy module found", OK,
            "'{0}' is group {1} / module {2}".format(module_name, group_id, module_id))

        # 6. and can its status be parsed
        try:
            status = parse_status(session.get_module_data(group_id, module_id))
        except (EsifError, StatusParseError) as exc:
            add("Policy status readable", FAILED, str(exc))
            return checks
        add("Policy status readable", OK,
            "active action set: {0}, workload hint: {1}, power: {2}".format(
                status.active_action_set, status.workload_value, status.power_source))
    finally:
        session.close()

    return checks


def _run(command, timeout=10):
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                              ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _process_names():
    """pid -> image name, from tasklist. Empty off Windows or on failure."""
    names = {}
    for line in _run(["tasklist", "/FO", "CSV", "/NH"]).splitlines():
        fields = [field.strip('"') for field in line.split('","')]
        if len(fields) >= 2:
            name = fields[0].lstrip('"')
            try:
                names[int(fields[1])] = name
            except ValueError:
                pass
    return names


def listening_report(port):
    """What Windows says is actually listening, so the port is not guesswork.

    A browser reaching the page and the tool being refused cannot both be true
    of the same listening socket, so the question is which socket each one is
    really talking to.
    """
    if not sys.platform.startswith("win"):
        return ""

    output = _run(["netstat", "-ano", "-p", "tcp"])
    if not output:
        return ""

    names = _process_names()
    suffix = ":{0}".format(port)
    on_port = []
    dtt_elsewhere = []

    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[3].upper() != "LISTENING":
            continue
        local, pid = fields[1], fields[4]
        owner = names.get(int(pid), "?") if pid.isdigit() else "?"
        entry = "{0:<28} pid {1:<7} {2}".format(local, pid, owner)
        if local.endswith(suffix):
            on_port.append(entry)
        elif any(tag in owner.lower() for tag in ("esif", "dptf", "dptt")):
            dtt_elsewhere.append(entry)

    lines = []
    if on_port:
        lines.append("Listening on port {0}:".format(port))
        lines.extend("  " + entry for entry in on_port)
    else:
        lines.append("Nothing is listening on port {0}.".format(port))
    if dtt_elsewhere:
        lines.append("DTT appears to be listening elsewhere:")
        lines.extend("  " + entry for entry in dtt_elsewhere)
    return "\n".join(lines)


def probe_addresses(host, port, timeout):
    """Connect to each address `host` resolves to, and say what each one did.

    Returns (first address that accepted or "", one report line per address).
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return "", "{0} does not resolve: {1}".format(host, exc)

    reachable = ""
    lines = []
    seen = set()
    for family, socktype, proto, _canonname, sockaddr in infos:
        address = sockaddr[0]
        if address in seen:
            continue
        seen.add(address)

        connection = socket.socket(family, socktype, proto)
        connection.settimeout(timeout)
        try:
            connection.connect(sockaddr)
            lines.append("{0:<28} connected".format(address))
            if not reachable:
                reachable = address
        except OSError as exc:
            lines.append("{0:<28} {1}".format(address, exc))
        finally:
            connection.close()

    return reachable, "\n".join(lines)


def _wrap_attempts(message):
    """One handshake attempt per line, from connect()'s summary."""
    head, _, attempts = message.partition(" -- ")
    if not attempts:
        return message
    lines = [head.strip()]
    lines += ["  " + attempt.strip() for attempt in attempts.split("; ")]
    return "\n".join(lines)


def format_report(checks):
    lines = []
    for check in checks:
        mark = {OK: "[ ok ]", FAILED: "[FAIL]", SKIPPED: "[ -- ]"}[check.state]
        lines.append("{0} {1}".format(mark, check.name))
        if check.detail:
            for line in str(check.detail).splitlines():
                lines.append("       {0}".format(line))
    return "\n".join(lines)


def advice(checks):
    """A sentence about what to do, keyed to the first failing check."""
    failed = next((c for c in checks if c.state == FAILED), None)
    if failed is None:
        return "Everything responded. The tool can talk to DTT."

    if failed.name.startswith("TCP port"):
        detail = str(failed.detail)
        if "connected" in detail:
            working = detail.split()[0]
            return (
                "One address answered and another did not, so the DTT web "
                "server is only listening on one of them.\n\n"
                "Put {0} in the Host box instead of a name that resolves to "
                "both.".format(working)
            )
        if "Listening on port" in detail:
            return (
                "Something IS listening on that port, but it refused this "
                "connection.\n\n"
                "Compare the address above with the one that was tried. If it "
                "shows a specific address rather than 0.0.0.0, put that exact "
                "address in the Host box.\n\n"
                "If the addresses do match, a security product is letting the "
                "browser through and refusing this process. Ask for python.exe "
                "in the portable folder to be allowed, or run the tool from a "
                "folder that is already trusted."
            )
        if "listening elsewhere" in detail:
            return (
                "Nothing is on that port, but DTT is listening on another one. "
                "Put that port number in the Port box."
            )
        return (
            "Every address refused the connection, so nothing is listening on "
            "that port from this process's point of view.\n\n"
            "A DTT page already on screen does not prove otherwise - it keeps "
            "showing the last data it received. Press F5 on it. If it fails to "
            "reload, the DTT web server has stopped and needs restarting "
            "(services.msc, restart the Intel(R) Dynamic Tuning service, or "
            "reboot).\n\n"
            "If the page does reload, then the server is up and only this "
            "process is being refused: check the port matches the browser's "
            "address bar, and that a security product is not blocking "
            "python.exe."
        )
    if failed.name.startswith("HTTP server"):
        return (
            "The port is open but did not answer an HTTP request. Something "
            "else may be using that port."
        )
    if failed.name.startswith("WebSocket"):
        timed_out = "timed out" in str(failed.detail)
        if timed_out:
            return (
                "The web server is up, but it accepted the connection and then "
                "sent nothing back. DTT's web server does that when it decides "
                "the request is incomplete; it closes the connection outright "
                "when it rejects a handshake.\n\n"
                "The table above shows every handshake that was tried. Send it "
                "back, together with the WebSocket request headers the browser "
                "sends (F12, Network, filter WS, click the connection, Headers), "
                "so the two can be compared."
            )
        return (
            "The web server is up but refused the WebSocket upgrade, which is "
            "how the DTT page itself fetches its data.\n\n"
            "Check that the path in Settings is /echo, and that nothing else is "
            "holding the connection."
        )
    if failed.name.startswith("ESIF"):
        return (
            "The socket connected but the ESIF command was not answered. The "
            "DTT service may still be starting up; wait a moment and retry."
        )
    if failed.name.startswith("Policy module"):
        return (
            "DTT is reachable but does not list that policy. Open the DTT page, "
            "check the Policies menu for the exact name, and put it in "
            "dtt.policy_module in config.json."
        )
    return "The policy was found but its status could not be read."
