"""GUI tests, skipped where tkinter or a display is unavailable.

The window is driven the way a tester would: load the whitelist from DTT, scan
a folder of shortcuts, run a sweep, then export.  Everything runs against the
same simulated DTT server the other tests use.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import tkinter

    tkinter.Tk().destroy()
    GUI_AVAILABLE = True
    GUI_SKIP_REASON = ""
except Exception as exc:  # pragma: no cover - environment dependent
    GUI_AVAILABLE = False
    GUI_SKIP_REASON = "no usable tkinter display ({0})".format(exc)

from tests.lnk_builder import build_lnk
from tests.mock_dtt import DttSimulator, FakeLauncher, MockDttServer

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
WL1 = os.path.join(FIXTURES, "status_wl1.xml")
WL2 = os.path.join(FIXTURES, "status_wl2.xml")


@unittest.skipUnless(GUI_AVAILABLE, GUI_SKIP_REASON)
class GuiTests(unittest.TestCase):
    def setUp(self):
        from dttwl import gui as gui_module

        self.gui_module = gui_module
        self.simulator = DttSimulator(WL1, extra_fixtures=[WL2])
        self.server = MockDttServer(self.simulator).start()
        self.addCleanup(self.server.stop)

        self.launcher = FakeLauncher(self.simulator, debounce=0.3)
        self._real_launcher = gui_module.WindowsLauncher
        gui_module.WindowsLauncher = lambda config, **kwargs: self.launcher
        self.addCleanup(setattr, gui_module, "WindowsLauncher", self._real_launcher)

        self.dialogs = []
        gui_module.messagebox.showinfo = lambda *a, **k: self.dialogs.append(("info", a[-1]))
        gui_module.messagebox.showerror = lambda *a, **k: self.dialogs.append(("error", a[-1]))
        # Any dialog left unpatched would block the event loop forever.
        gui_module.messagebox.showwarning = lambda *a, **k: self.dialogs.append(
            ("warning", a[-1]))

        self.workdir = tempfile.mkdtemp(prefix="dtt_gui_")
        self.addCleanup(shutil.rmtree, self.workdir, True)

        self.app = gui_module.ValidatorApp(
            config_path=os.path.join(self.workdir, "config.json"))
        self.addCleanup(self._teardown_window)
        self.app.vars["host"].set("127.0.0.1")
        self.app.vars["port"].set(str(self.server.port))
        self.app.vars["rounds"].set("1")
        self.app.vars["poll"].set("0.05")
        self.app.vars["debounce"].set("1.0")
        self.app.vars["samples"].set("2")
        self.app.vars["timeout"].set("4.0")
        self.app.vars["output"].set(os.path.join(self.workdir, "reports"))

    def _teardown_window(self):
        """Close and collect the window on the main thread.

        Tk aborts the process if its objects are finalised from another
        thread, and the worker threads hold a reference to the window.
        """
        import gc

        self.app._on_close()
        self.app = None
        gc.collect()

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.app.update()
            time.sleep(0.02)

    def wait_until(self, predicate, timeout=45):
        end = time.time() + timeout
        while time.time() < end:
            self.app.update()
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def load_whitelist(self):
        self.app._reload_whitelist()
        self.assertTrue(self.wait_until(lambda: len(self.app.apps) > 0, 15),
                        "whitelist never loaded: {0}".format(self.dialogs))

    def shortcut_folder(self, targets):
        folder = tempfile.mkdtemp(dir=self.workdir)
        for label, target in targets.items():
            with open(os.path.join(folder, label), "wb") as handle:
                handle.write(build_lnk(target))
        return folder

    # -- tests -------------------------------------------------------------

    def test_whitelist_loads_from_dtt(self):
        self.load_whitelist()
        self.assertEqual(len(self.app.apps), 34)
        names = {entry["process_name"] for entry in self.app.apps}
        self.assertIn("cinebench.exe", names)
        by_name = {e["process_name"]: e for e in self.app.apps}
        self.assertEqual(by_name["cinebench.exe"]["expected_mode"], "optimized_WL2")

    def test_folder_of_shortcuts_is_matched_to_the_whitelist(self):
        self.load_whitelist()
        folder = self.shortcut_folder({
            "Cinebench.lnk": "C:\\Tools\\Cinebench\\cinebench.exe",
            "Microsoft Edge.lnk": "C:\\Program Files\\Microsoft\\Edge\\msedge.exe",
            "Some Other App.lnk": "C:\\Apps\\notwhitelisted.exe",
        })
        self.app.var_folder.set(folder)
        self.app._scan_folder()
        self.pump(0.2)

        matched = {entry["process_name"] for entry in self.app.apps if entry["exe_path"]}
        self.assertEqual(matched, {"cinebench.exe", "msedge.exe"})

        # The table must actually be on screen with a row per application;
        # widgets on an unselected notebook tab are not mapped.
        self.app.notebook.select(1)
        self.pump(0.2)
        self.assertTrue(self.app.app_table.winfo_ismapped())
        self.assertEqual(len(self.app.app_table.get_children()), len(self.app.apps))

    def test_run_shows_live_state_and_exports_the_report(self):
        self.load_whitelist()
        folder = self.shortcut_folder({
            "Cinebench.lnk": "C:\\Tools\\Cinebench\\cinebench.exe",
            "Microsoft Edge.lnk": "C:\\Program Files\\Microsoft\\Edge\\msedge.exe",
        })
        self.app.var_folder.set(folder)
        self.app._scan_folder()
        for entry in self.app.apps:
            entry["enabled"] = entry["process_name"] in ("cinebench.exe", "msedge.exe")

        self.app._start_run()
        self.assertTrue(self.wait_until(lambda: len(self.app.rows) == 2, 60),
                        "run did not finish: {0}".format(self.dialogs))

        self.assertEqual({row.result for row in self.app.rows}, {"PASS"})
        self.assertEqual(self.app.banner["text"], "ALL PASS")
        self.assertEqual(self.app.banner["bg"], self.gui_module.COLOR_PASS_BG)
        self.assertEqual(len(self.app.results.get_children()), 2)

        self.app._export()
        self.pump(0.2)
        reports = os.path.join(self.workdir, "reports")
        files = os.listdir(reports)
        summary = [f for f in files if "_report_" in f and f.endswith(".csv")]
        self.assertEqual(len(summary), 1)

        with open(os.path.join(reports, summary[0]), encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(lines[0], "#,application,APAT results,pass/fail")
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(line.endswith(",pass") for line in lines[1:]), lines)

    def test_failure_turns_the_banner_red(self):
        self.load_whitelist()
        folder = self.shortcut_folder({
            "Cinebench.lnk": "C:\\Tools\\Cinebench\\cinebench.exe"})
        self.app.var_folder.set(folder)
        self.app._scan_folder()
        for entry in self.app.apps:
            entry["enabled"] = entry["process_name"] == "cinebench.exe"
            if entry["enabled"]:
                # Without a workload hint the expected mode is not re-derived,
                # so this stays wrong and the case fails.
                entry["workload_hint"] = None
                entry["expected_mode"] = "optimized_WL1"

        self.app._start_run()
        self.assertTrue(self.wait_until(lambda: len(self.app.rows) == 1, 60),
                        "run did not finish: {0}".format(self.dialogs))

        self.assertEqual(self.app.rows[0].result, "FAIL")
        self.assertEqual(self.app.banner["bg"], self.gui_module.COLOR_FAIL_BG)
        values = self.app.results.item(self.app.results.get_children()[0], "values")
        self.assertEqual(values[3], "fail")
        self.assertIn("Workload == 1", values[5])

    def test_the_strip_goes_neutral_when_the_run_ends(self):
        # The strip names the application tested last, so leaving it red would
        # read as a verdict on that application rather than on the run.
        self.load_whitelist()
        folder = self.shortcut_folder({
            "Cinebench.lnk": "C:\\Tools\\Cinebench\\cinebench.exe"})
        self.app.var_folder.set(folder)
        self.app._scan_folder()
        for entry in self.app.apps:
            entry["enabled"] = entry["process_name"] == "cinebench.exe"
            if entry["enabled"]:
                entry["workload_hint"] = None
                entry["expected_mode"] = "optimized_WL1"    # forces a failure

        self.app._start_run()
        self.assertTrue(self.wait_until(lambda: len(self.app.rows) == 1, 60),
                        "run did not finish: {0}".format(self.dialogs))

        self.assertEqual(self.app.banner["bg"], self.gui_module.COLOR_FAIL_BG)
        self.assertEqual(self.app.live_frame["bg"], self.gui_module.COLOR_RUN_BG)
        self.assertEqual(self.app.lbl_app["bg"], self.gui_module.COLOR_RUN_BG)
        self.assertEqual(self.app.lbl_app["text"], "Test complete")
        self.assertNotIn("cinebench", self.app.lbl_app["text"])
        self.assertIn("1 failed", self.app.lbl_mode["text"])

    def test_stop_ends_the_run_and_keeps_partial_results(self):
        self.load_whitelist()
        folder = self.shortcut_folder({
            "Cinebench.lnk": "C:\\Tools\\Cinebench\\cinebench.exe",
            "Microsoft Edge.lnk": "C:\\Program Files\\Microsoft\\Edge\\msedge.exe",
        })
        self.app.var_folder.set(folder)
        self.app._scan_folder()
        for entry in self.app.apps:
            entry["enabled"] = entry["process_name"] in ("cinebench.exe", "msedge.exe")
        self.app.vars["rounds"].set("5")

        self.app._start_run()
        self.assertTrue(self.wait_until(
            lambda: len(self.app.results.get_children()) >= 1, 60))
        self.app._stop_run()

        # ttk reports options as Tcl objects, so compare the string form.
        self.assertTrue(self.wait_until(
            lambda: str(self.app.btn_start["state"]) == "normal", 30),
            "run did not stop")
        self.assertLess(len(self.app.rows), 10)


    def test_the_hint_mapping_is_shown_and_applied(self):
        self.load_whitelist()
        self.pump(0.2)

        self.assertEqual(self.app.derived_modes,
                         {"1": "optimized_WL1", "2": "optimized_WL2"})
        self.assertEqual(sorted(self.app.hint_vars), ["1", "2"])
        by_name = {entry["process_name"]: entry for entry in self.app.apps}
        self.assertEqual(by_name["cinebench.exe"]["expected_mode"],
                         "optimized_WL2")

    def test_editing_a_name_becomes_an_override_and_reaches_the_config(self):
        self.load_whitelist()
        self.pump(0.2)

        self.app.hint_vars["2"].set("AC_O_WL2")
        self.assertEqual(self.app._hint_overrides(), {"2": "AC_O_WL2"})
        # An unchanged name stays derived rather than being frozen in.
        self.assertNotIn("1", self.app._hint_overrides())
        self.assertEqual(self.app._current_config()["expected_mode_by_hint"],
                         {"2": "AC_O_WL2"})

    def test_typing_a_path_applies_it_to_the_selected_row(self):
        self.load_whitelist()
        index = next(i for i, entry in enumerate(self.app.apps)
                     if entry["process_name"] == "photoshop.exe")
        self.app.app_table.selection_set(str(index))
        self.pump(0.1)

        self.app.var_manual_path.set('"D:\\Apps\\Adobe\\photoshop.exe"')
        self.app._apply_manual_path()

        self.assertEqual(self.app.apps[index]["exe_path"],
                         "D:\\Apps\\Adobe\\photoshop.exe")
        values = self.app.app_table.item(str(index), "values")
        self.assertEqual(values[3], "D:\\Apps\\Adobe\\photoshop.exe")

    def test_selecting_a_row_loads_its_current_path(self):
        self.load_whitelist()
        index = next(i for i, entry in enumerate(self.app.apps)
                     if entry["process_name"] == "cinebench.exe")
        self.app.apps[index]["exe_path"] = "C:\\Tools\\cinebench.exe"
        self.app._refresh_app_table()

        self.app.app_table.selection_set(str(index))
        self.app._on_app_selected()
        self.assertEqual(self.app.var_manual_path.get(), "C:\\Tools\\cinebench.exe")


if __name__ == "__main__":
    unittest.main(verbosity=2)
