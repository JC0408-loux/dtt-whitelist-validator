"""Configuration loading, defaults, and generation from the live DTT tables."""

import copy
import json
import os

from .paths import default_report_dir

DEFAULTS = {
    "dtt": {
        "host": "localhost",
        "port": 8888,
        "path": "/echo",
        "policy_module": "Adaptive Performance Policy",
        "request_timeout_seconds": 10.0,
    },
    "timing": {
        # APAT debounces the hint before DTT sees it; the buffer is the minimum
        # wait before a reading counts, and the timeout bounds how long a switch
        # may take before the case is failed.
        "debounce_buffer_seconds": 5.0,
        "poll_interval_seconds": 2.0,
        "stable_read_samples": 3,
        "detect_timeout_seconds": 10.0,
        "baseline_timeout_seconds": 20.0,
        "app_launch_timeout_seconds": 30.0,
        "close_grace_seconds": 5.0,
        "settle_after_close_seconds": 1.0,
    },
    "run": {
        # "real" launches the installed application; "stub" launches a renamed
        # copy of this tool to exercise the whitelist entry by name only.
        "mode": "real",
        "rounds": 1,
        "stop_on_first_failure": False,
    },
    # Left empty on purpose: the action set for each workload hint is read
    # off the platform, so a machine naming them AC_O_WL1 / AC_O_WL2 needs no
    # configuration. An entry here overrides the derived name for that hint.
    "expected_mode_by_hint": {},
    # Left null so the runner learns the idle action set from the machine.
    "baseline_mode": None,
    "preflight": {
        "require_power_source": "AC",
        "require_oem_variables": {},
        "abort_on_failure": True,
    },
    # A browser started while one is already running just opens a window in
    # the existing instance, so the test would be closing the user's own
    # browser -- including the DTT page. Giving it a private profile directory
    # forces a separate instance that can be closed on its own.
    "browser_isolation": {
        "enabled": True,
        "process_names": ["msedge.exe", "chrome.exe"],
        "extra_args": ["--no-first-run", "--no-default-browser-check",
                       "--new-window", "about:blank"],
    },
    "search_paths": [
        "%USERPROFILE%\\Desktop",
        "%USERPROFILE%\\Downloads",
        "C:\\Program Files",
        "C:\\Program Files (x86)",
    ],
    "search_max_depth": 4,
    "report": {
        "output_dir": default_report_dir(),
        "formats": ["csv", "xlsx"],
    },
    "apps": [],
}

APP_DEFAULTS = {
    "app_name": "",
    "process_name": "",
    "exe_path": "",
    "shell_target": "",
    "args": [],
    "workload_hint": None,
    "expected_mode": "",
    "enabled": True,
    "notes": "",
}


class ConfigError(Exception):
    pass


def _merge(base, override):
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load(path):
    if not os.path.isfile(path):
        raise ConfigError("config file not found: {0}".format(path))
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except ValueError as exc:
        raise ConfigError("config file is not valid JSON: {0}".format(exc))

    config = _merge(DEFAULTS, raw)
    config["apps"] = [_merge(APP_DEFAULTS, app) for app in config.get("apps", [])]
    # An explicit "" means "use the default folder", not "write to the working
    # directory" - so a shared config file need not name one machine's profile.
    if not str(config["report"].get("output_dir", "")).strip():
        config["report"]["output_dir"] = default_report_dir()
    validate(config)
    return config


def save(config, path):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def validate(config):
    timing = config["timing"]
    for key in ("debounce_buffer_seconds", "poll_interval_seconds",
                "detect_timeout_seconds", "baseline_timeout_seconds"):
        if float(timing[key]) <= 0:
            raise ConfigError("timing.{0} must be greater than zero".format(key))
    if int(timing["stable_read_samples"]) < 1:
        raise ConfigError("timing.stable_read_samples must be at least 1")
    if timing["detect_timeout_seconds"] <= timing["debounce_buffer_seconds"]:
        raise ConfigError(
            "timing.detect_timeout_seconds must be larger than "
            "timing.debounce_buffer_seconds"
        )
    if int(config["run"]["rounds"]) < 1:
        raise ConfigError("run.rounds must be at least 1")
    if config["run"]["mode"] not in ("real", "stub"):
        raise ConfigError("run.mode must be either 'real' or 'stub'")

    seen = set()
    for app in config["apps"]:
        if not app.get("process_name"):
            raise ConfigError(
                "every app needs a process_name (offending entry: {0!r})".format(
                    app.get("app_name") or app
                )
            )
        name = app["process_name"].lower()
        if name in seen:
            raise ConfigError("duplicate process_name in config: {0}".format(name))
        seen.add(name)
        if not app.get("expected_mode") and app.get("workload_hint") is None:
            raise ConfigError(
                "app '{0}' has neither an expected_mode nor a workload_hint to "
                "derive one from".format(app["process_name"])
            )
    return config


def generate_from_status(status, base=None):
    """Build a starter config from DTT's own Workload Hint Configuration table.

    The mapping of executable to workload hint is read off the platform rather
    than typed in by hand, so it cannot drift from what DTT actually applies.
    Executable paths are left blank for `resolve_paths` or the user to fill in.
    """
    from .status import derive_expected_modes

    config = copy.deepcopy(base if base is not None else DEFAULTS)
    expected_by_hint = derive_expected_modes(
        status, config.get("expected_mode_by_hint"))

    apps = []
    for hint, names in sorted(status.workload_groups.items()):
        expected = expected_by_hint.get(str(hint), "")
        for name in names:
            app = copy.deepcopy(APP_DEFAULTS)
            app["app_name"] = os.path.splitext(name)[0]
            app["process_name"] = name
            app["workload_hint"] = int(hint) if str(hint).isdigit() else hint
            app["expected_mode"] = expected or ""
            apps.append(app)

    config["apps"] = apps
    if config.get("baseline_mode") is None:
        config["baseline_mode"] = status.predicted_action_set(0)
    return config


def resolve_paths(config, log=None):
    """Fill in blank exe_path values by searching the configured directories."""
    log = log or (lambda _message: None)
    roots = []
    for entry in config.get("search_paths", []):
        expanded = os.path.expandvars(os.path.expanduser(entry))
        if os.path.isdir(expanded):
            roots.append(expanded)
        else:
            log("search path does not exist, skipping: {0}".format(expanded))

    wanted = {
        app["process_name"].lower(): app
        for app in config["apps"]
        if not app.get("exe_path") and not app.get("shell_target")
    }
    if not wanted or not roots:
        return 0

    max_depth = int(config.get("search_max_depth", 4))
    found = 0
    for root in roots:
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
            if dirpath.count(os.sep) - base_depth >= max_depth:
                dirnames[:] = []
            for filename in filenames:
                app = wanted.get(filename.lower())
                if app is not None and not app["exe_path"]:
                    app["exe_path"] = os.path.join(dirpath, filename)
                    found += 1
                    log("found {0} -> {1}".format(filename, app["exe_path"]))
        if all(app["exe_path"] for app in wanted.values()):
            break
    return found
