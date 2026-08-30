"""The test loop: launch, foreground, watch DTT, compare, clean up."""

import ntpath
import os
import tempfile
import time

from .esif import EsifError, EsifSession
from .report import ERROR, FAIL, PASS, SKIP, ResultRow
from .status import derive_expected_modes, parse_status


class PreflightError(Exception):
    pass


class RunCancelled(Exception):
    pass


class RunObserver:
    """Progress callbacks; the GUI implements these, the CLI ignores them."""

    def phase(self, text):
        pass

    def case_started(self, app, round_number, index, total):
        pass

    def sample(self, app, status, elapsed):
        pass

    def case_finished(self, row):
        pass


class Detector:
    """Reads the Adaptive Performance Policy status straight from DTT.

    One WebSocket session is reused for the whole run; a dropped connection is
    retried once per read so a transient hiccup does not fail a test case.
    """

    def __init__(self, config, log=None):
        dtt = config["dtt"]
        self.host = dtt["host"]
        self.port = int(dtt["port"])
        self.path = dtt["path"]
        self.module_name = dtt["policy_module"]
        self.timeout = float(dtt["request_timeout_seconds"])
        self.log = log or (lambda _m: None)
        self.session = None
        self.group_id = None
        self.module_id = None

    def open(self):
        self.session = EsifSession(self.host, self.port, self.path, self.timeout)
        self.session.connect()
        self.group_id, self.module_id = self.session.find_module(self.module_name)
        self.log(
            "connected to DTT; '{0}' is group {1} / module {2}".format(
                self.module_name, self.group_id, self.module_id
            )
        )
        return self

    def close(self):
        if self.session is not None:
            self.session.close()
            self.session = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *_exc):
        self.close()

    def read(self):
        try:
            xml = self.session.get_module_data(self.group_id, self.module_id)
        except EsifError as first_error:
            self.log("DTT read failed ({0}); reconnecting".format(first_error))
            self.close()
            self.open()
            xml = self.session.get_module_data(self.group_id, self.module_id)
        return parse_status(xml)


def browser_profile_root():
    return os.path.join(tempfile.gettempdir(), "dtt_wl_profiles")


def browser_launch_plan(config, app, profile_root):
    """(exe, args) to start a browser in its own instance, or None.

    A shortcut is resolved to its target first, because the isolation only
    works if arguments can be passed, and a .lnk takes none.
    """
    isolation = config.get("browser_isolation") or {}
    if not isolation.get("enabled", True):
        return None
    names = [name.lower() for name in isolation.get("process_names", [])]
    process_name = app["process_name"].lower()
    if process_name not in names:
        return None

    executable = app.get("exe_path") or ""
    if executable.lower().endswith(".lnk"):
        from . import shortcuts

        executable = shortcuts.resolve_shortcut(executable) or ""
    if not executable.lower().endswith(".exe"):
        return None

    profile = ntpath.join(profile_root, ntpath.splitext(process_name)[0])
    args = ["--user-data-dir={0}".format(profile)]
    args += list(isolation.get("extra_args", []))
    return executable, args


class WindowsLauncher:
    """Adapter around winfg so the runner can be exercised without Windows."""

    def __init__(self, config, log=None, baseline_hwnd=None):
        self.config = config
        self.log = log or (lambda _m: None)
        self.baseline_hwnd = baseline_hwnd
        from . import winfg

        self.winfg = winfg

    def launch(self, app):
        if self.config["run"]["mode"] == "stub":
            from . import stub

            path, args = stub.make_stub_launch(
                app["process_name"], app["app_name"] or app["process_name"])
            return self.winfg.launch(
                exe_path=path, args=args, process_name=app["process_name"])
        plan = browser_launch_plan(self.config, app, browser_profile_root())
        if plan is not None:
            executable, args = plan
            self.log("launching {0} with its own profile so the browser "
                     "already running is untouched".format(app["process_name"]))
            return self.winfg.launch(
                exe_path=executable,
                args=args + list(app.get("args") or []),
                process_name=app["process_name"],
            )

        return self.winfg.launch(
            exe_path=app["exe_path"] or None,
            shell_target=app["shell_target"] or None,
            args=app.get("args") or [],
            process_name=app["process_name"],
        )

    def wait_for_foreground(self, handle, timeout):
        return self.winfg.wait_for_foreground(handle, timeout)

    def close(self, handle, grace):
        return self.winfg.close_app(handle, grace)

    def go_baseline(self):
        return self.winfg.focus_self(self.baseline_hwnd)

    def foreground_process_name(self):
        return self.winfg.foreground_process_name()


class Runner:
    def __init__(self, config, detector, launcher, log=None, observer=None,
                 cancel_event=None):
        self.config = config
        self.detector = detector
        self.launcher = launcher
        self.log = log or (lambda _m: None)
        self.observer = observer or RunObserver()
        self._current_app = None
        self.cancel_event = cancel_event
        self.baseline_mode = config.get("baseline_mode")
        self.expected_by_hint = {}
        self.rows = []

    def _check_cancelled(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise RunCancelled()

    # -- preflight ---------------------------------------------------------

    def preflight(self):
        """Check the machine is in a state where WL1/WL2 can be reached at all.

        Running 30 applications only to have every one fail because the laptop
        is on battery wastes half an hour, so the blocking conditions are
        checked once up front.
        """
        problems = []
        self._warn_about_timing()
        self.launcher.go_baseline()
        status = self.detector.read()

        required_power = self.config["preflight"].get("require_power_source")
        if required_power and status.power_source != required_power:
            problems.append(
                "Power Source is {0}, but WL1/WL2 need {1} -- plug the machine in".format(
                    status.power_source, required_power
                )
            )

        for index, expected in (self.config["preflight"].get("require_oem_variables") or {}).items():
            position = int(index)
            actual = (
                status.oem_variables[position]
                if position < len(status.oem_variables)
                else None
            )
            if actual != str(expected):
                problems.append(
                    "OEM Variable {0} is {1}, expected {2}".format(position, actual, expected)
                )

        # Read the action set for each workload hint off this platform, so a
        # config written on a machine that names them optimized_WL* still works
        # on one that names them AC_O_WL*.
        self.expected_by_hint = derive_expected_modes(
            status, self.config.get("expected_mode_by_hint"))
        if self.expected_by_hint:
            self.log("workload hint mapping: " + ", ".join(
                "{0} -> {1}".format(hint, name)
                for hint, name in sorted(self.expected_by_hint.items())))
        self._apply_expected_modes()

        known = set(status.action_sets.values())
        for app in self._enabled_apps():
            if not app["expected_mode"]:
                problems.append(
                    "no action set found for workload hint {0} ({1}); this "
                    "platform may not use that hint".format(
                        app.get("workload_hint"), app["process_name"])
                )
                break
            if app["expected_mode"] not in known:
                problems.append(
                    "expected_mode '{0}' for {1} is not in the platform's action "
                    "table".format(app["expected_mode"], app["process_name"])
                )
                break

        if self.baseline_mode is None:
            self.baseline_mode = status.active_action_set
            self.log("learned baseline action set: {0}".format(self.baseline_mode))
        elif status.active_action_set != self.baseline_mode:
            self.log(
                "note: idle action set is '{0}' but config expects '{1}'".format(
                    status.active_action_set, self.baseline_mode
                )
            )

        whitelisted = {
            name for names in status.workload_groups.values() for name in names
        }
        current = ""
        try:
            current = self.launcher.foreground_process_name()
        except Exception:  # pragma: no cover - diagnostic only
            pass
        if current and current in whitelisted:
            problems.append(
                "the foreground window belongs to '{0}', which is whitelisted; "
                "the baseline is not neutral".format(current)
            )

        self.log("preflight: power={0}, workload={1}, active={2}, SEN6={3}".format(
            status.power_source, status.workload_value, status.active_action_set,
            status.temperatures.get("SEN6", "n/a"),
        ))
        return problems, status

    def _warn_about_timing(self):
        """Say so when the timings leave almost no room for a slow switch.

        A pass needs `stable_read_samples` consecutive matching reads, so the
        earliest one can be recorded is the debounce plus the polling in
        between. If that lands close to the detect timeout, a switch that is
        merely slower than usual is reported as a failure.
        """
        timing = self.config["timing"]
        debounce = float(timing["debounce_buffer_seconds"])
        poll = float(timing["poll_interval_seconds"])
        samples = int(timing["stable_read_samples"])
        timeout = float(timing["detect_timeout_seconds"])

        earliest = debounce + (samples - 1) * poll
        margin = timeout - earliest
        if margin >= 3.0:
            return

        self.log(
            "warning: a pass cannot be recorded before {0:.1f}s (debounce "
            "{1:.1f}s plus {2} more reads {3:.1f}s apart), leaving only "
            "{4:.1f}s before the {5:.1f}s detect timeout. A switch slower than "
            "usual will be reported as a failure - raise the detect timeout or "
            "lower the poll interval.".format(
                earliest, debounce, samples - 1, poll, margin, timeout))

    def _apply_expected_modes(self):
        """Point each application at the action set this platform actually uses."""
        for app in self.config["apps"]:
            hint = app.get("workload_hint")
            if hint is None:
                continue
            derived = self.expected_by_hint.get(str(hint))
            if derived and derived != app.get("expected_mode"):
                self.log("  {0}: expected mode {1} -> {2}".format(
                    app["process_name"], app.get("expected_mode") or "(unset)",
                    derived))
                app["expected_mode"] = derived

    def verify_stub_assumption(self, status):
        """Confirm that a renamed executable is enough to trigger the hint.

        Stub mode rests entirely on DTT matching the foreground executable by
        name.  If that is wrong every stub result would be a false failure, so
        the assumption is proven on this machine before any of it is trusted.
        """
        candidate = None
        for app in self._enabled_apps():
            if app["expected_mode"] != self.baseline_mode:
                candidate = app
                break
        if candidate is None:
            raise PreflightError("no application available to verify stub mode with")

        self.log("verifying stub mode using {0}".format(candidate["process_name"]))
        row = self._run_case(candidate, round_number=0, mode="stub-verify")
        if row.result != PASS:
            raise PreflightError(
                "stub mode did not work on this machine: launching a renamed "
                "copy as '{0}' did not switch DTT to {1} ({2}). Use "
                "run.mode = 'real' instead.".format(
                    candidate["process_name"], candidate["expected_mode"], row.reason
                )
            )
        self.log("stub mode verified: DTT matches on executable name alone")
        return row

    # -- main loop ---------------------------------------------------------

    def run(self):
        rounds = int(self.config["run"]["rounds"])
        stop_early = bool(self.config["run"].get("stop_on_first_failure"))
        apps = self._enabled_apps()
        total = rounds * len(apps)
        index = 0

        try:
            for round_number in range(1, rounds + 1):
                self.log("--- round {0} of {1} ---".format(round_number, rounds))
                for app in apps:
                    self._check_cancelled()
                    index += 1
                    self.observer.case_started(app, round_number, index, total)
                    row = self._run_case(app, round_number, self.config["run"]["mode"])
                    self.rows.append(row)
                    self.observer.case_finished(row)
                    self.log("  {0:<24} {1:<7} {2}".format(
                        row.process_name, row.result,
                        row.reason or "detected {0} in {1}".format(
                            row.detected_mode,
                            "{0:.2f}s".format(row.switch_latency_s)
                            if row.switch_latency_s is not None else "n/a",
                        ),
                    ))
                    if stop_early and row.result == FAIL:
                        self.log("stopping early after first failure")
                        return self.rows
        except RunCancelled:
            self.log("run cancelled; keeping the {0} result(s) collected so "
                     "far".format(len(self.rows)))
        return self.rows

    def _enabled_apps(self):
        return [app for app in self.config["apps"] if app.get("enabled", True)]

    def _skip_reason(self, app, mode):
        if mode == "real":
            if not app.get("exe_path") and not app.get("shell_target"):
                return "no exe_path configured"
            if app.get("exe_path") and not os.path.isfile(app["exe_path"]):
                return "not installed at {0}".format(app["exe_path"])
        return None

    def _run_case(self, app, round_number, mode):
        timing = self.config["timing"]
        row = ResultRow(
            app_name=app.get("app_name") or app["process_name"],
            process_name=app["process_name"],
            round_number=round_number,
            mode=mode,
            expected_mode=app["expected_mode"],
        )

        self._current_app = app
        self.observer.phase("preparing {0}".format(app["process_name"]))

        skip = self._skip_reason(app, mode)
        if skip:
            row.result = SKIP
            row.reason = skip
            return row

        self.observer.phase("waiting for baseline '{0}'".format(self.baseline_mode))
        if not self._return_to_baseline(row, timing):
            return row

        self.observer.phase("launching {0}".format(app["process_name"]))
        handle = None
        try:
            handle = self.launcher.launch(app)
            t0 = self.launcher.wait_for_foreground(
                handle, float(timing["app_launch_timeout_seconds"])
            )
        except Exception as exc:
            row.result = ERROR
            row.reason = str(exc)
            if handle is not None:
                self._safe_close(handle, timing, row)
            return row

        matched, latency, status = self._watch_for_mode(app["expected_mode"], t0, timing)
        self._fill_state(row, status)
        row.detected_mode = status.active_action_set if status else ""

        if matched:
            row.result = PASS
            row.switch_latency_s = latency
            buffer_seconds = float(timing["debounce_buffer_seconds"])
            if latency is not None and latency > buffer_seconds:
                row.notes = _join(
                    row.notes,
                    "slow switch: {0:.2f}s > debounce buffer {1:.1f}s".format(
                        latency, buffer_seconds
                    ),
                )
        else:
            row.result = FAIL
            row.reason = (
                status.explain(app["expected_mode"]) if status else "no reading from DTT"
            )

        close_note = self._safe_close(handle, timing, row)
        if close_note:
            row.notes = _join(row.notes, close_note)

        deassert = self._measure_deassert(timing)
        if deassert is None:
            row.notes = _join(
                row.notes,
                "did not return to baseline '{0}' within {1:.0f}s (state bleed)".format(
                    self.baseline_mode, float(timing["baseline_timeout_seconds"])
                ),
            )
        else:
            row.deassert_latency_s = deassert
        return row

    def _watch_for_mode(self, expected, t0, timing):
        """Poll DTT until `expected` has been the active action set N times.

        Polling from the moment the window reached the foreground -- rather
        than sleeping for the debounce and taking one reading -- both avoids
        reading mid-switch and yields the real switch latency.
        """
        poll = float(timing["poll_interval_seconds"])
        needed = int(timing["stable_read_samples"])
        deadline = t0 + float(timing["detect_timeout_seconds"])

        consecutive = 0
        first_match = None
        status = None

        while True:
            self._check_cancelled()
            try:
                status = self.detector.read()
            except EsifError:
                status = None

            self.observer.sample(self._current_app, status, time.monotonic() - t0)

            if status is not None and status.active_action_set == expected:
                if consecutive == 0:
                    first_match = time.monotonic()
                consecutive += 1
                if consecutive >= needed:
                    return True, first_match - t0, status
            else:
                consecutive = 0
                first_match = None

            if time.monotonic() >= deadline:
                return False, None, status
            time.sleep(poll)

    def _return_to_baseline(self, row, timing):
        try:
            self.launcher.go_baseline()
        except Exception as exc:  # pragma: no cover - diagnostic only
            self.log("could not focus the baseline window: {0}".format(exc))

        if self.baseline_mode is None:
            return True

        deadline = time.monotonic() + float(timing["baseline_timeout_seconds"])
        last = None
        while True:
            self._check_cancelled()
            try:
                last = self.detector.read()
            except EsifError as exc:
                row.result = ERROR
                row.reason = "DTT unreachable before the test case: {0}".format(exc)
                return False
            if last.active_action_set == self.baseline_mode:
                return True
            if time.monotonic() >= deadline:
                row.result = ERROR
                row.reason = (
                    "machine was not at baseline '{0}' before the test case "
                    "(active: {1})".format(self.baseline_mode, last.active_action_set)
                )
                self._fill_state(row, last)
                return False
            time.sleep(float(timing["poll_interval_seconds"]))

    def _measure_deassert(self, timing):
        if self.baseline_mode is None:
            return None
        try:
            self.launcher.go_baseline()
        except Exception:  # pragma: no cover - diagnostic only
            pass
        start = time.monotonic()
        deadline = start + float(timing["baseline_timeout_seconds"])
        while True:
            self._check_cancelled()
            try:
                status = self.detector.read()
            except EsifError:
                status = None
            if status is not None and status.active_action_set == self.baseline_mode:
                return time.monotonic() - start
            if time.monotonic() >= deadline:
                return None
            time.sleep(float(timing["poll_interval_seconds"]))

    def _safe_close(self, handle, timing, row):
        try:
            return self.launcher.close(handle, float(timing["close_grace_seconds"]))
        except Exception as exc:
            return "close failed: {0}".format(exc)
        finally:
            time.sleep(float(timing["settle_after_close_seconds"]))

    @staticmethod
    def _fill_state(row, status):
        if status is None:
            return
        row.workload_value = status.workload_value or ""
        row.power_source = status.power_source or ""
        row.temperature = status.temperatures.get("SEN6", "")
        row.pl1_max = status.requests.get("PL1MAX", "")
        row.pl1_min = status.requests.get("PL1MIN", "")


def _join(existing, addition):
    if not existing:
        return addition
    return existing + "; " + addition
