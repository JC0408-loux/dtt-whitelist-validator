"""End-to-end tests against a simulated DTT server.

Everything except the Windows-only foreground control is exercised here: the
WebSocket client, the ESIF request framing, the status parser, the arbitration
logic, the polling loop, latency measurement and the report writer.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dttwl import config as config_module
from dttwl.report import ERROR, FAIL, PASS, SKIP, summarize, write_csv
from dttwl.runner import Detector, Runner
from dttwl.status import parse_status, split_application_names
from tests.mock_dtt import DttSimulator, FakeLauncher, MockDttServer

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
WL1 = os.path.join(FIXTURES, "status_wl1.xml")
WL2 = os.path.join(FIXTURES, "status_wl2.xml")


def read_fixture(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def base_config(port, **overrides):
    config = config_module._merge(config_module.DEFAULTS, {
        "dtt": {"host": "127.0.0.1", "port": port, "request_timeout_seconds": 5.0},
        "timing": {
            "debounce_buffer_seconds": 1.0,
            "poll_interval_seconds": 0.05,
            "stable_read_samples": 2,
            "detect_timeout_seconds": 4.0,
            "baseline_timeout_seconds": 3.0,
            "app_launch_timeout_seconds": 3.0,
            "close_grace_seconds": 0.1,
            "settle_after_close_seconds": 0.0,
        },
        "run": {"rounds": 1, "mode": "real"},
        "preflight": {"require_power_source": "AC"},
    })
    return config_module._merge(config, overrides)


def app(process_name, expected_mode, **extra):
    entry = config_module._merge(config_module.APP_DEFAULTS, {
        "app_name": process_name.split(".")[0],
        "process_name": process_name,
        "expected_mode": expected_mode,
        "exe_path": extra.pop("exe_path", __file__),
    })
    return config_module._merge(entry, extra)


class StatusParsingTests(unittest.TestCase):
    def test_active_action_is_first_satisfied_row(self):
        for path, expected in ((WL1, "optimized_WL1"), (WL2, "optimized_WL2")):
            status = parse_status(read_fixture(path))
            self.assertEqual(status.active_action_set, expected)
            self.assertEqual(status.first_satisfied_row().action_set, expected)

    def test_prediction_by_workload_hint(self):
        status = parse_status(read_fixture(WL1))
        self.assertEqual(status.predicted_action_set(1), "optimized_WL1")
        self.assertEqual(status.predicted_action_set(2), "optimized_WL2")
        self.assertEqual(status.predicted_action_set(0), "optimized")

    def test_explain_names_the_failing_minterm(self):
        status = parse_status(read_fixture(WL1))
        self.assertIn("Workload == 2", status.explain("optimized_WL2"))

    def test_explain_reports_preemption(self):
        status = parse_status(read_fixture(WL1))
        # "optimized" is satisfied too, but sits below optimized_WL1.
        self.assertIn("preempted by 'optimized_WL1'", status.explain("optimized"))

    def test_multi_executable_cells_are_split(self):
        status = parse_status(read_fixture(WL1))
        group2 = status.workload_groups["2"]
        self.assertIn("3dmarkspeedway.exe", group2)
        self.assertIn("premiere pro.exe", group2)
        self.assertIn("cinebench.exe", group2)
        self.assertIn("outlook.exe", status.workload_groups["1"])
        self.assertEqual(len(group2), 26)

    def test_split_handles_stray_whitespace_and_newlines(self):
        self.assertEqual(
            split_application_names("a.exe;  b.exe\n c.exe;;"),
            ["a.exe", "b.exe", "c.exe"],
        )


class ConfigTests(unittest.TestCase):
    def test_generate_from_status_builds_the_app_list(self):
        status = parse_status(read_fixture(WL1))
        generated = config_module.generate_from_status(status)
        names = [entry["process_name"] for entry in generated["apps"]]
        self.assertIn("cinebench.exe", names)
        self.assertIn("chrome.exe", names)
        self.assertEqual(len(names), 34)
        self.assertEqual(generated["baseline_mode"], "optimized")
        by_name = {entry["process_name"]: entry for entry in generated["apps"]}
        self.assertEqual(by_name["cinebench.exe"]["expected_mode"], "optimized_WL2")
        self.assertEqual(by_name["chrome.exe"]["expected_mode"], "optimized_WL1")

    def test_validate_rejects_bad_timing(self):
        config = config_module._merge(config_module.DEFAULTS, {
            "timing": {"detect_timeout_seconds": 1.0, "debounce_buffer_seconds": 5.0}
        })
        with self.assertRaises(config_module.ConfigError):
            config_module.validate(config)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.simulator = DttSimulator(WL1, extra_fixtures=[WL2])
        self.server = MockDttServer(self.simulator).start()
        self.addCleanup(self.server.stop)
        self.messages = []

    def _runner(self, config, launcher=None):
        detector = Detector(config, log=self.messages.append)
        detector.open()
        self.addCleanup(detector.close)
        launcher = launcher or FakeLauncher(self.simulator)
        return Runner(config, detector, launcher, log=self.messages.append)

    def test_discovery_finds_the_policy_module(self):
        config = base_config(self.server.port)
        runner = self._runner(config)
        self.assertEqual((runner.detector.group_id, runner.detector.module_id), ("0", "0"))

    def test_preflight_learns_the_baseline_and_passes(self):
        config = base_config(self.server.port, apps=[app("cinebench.exe", "optimized_WL2")])
        runner = self._runner(config)
        problems, status = runner.preflight()
        self.assertEqual(problems, [])
        self.assertEqual(runner.baseline_mode, "optimized")
        self.assertEqual(status.power_source, "AC")

    def test_preflight_blocks_on_dc_power(self):
        self.simulator.power_source = "DC"
        config = base_config(self.server.port, apps=[app("cinebench.exe", "optimized_WL2")])
        runner = self._runner(config)
        problems, _status = runner.preflight()
        self.assertTrue(any("Power Source is DC" in p for p in problems))

    def test_preflight_rejects_unknown_action_set(self):
        config = base_config(self.server.port, apps=[app("cinebench.exe", "optimized_WL9")])
        runner = self._runner(config)
        problems, _status = runner.preflight()
        self.assertTrue(any("not in the platform's action table" in p for p in problems))

    def test_passing_run_for_both_workload_groups(self):
        config = base_config(self.server.port, apps=[
            app("cinebench.exe", "optimized_WL2"),
            app("chrome.exe", "optimized_WL1"),
        ])
        runner = self._runner(config)
        runner.preflight()
        rows = runner.run()
        self.assertEqual([row.result for row in rows], [PASS, PASS])
        self.assertEqual(rows[0].detected_mode, "optimized_WL2")
        self.assertEqual(rows[0].workload_value, "2")
        self.assertEqual(rows[0].pl1_max, "41000")
        self.assertEqual(rows[1].pl1_max, "19000")
        for row in rows:
            self.assertIsNotNone(row.switch_latency_s)
            self.assertIsNotNone(row.deassert_latency_s)

    def test_wrong_expected_mode_fails_with_the_reason(self):
        config = base_config(self.server.port,
                             apps=[app("cinebench.exe", "optimized_WL1")])
        runner = self._runner(config)
        runner.preflight()
        rows = runner.run()
        self.assertEqual(rows[0].result, FAIL)
        self.assertEqual(rows[0].detected_mode, "optimized_WL2")
        self.assertIn("Workload == 1", rows[0].reason)

    def test_hint_that_never_arrives_fails_rather_than_hangs(self):
        # An application DTT does not know about: the hint stays absent.
        self.simulator.hint_by_process.pop("cinebench.exe")
        config = base_config(self.server.port,
                             apps=[app("cinebench.exe", "optimized_WL2")])
        runner = self._runner(config)
        runner.preflight()
        rows = runner.run()
        self.assertEqual(rows[0].result, FAIL)
        self.assertIn("Workload == 2", rows[0].reason)

    def test_switch_latency_tracks_the_debounce(self):
        config = base_config(self.server.port,
                             apps=[app("cinebench.exe", "optimized_WL2")])
        runner = self._runner(config, FakeLauncher(self.simulator, debounce=0.8))
        runner.preflight()
        rows = runner.run()
        self.assertEqual(rows[0].result, PASS)
        self.assertGreaterEqual(rows[0].switch_latency_s, 0.8)
        self.assertLess(rows[0].switch_latency_s, 2.5)

    def test_slow_switch_is_noted_against_the_buffer(self):
        config = base_config(self.server.port,
                             apps=[app("cinebench.exe", "optimized_WL2")])
        runner = self._runner(config, FakeLauncher(self.simulator, debounce=1.5))
        runner.preflight()
        rows = runner.run()
        self.assertEqual(rows[0].result, PASS)
        self.assertIn("slow switch", rows[0].notes)

    def test_launch_failure_is_recorded_not_raised(self):
        config = base_config(self.server.port, apps=[
            app("cinebench.exe", "optimized_WL2"),
            app("chrome.exe", "optimized_WL1"),
        ])
        launcher = FakeLauncher(self.simulator, fail_to_launch={"cinebench.exe"})
        runner = self._runner(config, launcher)
        runner.preflight()
        rows = runner.run()
        self.assertEqual(rows[0].result, ERROR)
        self.assertIn("simulated launch failure", rows[0].reason)
        self.assertEqual(rows[1].result, PASS)

    def test_app_that_never_gains_focus_is_recorded(self):
        config = base_config(self.server.port,
                             apps=[app("cinebench.exe", "optimized_WL2")])
        launcher = FakeLauncher(self.simulator, never_foreground={"cinebench.exe"})
        runner = self._runner(config, launcher)
        runner.preflight()
        rows = runner.run()
        self.assertEqual(rows[0].result, ERROR)
        self.assertIn("never reached the foreground", rows[0].reason)

    def test_missing_executable_is_skipped_not_failed(self):
        config = base_config(self.server.port, apps=[
            app("photoshop.exe", "optimized_WL2", exe_path="C:\\nope\\photoshop.exe"),
            app("illustrator.exe", "optimized_WL2", exe_path=""),
        ])
        runner = self._runner(config)
        runner.preflight()
        rows = runner.run()
        self.assertEqual([row.result for row in rows], [SKIP, SKIP])
        self.assertIn("not installed", rows[0].reason)
        self.assertIn("no exe_path", rows[1].reason)

    def test_multiple_rounds_surface_intermittent_behaviour(self):
        config = base_config(self.server.port, run={"rounds": 3, "mode": "real"},
                             apps=[app("cinebench.exe", "optimized_WL2")])
        runner = self._runner(config)
        runner.preflight()
        rows = runner.run()
        self.assertEqual(len(rows), 3)
        self.assertEqual([row.round_number for row in rows], [1, 2, 3])

        rows[1].result = FAIL
        summary = summarize(rows)
        self.assertEqual(summary[0]["verdict"], "INTERMITTENT")

    def test_report_puts_failures_first(self):
        config = base_config(self.server.port, apps=[
            app("chrome.exe", "optimized_WL1"),
            app("cinebench.exe", "optimized_WL1"),
        ])
        runner = self._runner(config)
        runner.preflight()
        rows = runner.run()

        path = os.path.join(os.environ.get("TMPDIR", "/tmp"), "dtt_report_test.csv")
        write_csv(rows, path)
        self.addCleanup(os.remove, path)
        with open(path, encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()
        self.assertIn("cinebench", lines[1])
        self.assertIn("FAIL", lines[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        from dttwl import shortcuts
        from tests.lnk_builder import build_lnk

        self.shortcuts = shortcuts
        self.build_lnk = build_lnk
        self.folder = tempfile.mkdtemp(prefix="dtt_lnk_")
        self.addCleanup(__import__("shutil").rmtree, self.folder, True)

    def _write(self, name, data):
        path = os.path.join(self.folder, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_reads_ansi_shortcut_target(self):
        path = self._write("Cinebench.lnk",
                           self.build_lnk("C:\\Tools\\Cinebench\\Cinebench.exe"))
        self.assertEqual(self.shortcuts.resolve_shortcut(path),
                         "C:\\Tools\\Cinebench\\Cinebench.exe")

    def test_reads_unicode_shortcut_with_suffix(self):
        path = self._write("Photoshop.lnk", self.build_lnk(
            "C:\\Program Files\\Adobe\\", "Photoshop.exe", unicode_paths=True))
        self.assertEqual(self.shortcuts.resolve_shortcut(path),
                         "C:\\Program Files\\Adobe\\Photoshop.exe")

    def test_skips_the_target_id_list(self):
        path = self._write("Code.lnk", self.build_lnk(
            "C:\\Apps\\Code.exe", id_list=b"\x01\x02\x03\x04\x05\x06"))
        self.assertEqual(self.shortcuts.resolve_shortcut(path), "C:\\Apps\\Code.exe")

    def test_unreadable_shortcut_does_not_raise(self):
        path = self._write("Broken.lnk", b"not a shortcut at all")
        self.assertEqual(self.shortcuts.resolve_shortcut(path), "")

    def test_scan_and_match_against_the_whitelist(self):
        self._write("Adobe Photoshop 2024.lnk",
                    self.build_lnk("C:\\Program Files\\Adobe\\photoshop.exe"))
        self._write("Cinebench.lnk", b"corrupt")           # falls back to the stem
        self._write("geekbench 6.exe", b"MZ")              # a bare executable
        self._write("notes.txt", b"ignore me")

        entries = self.shortcuts.scan_folder(self.folder)
        self.assertEqual(len(entries), 3)

        status = parse_status(read_fixture(WL1))
        whitelist = status.workload_groups["2"]
        matched = self.shortcuts.match_to_whitelist(entries, whitelist)

        self.assertIn("photoshop.exe", matched)
        self.assertTrue(matched["photoshop.exe"]["path"].endswith(
            "Adobe Photoshop 2024.lnk"))
        self.assertIn("cinebench.exe", matched)
        self.assertIn("geekbench 6.exe", matched)
        self.assertNotIn("illustrator.exe", matched)


class SimpleReportTests(unittest.TestCase):
    @staticmethod
    def _row(process_name, result, detected="", reason=""):
        from dttwl.report import ResultRow

        return ResultRow(app_name=process_name.split(".")[0],
                         process_name=process_name, result=result,
                         detected_mode=detected, reason=reason)

    def test_layout_matches_the_requested_columns(self):
        from dttwl.report import simple_rows

        rows = [
            self._row("cinebench.exe", PASS, "optimized_WL2"),
            self._row("msedge.exe", PASS, "optimized_WL1"),
            self._row("steam.exe", FAIL, "optimized"),
        ]
        self.assertEqual(simple_rows(rows), [
            {"number": 1, "application": "cinebench.exe",
             "apat_result": "optimized_WL2", "verdict": "pass"},
            {"number": 2, "application": "msedge.exe",
             "apat_result": "optimized_WL1", "verdict": "pass"},
            {"number": 3, "application": "steam.exe",
             "apat_result": "optimized", "verdict": "fail"},
        ])

    def test_a_single_failing_round_decides_the_verdict(self):
        from dttwl.report import simple_rows

        rows = [
            self._row("cinebench.exe", PASS, "optimized_WL2"),
            self._row("cinebench.exe", FAIL, "optimized"),
            self._row("cinebench.exe", PASS, "optimized_WL2"),
        ]
        entry = simple_rows(rows)[0]
        self.assertEqual(entry["verdict"], "fail")
        self.assertEqual(entry["apat_result"], "optimized")

    def test_skipped_applications_are_not_failures(self):
        from dttwl.report import simple_rows

        rows = [self._row("photoshop.exe", SKIP, reason="not installed")]
        entry = simple_rows(rows)[0]
        self.assertEqual(entry["verdict"], "skip")
        self.assertEqual(entry["apat_result"], "not installed")

    def test_an_error_is_not_reported_as_a_skip(self):
        # "skip" means the case was never attempted - no path configured - and
        # is unremarkable in a list of thirty. An error means the tool tried
        # and got no answer. Calling both "skip" is what buried code.exe
        # failing to reach the foreground among nine benign rows.
        from dttwl.report import simple_rows

        rows = [self._row("code.exe", ERROR,
                          reason="code.exe never reached the foreground within 30s")]
        entry = simple_rows(rows)[0]
        self.assertEqual(entry["verdict"], "error")
        self.assertEqual(entry["apat_result"],
                         "code.exe never reached the foreground within 30s")

    def test_an_error_is_not_reported_as_a_failure_either(self):
        # "fail" asserts that DTT was given the foreground and did not switch.
        # If the window never came forward, APAT was never asked, so a failure
        # verdict would state an observation that was never made - and send a
        # tester to the DTT team with nothing to find.
        from dttwl.report import simple_rows

        rows = [self._row("code.exe", ERROR, reason="never reached the foreground")]
        self.assertNotEqual(simple_rows(rows)[0]["verdict"], "fail")

    def test_a_real_failure_still_outranks_an_error(self):
        from dttwl.report import simple_rows

        rows = [
            self._row("code.exe", ERROR, reason="never reached the foreground"),
            self._row("code.exe", FAIL, "optimized"),
        ]
        self.assertEqual(simple_rows(rows)[0]["verdict"], "fail")

    def test_a_passing_round_outranks_an_error(self):
        from dttwl.report import simple_rows

        rows = [
            self._row("code.exe", ERROR, reason="never reached the foreground"),
            self._row("code.exe", PASS, "optimized_WL1"),
        ]
        entry = simple_rows(rows)[0]
        self.assertEqual(entry["verdict"], "pass")
        self.assertEqual(entry["apat_result"], "optimized_WL1")


class RunSummaryTests(unittest.TestCase):
    """An application the run could not answer for must not look answered."""

    @staticmethod
    def _row(process_name, result, reason=""):
        from dttwl.report import ResultRow

        return ResultRow(app_name=process_name.split(".")[0],
                         process_name=process_name, result=result, reason=reason)

    def test_an_errored_application_is_not_not_tested(self):
        from dttwl.report import summarize

        rows = [self._row("code.exe", ERROR, "never reached the foreground")]
        line = summarize(rows)[0]
        self.assertEqual(line["verdict"], "ERROR")
        self.assertEqual(line["errored"], 1)

    def test_a_configured_but_unlaunchable_app_differs_from_an_unconfigured_one(self):
        from dttwl.report import summarize

        summary = {line["process_name"]: line["verdict"] for line in summarize([
            self._row("code.exe", ERROR, "never reached the foreground"),
            self._row("olk.exe", SKIP, "no exe_path configured"),
        ])}
        self.assertEqual(summary["code.exe"], "ERROR")
        self.assertEqual(summary["olk.exe"], "NOT TESTED")

    def test_errors_are_counted_apart_from_skips(self):
        from dttwl.report import summarize

        rows = [
            self._row("code.exe", ERROR, "never reached the foreground"),
            self._row("code.exe", SKIP, "no exe_path configured"),
        ]
        line = summarize(rows)[0]
        self.assertEqual(line["errored"], 1)
        self.assertEqual(line["other"], 2)


class StubPlanTests(unittest.TestCase):
    """Where the renamed interpreter goes, for each way the tool is shipped."""

    def test_frozen_build_copies_its_own_exe_to_a_temp_folder(self):
        from dttwl.stub import STUB_FLAG, stub_plan

        source, destination, args = stub_plan(
            executable="D:\\usb\\dtt-wl-validator.exe", frozen=True,
            app_main="", temp_dir="C:\\Temp\\stubs",
            process_name="photoshop.exe", title="Photoshop",
        )
        self.assertEqual(source, "D:\\usb\\dtt-wl-validator.exe")
        self.assertEqual(destination, os.path.join("C:\\Temp\\stubs", "photoshop.exe"))
        self.assertEqual(args, [STUB_FLAG, "--title", "Photoshop"])

    def test_portable_build_keeps_the_copy_beside_the_interpreter(self):
        # python.exe needs its DLLs and standard library alongside it, so the
        # copy cannot go to a temp folder the way a frozen exe can.
        from dttwl.stub import STUB_FLAG, stub_plan

        interpreter = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "fixtures", "python.exe")
        source, destination, args = stub_plan(
            executable=interpreter, frozen=False,
            app_main="D:\\portable\\app\\main.py", temp_dir="C:\\Temp\\stubs",
            process_name="cinebench.exe", title="Cinebench",
        )
        self.assertEqual(source, interpreter)
        self.assertEqual(os.path.dirname(destination), os.path.dirname(interpreter))
        self.assertTrue(destination.endswith("cinebench.exe"))
        self.assertEqual(args, ["D:\\portable\\app\\main.py", STUB_FLAG,
                                "--title", "Cinebench"])

    def test_portable_build_without_a_main_script_is_rejected(self):
        from dttwl.stub import stub_plan
        from dttwl.winfg import LauncherError

        interpreter = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "fixtures", "python.exe")
        with self.assertRaises(LauncherError):
            stub_plan(executable=interpreter, frozen=False, app_main="",
                      temp_dir="C:\\Temp", process_name="x.exe", title="x")


class DiagnosticsTests(unittest.TestCase):
    """The report has to name the layer that broke, not just say 'no connection'."""

    def setUp(self):
        self.simulator = DttSimulator(WL1, extra_fixtures=[WL2])
        self.server = MockDttServer(self.simulator).start()
        self.addCleanup(self.server.stop)

    def test_every_layer_passes_against_a_live_server(self):
        from dttwl import diagnose

        checks = diagnose.run(base_config(self.server.port))
        self.assertEqual([c.state for c in checks], [diagnose.OK] * 6)
        # No whitelisted application is in the foreground, so DTT is idle.
        self.assertIn("active action set: optimized", checks[-1].detail)
        self.assertIn("power: AC", checks[-1].detail)
        self.assertIn("Everything responded", diagnose.advice(checks))

    def test_a_closed_port_stops_at_the_first_layer(self):
        from dttwl import diagnose

        self.server.stop()
        checks = diagnose.run(base_config(self.server.port), timeout=1.0)
        self.assertEqual(checks[0].state, diagnose.FAILED)
        self.assertTrue(all(c.state == diagnose.SKIPPED for c in checks[1:]))
        self.assertIn("nothing is listening", diagnose.advice(checks))

    def test_a_missing_policy_module_is_named(self):
        from dttwl import diagnose

        config = base_config(self.server.port)
        config["dtt"]["policy_module"] = "No Such Policy"
        checks = diagnose.run(config)

        by_name = {c.name: c for c in checks}
        self.assertEqual(by_name["ESIF commands answered"].state, diagnose.OK)
        self.assertEqual(by_name["Policy module found"].state, diagnose.FAILED)
        self.assertIn("dtt.policy_module", diagnose.advice(checks))

    def test_report_lines_up_with_the_check_states(self):
        from dttwl import diagnose

        report = diagnose.format_report(diagnose.run(base_config(self.server.port)))
        self.assertEqual(report.count("[ ok ]"), 6)
        self.assertNotIn("[FAIL]", report)


class HandshakeVariantTests(unittest.TestCase):
    """A server that goes quiet on a handshake it dislikes must still be usable."""

    def setUp(self):
        import dttwl.esif as esif_module

        self.esif = esif_module
        esif_module._preferred_variant = None
        self.addCleanup(setattr, esif_module, "_preferred_variant", None)

    def test_a_variant_only_server_is_still_connected_to(self):
        from tests.mock_dtt import PickyServer

        # Accepts only the browser-like handshake, which is not the first tried.
        server = PickyServer(required_header=("Pragma", "no-cache")).start()
        self.addCleanup(server.stop)

        session = self.esif.EsifSession("127.0.0.1", server.port, "/echo", timeout=3.0)
        session.connect()
        self.addCleanup(session.close)
        self.assertEqual(session.variant_used["name"], "browser-like headers")

    def test_the_working_variant_is_reused(self):
        from tests.mock_dtt import PickyServer

        server = PickyServer(required_header=("Pragma", "no-cache")).start()
        self.addCleanup(server.stop)

        first = self.esif.EsifSession("127.0.0.1", server.port, "/echo", timeout=3.0)
        first.connect()
        first.close()
        attempts_after_first = len(server.requests)

        second = self.esif.EsifSession("127.0.0.1", server.port, "/echo", timeout=3.0)
        second.connect()
        second.close()
        self.assertEqual(len(server.requests), attempts_after_first + 1)

    def test_all_variants_failing_names_them(self):
        from dttwl.esif import EsifError
        from tests.mock_dtt import PickyServer

        server = PickyServer(required_header=None).start()
        self.addCleanup(server.stop)

        session = self.esif.EsifSession("127.0.0.1", server.port, "/echo", timeout=1.0)
        with self.assertRaises(EsifError) as caught:
            session.connect()
        message = str(caught.exception)
        self.assertIn("standard", message)
        self.assertIn("browser-like headers", message)
        self.assertIn("timed out", message)

    def test_diagnostics_report_the_handshake_matrix(self):
        from dttwl import diagnose
        from tests.mock_dtt import PickyServer

        server = PickyServer(required_header=None).start()
        self.addCleanup(server.stop)

        config = base_config(server.port)
        config["dtt"]["host"] = "127.0.0.1"
        self.addCleanup(setattr, self.esif, "_preferred_variant", None)
        checks = diagnose.run(config, timeout=1.0)

        by_name = {c.name: c for c in checks}
        self.assertEqual(by_name["HTTP server responds"].state, diagnose.OK)
        ws = by_name["WebSocket upgrade accepted"]
        self.assertEqual(ws.state, diagnose.FAILED)
        self.assertIn("standard: timed out", ws.detail)
        self.assertIn("no Origin header", ws.detail)
        self.assertIn("with Sec-WebSocket-Protocol", ws.detail)
        # One connection per handshake, not one per handshake per address
        # (the extra request is the plain HTTP check).
        upgrades = [r for r in server.requests if "Upgrade: websocket" in r]
        self.assertEqual(len(upgrades), len(self.esif.HANDSHAKE_VARIANTS))
        self.assertIn("sent nothing back", diagnose.advice(checks))


class AddressProbeTests(unittest.TestCase):
    """The TCP check must say which address answered, not just pass or fail."""

    def test_a_refused_port_lists_every_address_tried(self):
        from dttwl import diagnose

        config = base_config(1)          # nothing listens on port 1
        config["dtt"]["host"] = "localhost"
        checks = diagnose.run(config, timeout=1.0)

        tcp = checks[0]
        self.assertEqual(tcp.state, diagnose.FAILED)
        self.assertIn("127.0.0.1", tcp.detail)
        self.assertNotIn("connected", tcp.detail)
        advice = diagnose.advice(checks)
        self.assertIn("does not prove otherwise", advice)
        self.assertIn("Dynamic Tuning", advice)

    def test_a_reachable_port_records_the_address_that_answered(self):
        from dttwl import diagnose

        server = MockDttServer(DttSimulator(WL1), host="127.0.0.1").start()
        self.addCleanup(server.stop)

        config = base_config(server.port)
        config["dtt"]["host"] = "127.0.0.1"
        checks = diagnose.run(config, timeout=2.0)

        self.assertEqual(checks[0].state, diagnose.OK)
        self.assertIn("connected", checks[0].detail)

    def test_unresolvable_host_is_reported_as_such(self):
        from dttwl import diagnose

        config = base_config(8888)
        config["dtt"]["host"] = "no-such-host.invalid"
        checks = diagnose.run(config, timeout=1.0)

        self.assertEqual(checks[0].state, diagnose.FAILED)
        self.assertIn("does not resolve", checks[0].detail)


class ListeningReportTests(unittest.TestCase):
    """The netstat-backed report is what separates 'down' from 'refusing us'."""

    def setUp(self):
        from dttwl import diagnose

        self.diagnose = diagnose
        self.netstat = (
            "\r\n"
            "Active Connections\r\n"
            "\r\n"
            "  Proto  Local Address          Foreign Address        State           PID\r\n"
            "  TCP    127.0.0.1:8888         0.0.0.0:0              LISTENING       4321\r\n"
            "  TCP    0.0.0.0:445            0.0.0.0:0              LISTENING       4\r\n"
            "  TCP    127.0.0.1:9999         0.0.0.0:0              LISTENING       7777\r\n"
            "  TCP    127.0.0.1:50000        127.0.0.1:8888         ESTABLISHED     1234\r\n"
        )
        self.tasklist = (
            '"esif_uf.exe","4321","Services","0","20,000 K"\r\n'
            '"System","4","Services","0","100 K"\r\n'
            '"notepad.exe","7777","Console","1","5,000 K"\r\n'
        )
        self._patch(sys_platform="win32")

    def _patch(self, sys_platform):
        import dttwl.diagnose as module

        original_platform = module.sys.platform
        original_run = module._run
        module.sys.platform = sys_platform

        def fake_run(command, timeout=10):
            if command[0] == "netstat":
                return self.netstat
            if command[0] == "tasklist":
                return self.tasklist
            return ""

        module._run = fake_run
        self.addCleanup(setattr, module.sys, "platform", original_platform)
        self.addCleanup(setattr, module, "_run", original_run)

    def test_names_the_socket_and_its_owner(self):
        report = self.diagnose.listening_report(8888)
        self.assertIn("Listening on port 8888", report)
        self.assertIn("127.0.0.1:8888", report)
        self.assertIn("esif_uf.exe", report)
        # An established connection is not a listener.
        self.assertNotIn("50000", report)

    def test_finds_dtt_on_a_different_port(self):
        self.netstat = self.netstat.replace("127.0.0.1:8888", "127.0.0.1:8899")
        report = self.diagnose.listening_report(8888)
        self.assertIn("Nothing is listening on port 8888", report)
        self.assertIn("listening elsewhere", report)
        self.assertIn("8899", report)

    def test_advice_distinguishes_refused_from_absent(self):
        from dttwl.diagnose import Check, FAILED, advice

        refusing = [Check("TCP port localhost:8888 reachable", FAILED,
                          "127.0.0.1  refused\nListening on port 8888:\n"
                          "  127.0.0.1:8888  pid 4321  esif_uf.exe")]
        self.assertIn("security product", advice(refusing))

        moved = [Check("TCP port localhost:8888 reachable", FAILED,
                       "127.0.0.1 refused\nDTT appears to be listening elsewhere:\n"
                       "  127.0.0.1:8899  pid 4321  esif_uf.exe")]
        self.assertIn("Put that port number", advice(moved))

    def test_off_windows_the_report_is_empty(self):
        import dttwl.diagnose as module

        module.sys.platform = "linux"
        self.assertEqual(self.diagnose.listening_report(8888), "")


class BrowserIsolationTests(unittest.TestCase):
    """A browser must be started as its own instance, not as another window."""

    @staticmethod
    def _config(**overrides):
        return config_module._merge(config_module.DEFAULTS, overrides)

    def test_a_browser_gets_its_own_profile_directory(self):
        from dttwl.runner import browser_launch_plan

        plan = browser_launch_plan(
            self._config(),
            app("msedge.exe", "optimized_WL1",
                exe_path="C:\\Program Files\\Edge\\msedge.exe"),
            "C:\\Temp\\profiles")
        self.assertIsNotNone(plan)
        executable, args = plan
        self.assertEqual(executable, "C:\\Program Files\\Edge\\msedge.exe")
        self.assertIn("--user-data-dir=C:\\Temp\\profiles\\msedge", args)
        self.assertIn("--new-window", args)

    def test_a_browser_shortcut_is_resolved_so_arguments_can_be_passed(self):
        import shutil
        import tempfile

        from dttwl.runner import browser_launch_plan
        from tests.lnk_builder import build_lnk

        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        link = os.path.join(folder, "Microsoft Edge.lnk")
        with open(link, "wb") as handle:
            handle.write(build_lnk("C:\\Program Files\\Edge\\msedge.exe"))

        plan = browser_launch_plan(
            self._config(),
            app("msedge.exe", "optimized_WL1", exe_path=link),
            "C:\\Temp\\profiles")
        self.assertIsNotNone(plan)
        self.assertEqual(plan[0], "C:\\Program Files\\Edge\\msedge.exe")

    def test_other_applications_are_launched_normally(self):
        from dttwl.runner import browser_launch_plan

        self.assertIsNone(browser_launch_plan(
            self._config(),
            app("cinebench.exe", "optimized_WL2", exe_path="C:\\T\\cinebench.exe"),
            "C:\\Temp\\profiles"))

    def test_isolation_can_be_turned_off(self):
        from dttwl.runner import browser_launch_plan

        self.assertIsNone(browser_launch_plan(
            self._config(browser_isolation={"enabled": False}),
            app("msedge.exe", "optimized_WL1", exe_path="C:\\E\\msedge.exe"),
            "C:\\Temp\\profiles"))


class LaunchOwnershipTests(unittest.TestCase):
    """Only processes this launch started may be foregrounded or closed."""

    def _app(self, pre_existing, running):
        from dttwl.winfg import LaunchedApp

        launched = LaunchedApp("msedge.exe", pre_existing=pre_existing)
        launched.pids = list(running)
        return launched

    def test_processes_that_were_already_running_are_not_owned(self):
        launched = self._app(pre_existing={100, 200}, running=[100, 200, 300])
        self.assertEqual(launched.owned_pids(), [300])
        self.assertEqual(launched.target_pids(), [300])

    def test_joining_an_existing_instance_owns_nothing_to_close(self):
        # Edge started while Edge was already running: no new process appears,
        # so there is nothing this test may close.
        launched = self._app(pre_existing={100, 200}, running=[100, 200])
        self.assertEqual(launched.owned_pids(), [])

    def test_a_single_instance_app_is_still_foregrounded(self):
        # This assertion used to read target_pids() == [], which is precisely
        # the bug: VS Code, Outlook and Word hand the launch to the copy
        # already running and exit, so nothing is owned and no window was ever
        # looked for - the case timed out after 30s with no verdict.
        # The window to bring forward belongs to a pre-existing process, and
        # the executable name is what DTT matches on anyway.
        launched = self._app(pre_existing={100, 200}, running=[100, 200])
        self.assertEqual(sorted(launched.target_pids()), [100, 200])

    def test_owning_a_process_still_wins_over_the_pre_existing_ones(self):
        # When the launch did create its own process, that is the one to
        # foreground - not somebody's older window of the same application.
        launched = self._app(pre_existing={100, 200}, running=[100, 200, 300])
        self.assertEqual(launched.target_pids(), [300])


AC_DC = os.path.join(FIXTURES, "status_ac_dc_named.xml")


class ActionSetDerivationTests(unittest.TestCase):
    """Which action set a hint uses is read from DTT, never matched by name."""

    def test_derives_the_names_this_platform_uses(self):
        from dttwl.status import derive_expected_modes

        status = parse_status(read_fixture(WL1))
        self.assertEqual(derive_expected_modes(status),
                         {"1": "optimized_WL1", "2": "optimized_WL2"})

    def test_a_differently_named_platform_needs_no_configuration(self):
        from dttwl.status import derive_expected_modes

        status = parse_status(read_fixture(AC_DC))
        self.assertEqual(derive_expected_modes(status),
                         {"1": "AC_O_WL1", "2": "AC_O_WL2"})

    def test_separate_ac_and_dc_rows_are_told_apart_by_live_conditions(self):
        # DC_O_WL2 sits above AC_O_WL2 in arbitration order but requires DC,
        # and this capture is on AC, so the AC row is the one that applies.
        status = parse_status(read_fixture(AC_DC))
        self.assertEqual(status.workload_action_set("2", live=False), "DC_O_WL2")
        self.assertEqual(status.workload_action_set("2"), "AC_O_WL2")

    def test_an_override_replaces_the_derived_name(self):
        from dttwl.status import derive_expected_modes

        status = parse_status(read_fixture(WL1))
        mapping = derive_expected_modes(status, {"2": "SomethingElse"})
        self.assertEqual(mapping, {"1": "optimized_WL1", "2": "SomethingElse"})

    def test_hints_come_from_both_tables(self):
        status = parse_status(read_fixture(WL1))
        self.assertEqual(status.workload_hints(), ["1", "2"])

    def test_a_hint_with_no_action_set_is_left_out(self):
        from dttwl.status import derive_expected_modes

        status = parse_status(read_fixture(WL1))
        status.workload_groups["7"] = ["nothing.exe"]
        mapping = derive_expected_modes(status)
        self.assertNotIn("7", mapping)
        self.assertEqual(sorted(mapping), ["1", "2"])

    def test_generated_config_carries_the_platform_names(self):
        status = parse_status(read_fixture(AC_DC))
        generated = config_module.generate_from_status(status)
        by_name = {entry["process_name"]: entry for entry in generated["apps"]}
        self.assertEqual(by_name["chrome.exe"]["expected_mode"], "AC_O_WL1")
        self.assertEqual(by_name["cinebench.exe"]["expected_mode"], "AC_O_WL2")


class PreflightDerivationTests(unittest.TestCase):
    """A config written on one machine must adapt to the next one."""

    def setUp(self):
        self.simulator = DttSimulator(AC_DC)
        self.server = MockDttServer(self.simulator).start()
        self.addCleanup(self.server.stop)
        self.messages = []

    def _runner(self, config):
        detector = Detector(config, log=self.messages.append)
        detector.open()
        self.addCleanup(detector.close)
        return Runner(config, detector, FakeLauncher(self.simulator),
                      log=self.messages.append)

    def test_stale_expected_modes_are_replaced_by_the_platform_names(self):
        config = base_config(self.server.port, apps=[
            config_module._merge(app("cinebench.exe", "optimized_WL2"),
                                 {"workload_hint": 2}),
            config_module._merge(app("chrome.exe", "optimized_WL1"),
                                 {"workload_hint": 1}),
        ])
        runner = self._runner(config)
        problems, _status = runner.preflight()

        self.assertEqual(problems, [])
        self.assertEqual(runner.expected_by_hint,
                         {"1": "AC_O_WL1", "2": "AC_O_WL2"})
        self.assertEqual(config["apps"][0]["expected_mode"], "AC_O_WL2")
        self.assertEqual(config["apps"][1]["expected_mode"], "AC_O_WL1")

    def test_the_run_then_passes_on_the_renamed_platform(self):
        config = base_config(self.server.port, apps=[
            config_module._merge(app("cinebench.exe", "optimized_WL2"),
                                 {"workload_hint": 2}),
        ])
        runner = self._runner(config)
        runner.preflight()
        rows = runner.run()

        self.assertEqual(rows[0].result, PASS)
        self.assertEqual(rows[0].detected_mode, "AC_O_WL2")
        self.assertEqual(rows[0].expected_mode, "AC_O_WL2")

    def test_a_configured_override_is_not_overwritten(self):
        config = base_config(self.server.port, apps=[
            config_module._merge(app("cinebench.exe", ""), {"workload_hint": 2}),
        ])
        config["expected_mode_by_hint"] = {"2": "AC_O_WL2"}
        runner = self._runner(config)
        runner.preflight()
        self.assertEqual(runner.expected_by_hint["2"], "AC_O_WL2")


class TimingWarningTests(unittest.TestCase):
    """Thin margins must be called out, not left to look like real failures."""

    def setUp(self):
        self.simulator = DttSimulator(WL1)
        self.server = MockDttServer(self.simulator).start()
        self.addCleanup(self.server.stop)
        self.messages = []

    def _preflight(self, **timing):
        config = base_config(self.server.port, timing=timing,
                             apps=[app("cinebench.exe", "optimized_WL2")])
        detector = Detector(config, log=self.messages.append)
        detector.open()
        self.addCleanup(detector.close)
        Runner(config, detector, FakeLauncher(self.simulator),
               log=self.messages.append).preflight()
        return [m for m in self.messages if m.startswith("warning:")]

    def test_the_shipped_defaults_are_flagged(self):
        # debounce 5 + 2 more reads 2s apart = 9s, against a 10s timeout.
        warnings = self._preflight(debounce_buffer_seconds=5.0,
                                   poll_interval_seconds=2.0,
                                   stable_read_samples=3,
                                   detect_timeout_seconds=10.0)
        self.assertEqual(len(warnings), 1)
        self.assertIn("cannot be recorded before 9.0s", warnings[0])
        self.assertIn("only 1.0s", warnings[0])

    def test_a_roomy_configuration_is_not_flagged(self):
        warnings = self._preflight(debounce_buffer_seconds=5.0,
                                   poll_interval_seconds=0.5,
                                   stable_read_samples=3,
                                   detect_timeout_seconds=25.0)
        self.assertEqual(warnings, [])
