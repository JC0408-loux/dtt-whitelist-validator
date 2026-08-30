"""Command line entry point for the DTT whitelist validation tool."""

import argparse
import os
import sys
import time

from . import config as config_module
from . import diagnose as diagnose_module
from . import report as report_module
from .esif import EsifError
from .runner import Detector, PreflightError, Runner, WindowsLauncher
from .status import StatusParseError

DEFAULT_CONFIG = "config.json"


def log(message):
    print(message, flush=True)


def _detector(config):
    return Detector(config, log=log)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_diagnose(args):
    config = _load_config(args) if os.path.isfile(args.config) else _bootstrap_config(args)
    checks = diagnose_module.run(config)
    log("")
    log(diagnose_module.format_report(checks))
    log("")
    log(diagnose_module.advice(checks))
    return 1 if any(c.state == diagnose_module.FAILED for c in checks) else 0


def cmd_status(args):
    config = _load_config(args)
    with _detector(config) as detector:
        status = detector.read()

    log("")
    log("Active action set : {0}  (action_id {1})".format(
        status.active_action_set, status.active_action_id))
    log("Workload hint     : {0}".format(status.workload_value))
    log("Power source      : {0}".format(status.power_source))
    log("OEM variables     : {0}".format(", ".join(
        "{0}={1}".format(i, v) for i, v in enumerate(status.oem_variables))))
    log("Temperatures      : {0}".format(status.temperatures or "n/a"))
    log("Applied requests  : PL1MAX={0} PL1MIN={1} IEOT={2}".format(
        status.requests.get("PL1MAX", "-"), status.requests.get("PL1MIN", "-"),
        status.requests.get("IEOT", "-")))
    log("")
    log("Conditions table (arbitration order, first fully satisfied row wins):")
    for row in status.rows:
        marker = "->" if row.action_id == status.active_action_id else "  "
        state = "ALL TRUE" if row.satisfied else "        "
        log("  {0} {1:<20} {2}  {3}".format(
            marker, row.action_set, state,
            "; ".join(
                "{0}{1}".format("" if m.result else "!", m.describe())
                for m in row.minterms
            ),
        ))
    log("")
    log("Predicted action set by workload hint:")
    for hint in ("0", "1", "2"):
        log("  hint {0} -> {1}".format(hint, status.predicted_action_set(hint)))
    return 0


def cmd_watch(args):
    config = _load_config(args)
    interval = float(args.interval)
    log("Watching DTT every {0:.1f}s; press Ctrl+C to stop.".format(interval))
    previous = None
    started = time.monotonic()
    with _detector(config) as detector:
        try:
            while True:
                status = detector.read()
                key = (status.active_action_set, status.workload_value,
                       status.power_source)
                if key != previous:
                    log("+{0:7.2f}s  action={1:<18} workload={2:<3} power={3}".format(
                        time.monotonic() - started, status.active_action_set,
                        status.workload_value, status.power_source))
                    previous = key
                time.sleep(interval)
        except KeyboardInterrupt:
            log("stopped")
    return 0


def cmd_init_config(args):
    path = args.config
    if os.path.exists(path) and not args.force:
        log("{0} already exists; pass --force to overwrite it.".format(path))
        return 1

    base = config_module.DEFAULTS
    if os.path.exists(path):
        try:
            base = config_module.load(path)
        except config_module.ConfigError:
            base = config_module.DEFAULTS

    detector = Detector(_bootstrap_config(args), log=log)
    with detector:
        status = detector.read()

    generated = config_module.generate_from_status(status, base)
    log("read {0} applications from DTT's Workload Hint Configuration".format(
        len(generated["apps"])))

    if args.resolve:
        found = config_module.resolve_paths(generated, log=log)
        log("resolved {0} executable path(s)".format(found))

    config_module.save(generated, path)
    log("wrote {0}".format(path))
    log("")
    log("Next: fill in exe_path for the applications you want to launch for real,")
    log("or set run.mode to 'stub' to test the whitelist entries by name.")
    return 0


def cmd_resolve_paths(args):
    config = _load_config(args)
    found = config_module.resolve_paths(config, log=log)
    config_module.save(config, args.config)
    log("resolved {0} executable path(s); updated {1}".format(found, args.config))
    return 0


def cmd_verify_stub(args):
    config = _load_config(args)
    config["run"]["mode"] = "stub"
    with _detector(config) as detector:
        runner = Runner(config, detector, WindowsLauncher(config, log), log)
        problems, status = runner.preflight()
        for problem in problems:
            log("preflight: {0}".format(problem))
        if problems and config["preflight"].get("abort_on_failure", True):
            return 2
        try:
            runner.verify_stub_assumption(status)
        except PreflightError as exc:
            log("")
            log("RESULT: stub mode is NOT usable on this machine.")
            log(str(exc))
            return 1
    log("")
    log("RESULT: stub mode works. DTT matches the whitelist on executable name,")
    log("so applications do not have to be installed to validate their entry.")
    return 0


def cmd_gui(args):
    from .gui import launch

    return launch(args.config)


def cmd_run(args):
    config = _load_config(args)
    if args.rounds:
        config["run"]["rounds"] = args.rounds
    if args.mode:
        config["run"]["mode"] = args.mode

    with _detector(config) as detector:
        runner = Runner(config, detector, WindowsLauncher(config, log), log)

        problems, status = runner.preflight()
        for problem in problems:
            log("preflight: {0}".format(problem))
        if problems and config["preflight"].get("abort_on_failure", True):
            log("")
            log("Aborting: fix the problems above, or set "
                "preflight.abort_on_failure to false to run anyway.")
            return 2

        if config["run"]["mode"] == "stub":
            try:
                runner.verify_stub_assumption(status)
            except PreflightError as exc:
                log("")
                log(str(exc))
                return 2

        rows = runner.run()

    return _write_reports(config, rows)


def _write_reports(config, rows):
    output_dir = config["report"]["output_dir"]
    formats = config["report"]["formats"]
    paths = report_module.timestamped_paths(output_dir, formats)

    written = []
    if "csv" in paths:
        written.append(report_module.write_simple_csv(rows, paths["csv"]))
        details = paths["csv"].replace("_report_", "_details_")
        written.append(report_module.write_csv(rows, details))
    if "xlsx" in paths:
        result = report_module.write_xlsx(rows, paths["xlsx"])
        if result:
            written.append(result)
        else:
            log("note: openpyxl is not bundled in this build, CSV only")

    summary = report_module.summarize(rows)
    failing = [line for line in summary if line["verdict"] in ("FAIL", "INTERMITTENT")]

    log("")
    log("=" * 64)
    log("SUMMARY  ({0} applications, {1} test cases)".format(len(summary), len(rows)))
    log("=" * 64)
    if failing:
        for line in failing:
            log("  {0:<12} {1:<26} expected {2:<18} {3}/{4} rounds passed".format(
                line["verdict"], line["process_name"], line["expected_mode"],
                line["passed"], line["rounds"]))
    else:
        log("  no failures")

    not_tested = [line for line in summary if line["verdict"] == "NOT TESTED"]
    if not_tested:
        log("")
        log("  {0} application(s) skipped (not installed / no path configured)".format(
            len(not_tested)))

    log("")
    for path in written:
        log("report: {0}".format(path))

    return 1 if failing else 0


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------


def _bootstrap_config(args):
    """Connection settings only, for commands that run before a config exists."""
    config = config_module._merge(config_module.DEFAULTS, {})
    if args.host:
        config["dtt"]["host"] = args.host
    if args.port:
        config["dtt"]["port"] = args.port
    return config


def _load_config(args):
    config = config_module.load(args.config)
    if args.host:
        config["dtt"]["host"] = args.host
    if args.port:
        config["dtt"]["port"] = args.port
    return config


def build_parser():
    parser = argparse.ArgumentParser(
        prog="dtt-wl-validator",
        description="Validate that launching a whitelisted application switches "
                    "Intel DTT to the expected WL1/WL2 action set.",
    )
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG,
                        help="config file (default: %(default)s)")
    parser.add_argument("--host", help="override the DTT web server host")
    parser.add_argument("--port", type=int, help="override the DTT web server port")

    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run the whole validation sweep (default)")
    run.add_argument("--rounds", type=int, help="override run.rounds")
    run.add_argument("--mode", choices=["real", "stub"], help="override run.mode")
    run.set_defaults(func=cmd_run)

    gui = sub.add_parser("gui", help="open the desktop window (default when the "
                                     "executable is started with no arguments)")
    gui.set_defaults(func=cmd_gui)

    diagnose = sub.add_parser("diagnose",
                              help="check each layer of the connection to DTT "
                                   "and say which one failed")
    diagnose.set_defaults(func=cmd_diagnose)

    status = sub.add_parser("status", help="print the current DTT state once")
    status.set_defaults(func=cmd_status)

    watch = sub.add_parser("watch", help="print DTT state changes as they happen")
    watch.add_argument("--interval", type=float, default=0.5)
    watch.set_defaults(func=cmd_watch)

    init = sub.add_parser("init-config",
                          help="generate a config from DTT's own whitelist table")
    init.add_argument("--force", action="store_true", help="overwrite an existing file")
    init.add_argument("--resolve", action="store_true",
                      help="also search for executable paths")
    init.set_defaults(func=cmd_init_config)

    resolve = sub.add_parser("resolve-paths",
                             help="fill in blank exe_path values by searching")
    resolve.set_defaults(func=cmd_resolve_paths)

    verify = sub.add_parser("verify-stub",
                            help="check whether renaming an executable is enough "
                                 "to trigger the workload hint")
    verify.set_defaults(func=cmd_verify_stub)

    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # The packaged executable doubles as the stub window when copied under
    # another name, so this is handled before any argument parsing.
    from . import stub

    if stub.is_stub_invocation(argv):
        title = "DTT whitelist stub"
        if "--title" in argv:
            index = argv.index("--title")
            if index + 1 < len(argv):
                title = argv[index + 1]
        stub.run_stub_window(title)
        return 0

    parser = build_parser()
    # Started with no arguments (a double-click) means the window, not a sweep.
    args = parser.parse_args(argv or ["gui"])
    if getattr(args, "func", None) is None:
        args = parser.parse_args(argv + ["run"])

    try:
        return args.func(args)
    except config_module.ConfigError as exc:
        log("config error: {0}".format(exc))
        return 2
    except (EsifError, StatusParseError) as exc:
        log("DTT error: {0}".format(exc))
        return 2
    except KeyboardInterrupt:
        log("interrupted")
        return 130
