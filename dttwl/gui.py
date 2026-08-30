# -*- coding: utf-8 -*-
"""Desktop UI for the validator.

A native window rather than a web page for two reasons: a browser cannot
launch local executables at all, and msedge.exe / chrome.exe are themselves on
the workload-hint list, so a browser-based UI would change the very state being
measured.  This window belongs to a process that is not whitelisted, which also
makes it the neutral baseline the runner returns to between test cases.

It stays on top by default so the live status remains readable while the
application under test holds the foreground.
"""

import os
import queue
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import appicon
from . import config as config_module
from . import diagnose as diagnose_module
from . import paths as paths_module
from . import report as report_module
from . import shortcuts as shortcuts_module
from .esif import EsifError
from .runner import Detector, PreflightError, RunObserver, Runner, WindowsLauncher
from .status import StatusParseError, derive_expected_modes
from .version import FULL_TITLE as TITLE

COLOR_PASS_BG = "#1f9d55"
COLOR_FAIL_BG = "#c0392b"
COLOR_IDLE_BG = "#4a5568"
# Dell blue, used wherever the strip is stating a fact rather than a verdict.
COLOR_RUN_BG = "#0076CE"
COLOR_PASS_ROW = "#d7f2e0"
COLOR_FAIL_ROW = "#ffd9d9"
COLOR_SKIP_ROW = "#ededed"
# Amber, not red: an error is "the tool could not get an answer", which is a
# different claim from "DTT did not switch".
COLOR_ERROR_BG = "#b7791f"
COLOR_ERROR_ROW = "#ffeccc"


CONNECTION_HINT = (
    "\n\nGo to the Settings tab and press \"Test connection\" for a "
    "step-by-step report of which layer failed."
)


def _with_hint(exc):
    message = str(exc)
    if isinstance(exc, EsifError):
        return message + CONNECTION_HINT
    return message


class GuiObserver(RunObserver):
    """Pushes runner progress onto a queue the Tk thread drains."""

    def __init__(self, events):
        self.events = events

    def phase(self, text):
        self.events.put(("phase", text))

    def case_started(self, app, round_number, index, total):
        self.events.put(("case_started", (app, round_number, index, total)))

    def sample(self, app, status, elapsed):
        self.events.put(("sample", (app, status, elapsed)))

    def case_finished(self, row):
        self.events.put(("case_finished", row))


class ValidatorApp(tk.Tk):
    def __init__(self, config_path="config.json"):
        # Has to happen before the window exists, or the taskbar button is
        # already filed under the host interpreter and keeps Python's icon.
        appicon.set_app_user_model_id()

        super().__init__()
        self.title(TITLE)
        self.geometry("1000x680")
        self.minsize(880, 600)
        appicon.apply_window_icon(self)

        self.config_path = config_path
        self.settings = self._load_settings()
        self.apps = list(self.settings.get("apps", []))
        self.derived_modes = {}
        self.hint_vars = {}

        self.events = queue.Queue()
        self.worker = None
        self.cancel_event = threading.Event()
        self.rows = []

        self._build_widgets()
        self._refresh_app_table()
        self._pump_id = self.after(100, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- setup -------------------------------------------------------------

    def _load_settings(self):
        if os.path.isfile(self.config_path):
            try:
                return config_module.load(self.config_path)
            except config_module.ConfigError:
                pass
        return config_module._merge(config_module.DEFAULTS, {})

    def _build_widgets(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=24)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        self.notebook = notebook

        self.tab_test = ttk.Frame(notebook)
        self.tab_apps = ttk.Frame(notebook)
        self.tab_settings = ttk.Frame(notebook)
        notebook.add(self.tab_test, text="  Test  ")
        notebook.add(self.tab_apps, text="  Application Path  ")
        notebook.add(self.tab_settings, text="  Settings  ")

        self._build_test_tab()
        self._build_apps_tab()
        self._build_settings_tab()

    # -- test tab ----------------------------------------------------------

    def _build_test_tab(self):
        frame = self.tab_test

        live = tk.Frame(frame, bg=COLOR_IDLE_BG)
        live.pack(fill="x", padx=4, pady=(4, 10))

        self.lbl_app = tk.Label(live, text="—", bg=COLOR_IDLE_BG, fg="white",
                                font=("Segoe UI", 26, "bold"), anchor="w")
        self.lbl_app.pack(fill="x", padx=18, pady=(14, 0))

        self.lbl_mode = tk.Label(live, text="DTT action set: —", bg=COLOR_IDLE_BG,
                                 fg="#e6ecf3", font=("Segoe UI", 15), anchor="w")
        self.lbl_mode.pack(fill="x", padx=18)

        self.lbl_phase = tk.Label(live, text="Not started", bg=COLOR_IDLE_BG, fg="#c3ced9",
                                  font=("Segoe UI", 10), anchor="w")
        self.lbl_phase.pack(fill="x", padx=18, pady=(2, 14))

        self.banner = tk.Label(frame, text="IDLE", bg=COLOR_IDLE_BG, fg="white",
                               font=("Segoe UI", 30, "bold"), height=2)
        self.banner.pack(fill="x", padx=4)
        self.live_frame = live

        controls = ttk.Frame(frame)
        controls.pack(fill="x", padx=4, pady=10)

        self.btn_start = ttk.Button(controls, text="Start test", command=self._start_run)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(controls, text="Stop", command=self._stop_run,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        self.btn_export = ttk.Button(controls, text="Export report", command=self._export,
                                     state="disabled")
        self.btn_export.pack(side="left", padx=6)

        self.var_topmost = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Always on top", variable=self.var_topmost,
                        command=self._apply_topmost).pack(side="right")
        self._apply_topmost()

        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.pack(fill="x", padx=4)
        self.lbl_progress = ttk.Label(frame, text="")
        self.lbl_progress.pack(anchor="w", padx=4, pady=(2, 8))

        columns = ("number", "application", "apat", "verdict", "latency", "detail")
        self.results = ttk.Treeview(frame, columns=columns, show="headings", height=10)
        for key, title, width, anchor in (
            ("number", "#", 46, "center"),
            ("application", "application", 190, "w"),
            ("apat", "APAT results", 170, "w"),
            ("verdict", "pass/fail", 90, "center"),
            ("latency", "switch (s)", 90, "center"),
            ("detail", "Detail", 340, "w"),
        ):
            self.results.heading(key, text=title)
            self.results.column(key, width=width, anchor=anchor)
        self.results.tag_configure("pass", background=COLOR_PASS_ROW)
        self.results.tag_configure("fail", background=COLOR_FAIL_ROW)
        self.results.tag_configure("skip", background=COLOR_SKIP_ROW)
        self.results.tag_configure("error", background=COLOR_ERROR_ROW)

        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.results.yview)
        self.results.configure(yscrollcommand=scroll.set)
        self.results.pack(side="left", fill="both", expand=True, padx=(4, 0),
                          pady=(0, 4))
        scroll.pack(side="left", fill="y", pady=(0, 4))

    def _apply_topmost(self):
        self.attributes("-topmost", bool(self.var_topmost.get()))

    # -- application path tab ---------------------------------------------

    def _build_apps_tab(self):
        frame = self.tab_apps

        picker = ttk.LabelFrame(frame, text=" Shortcut folder ")
        picker.pack(fill="x", padx=6, pady=8)

        row = ttk.Frame(picker)
        row.pack(fill="x", padx=8, pady=8)
        self.var_folder = tk.StringVar(value=self.settings.get("shortcut_folder", ""))
        ttk.Entry(row, textvariable=self.var_folder).pack(
            side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=self._browse_folder).pack(
            side="left", padx=(6, 0))
        ttk.Button(row, text="Scan and match", command=self._scan_folder).pack(
            side="left", padx=6)

        ttk.Label(
            picker,
            text="Put a shortcut (.lnk) or executable for every application under "
                 "test into one folder, then press Scan and match.\n"
                 "Each shortcut's real target is read and matched against the "
                 "whitelist DTT reports, so\n"
                 "\"Adobe Photoshop 2024.lnk\" lines up with photoshop.exe.",
            justify="left", foreground="#55637a",
        ).pack(anchor="w", padx=8, pady=(0, 8))

        actions = ttk.Frame(frame)
        actions.pack(fill="x", padx=6)
        ttk.Button(actions, text="Reload whitelist from DTT",
                   command=self._reload_whitelist).pack(side="left")
        ttk.Button(actions, text="Pick path...",
                   command=self._set_path_manually).pack(side="left", padx=6)
        ttk.Button(actions, text="Enable / disable",
                   command=self._toggle_enabled).pack(side="left")
        ttk.Button(actions, text="Save settings",
                   command=self._save_settings).pack(side="right")

        table_area = ttk.Frame(frame)
        table_area.pack(fill="both", expand=True)

        columns = ("enabled", "application", "expected", "path")
        self.app_table = ttk.Treeview(table_area, columns=columns, show="headings")
        for key, title, width, anchor in (
            ("enabled", "Test", 60, "center"),
            ("application", "application", 190, "w"),
            ("expected", "Expected mode", 160, "w"),
            ("path", "Path / status", 520, "w"),
        ):
            self.app_table.heading(key, text=title)
            self.app_table.column(key, width=width, anchor=anchor)
        self.app_table.tag_configure("missing", foreground="#a41d1d")
        self.app_table.tag_configure("disabled", foreground="#9aa4b0")
        self.app_table.bind("<Double-1>", lambda _e: self._set_path_manually())

        scroll = ttk.Scrollbar(table_area, orient="vertical",
                               command=self.app_table.yview)
        self.app_table.configure(yscrollcommand=scroll.set)
        self.app_table.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=8)
        scroll.pack(side="left", fill="y", pady=8)

        manual = ttk.LabelFrame(frame, text=" Type a path for the selected row ")
        manual.pack(fill="x", padx=6, pady=(0, 8))
        entry_row = ttk.Frame(manual)
        entry_row.pack(fill="x", padx=8, pady=8)
        self.var_manual_path = tk.StringVar()
        entry = ttk.Entry(entry_row, textvariable=self.var_manual_path)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self._apply_manual_path())
        ttk.Button(entry_row, text="Apply to selection",
                   command=self._apply_manual_path).pack(side="left", padx=(6, 0))
        self.app_table.bind("<<TreeviewSelect>>", self._on_app_selected)

    # -- settings tab ------------------------------------------------------

    def _build_settings_tab(self):
        frame = self.tab_settings
        dtt = self.settings["dtt"]
        timing = self.settings["timing"]
        run = self.settings["run"]

        self.vars = {
            "host": tk.StringVar(value=str(dtt["host"])),
            "port": tk.StringVar(value=str(dtt["port"])),
            "rounds": tk.StringVar(value=str(run["rounds"])),
            "mode": tk.StringVar(value=str(run["mode"])),
            "debounce": tk.StringVar(value=str(timing["debounce_buffer_seconds"])),
            "poll": tk.StringVar(value=str(timing["poll_interval_seconds"])),
            "samples": tk.StringVar(value=str(timing["stable_read_samples"])),
            "timeout": tk.StringVar(value=str(timing["detect_timeout_seconds"])),
            "output": tk.StringVar(value=str(self.settings.get("report", {}).get(
                "output_dir", paths_module.default_report_dir()))),
        }

        grid = ttk.LabelFrame(frame, text=" DTT connection ")
        grid.pack(fill="x", padx=6, pady=8)
        self._field(grid, 0, "Host", self.vars["host"])
        self._field(grid, 1, "Port", self.vars["port"])
        ttk.Button(grid, text="Test connection", command=self._test_connection).grid(
            row=2, column=1, sticky="w", padx=8, pady=(0, 8))

        grid = ttk.LabelFrame(frame, text=" Test parameters ")
        grid.pack(fill="x", padx=6, pady=8)
        self._field(grid, 0, "Rounds per application", self.vars["rounds"])
        self._field(grid, 1, "APAT debounce buffer (s)", self.vars["debounce"])
        self._field(grid, 2, "Poll interval (s)", self.vars["poll"])
        self._field(grid, 3, "Stable reads required", self.vars["samples"])
        self._field(grid, 4, "Detect timeout (s)", self.vars["timeout"])

        ttk.Label(grid, text="Launch mode").grid(row=5, column=0, sticky="w", padx=8, pady=4)
        ttk.Combobox(grid, textvariable=self.vars["mode"], state="readonly",
                     values=["real", "stub"], width=18).grid(
            row=5, column=1, sticky="w", padx=8, pady=4)
        ttk.Label(grid, text="real = launch the installed application; "
                             "stub = launch a renamed empty window to check "
                             "the whitelist entry",
                  foreground="#55637a").grid(row=6, column=0, columnspan=2,
                                             sticky="w", padx=8, pady=(0, 8))

        self.hint_frame = ttk.LabelFrame(frame, text=" Workload hint mapping ")
        self.hint_frame.pack(fill="x", padx=6, pady=8)
        self.hint_note = ttk.Label(
            self.hint_frame, justify="left", foreground="#55637a",
            text="Read from DTT: an action set carrying a \"Workload == N\" "
                 "condition is the one for hint N,\nwhatever it is named. Press "
                 "Test connection or Reload whitelist to fill this in.\n"
                 "Edit a name to override it; clear it to go back to the "
                 "derived one.")
        self.hint_note.grid(row=0, column=0, columnspan=2, sticky="w",
                            padx=8, pady=(6, 4))

        grid = ttk.LabelFrame(frame, text=" Report ")
        grid.pack(fill="x", padx=6, pady=8)
        
        row = ttk.Frame(grid)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="Output folder").pack(side="left")
        ttk.Entry(row, textvariable=self.vars["output"], width=44).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Browse...", command=self._browse_output_folder).pack(
            side="left", padx=(6, 0))

        ttk.Button(frame, text="Save settings", command=self._save_settings).pack(
            anchor="e", padx=6, pady=8)

    def _render_hint_mapping(self, derived, overrides=None):
        """One row per workload hint: the derived name, editable to override."""
        overrides = overrides or {}
        for child in self.hint_frame.winfo_children():
            if child is not self.hint_note:
                child.destroy()
        self.hint_vars = {}
        self.derived_modes = dict(derived)

        if not derived:
            return
        for index, hint in enumerate(sorted(derived), start=1):
            ttk.Label(self.hint_frame,
                      text="Workload hint {0}".format(hint)).grid(
                row=index, column=0, sticky="w", padx=8, pady=3)
            variable = tk.StringVar(value=overrides.get(hint, derived[hint]))
            self.hint_vars[hint] = variable
            ttk.Entry(self.hint_frame, textvariable=variable, width=34).grid(
                row=index, column=1, sticky="w", padx=8, pady=3)

    def _hint_overrides(self):
        """Only names the user actually changed count as overrides."""
        overrides = {}
        for hint, variable in self.hint_vars.items():
            value = variable.get().strip()
            if value and value != self.derived_modes.get(hint):
                overrides[hint] = value
        return overrides

    def _adopt_mapping(self, status):
        """Take the platform's mapping and point the applications at it."""
        derived = derive_expected_modes(status, self._hint_overrides() or
                                        self.settings.get("expected_mode_by_hint"))
        self._render_hint_mapping(derived, self._hint_overrides())
        for app in self.apps:
            hint = app.get("workload_hint")
            if hint is not None and str(hint) in derived:
                app["expected_mode"] = derived[str(hint)]
        self._refresh_app_table()
        return derived

    @staticmethod
    def _field(parent, row, label, variable):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(parent, textvariable=variable, width=44).grid(
            row=row, column=1, sticky="w", padx=8, pady=4)

    # -- config assembly ---------------------------------------------------

    def _current_config(self):
        config = config_module._merge(self.settings, {
            "dtt": {"host": self.vars["host"].get().strip() or "localhost",
                    "port": int(self.vars["port"].get() or 8888)},
            "timing": {
                "debounce_buffer_seconds": float(self.vars["debounce"].get()),
                "poll_interval_seconds": float(self.vars["poll"].get()),
                "stable_read_samples": int(self.vars["samples"].get()),
                "detect_timeout_seconds": float(self.vars["timeout"].get()),
            },
            "run": {"rounds": int(self.vars["rounds"].get()),
                    "mode": self.vars["mode"].get()},
            "report": {"output_dir": self.vars["output"].get().strip()
                       or paths_module.default_report_dir()},
            "expected_mode_by_hint": self._hint_overrides(),
            "shortcut_folder": self.var_folder.get().strip(),
            "apps": self.apps,
        })
        config_module.validate(config)
        return config

    def _config_or_error(self):
        """Read the settings widgets, reporting a bad value instead of raising."""
        try:
            return self._current_config()
        except (ValueError, config_module.ConfigError) as exc:
            messagebox.showerror(TITLE, "Invalid settings: {0}".format(exc))
            return None

    def _save_settings(self):
        config = self._config_or_error()
        if config is None:
            return
        config_module.save(config, self.config_path)
        self.settings = config
        messagebox.showinfo(TITLE, "Saved to {0}".format(
            os.path.abspath(self.config_path)))

    # -- application list --------------------------------------------------

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Choose the folder holding the shortcuts")
        if folder:
            self.var_folder.set(folder)

    def _browse_output_folder(self):
        folder = filedialog.askdirectory(title="Choose the report output folder")
        if folder:
            self.vars["output"].set(folder)

    def _reload_whitelist(self):
        # Tk variables may only be read from the thread running the event loop,
        # so the config is assembled here and handed to the worker.
        config = self._config_or_error()
        if config is None:
            return

        def work():
            with Detector(config) as detector:
                status = detector.read()
            generated = config_module.generate_from_status(status, config)
            self.events.put(("whitelist", (generated, status)))

        self._run_in_background(work, "Could not read the whitelist from DTT")

    def _scan_folder(self):
        folder = self.var_folder.get().strip()
        if not os.path.isdir(folder):
            messagebox.showerror(TITLE, "No such folder: {0}".format(folder))
            return
        if not self.apps:
            messagebox.showinfo(TITLE, "Press \"Reload whitelist from DTT\" first to get "
                                   "the list of applications.")
            return

        entries = shortcuts_module.scan_folder(folder)
        matched = shortcuts_module.match_to_whitelist(
            entries, [app["process_name"] for app in self.apps])

        for app in self.apps:
            entry = matched.get(app["process_name"])
            if entry is not None:
                app["exe_path"] = entry["path"]
                app["notes"] = "from {0}".format(entry["source"])

        unmatched = [entry for entry in entries
                     if entry["process_name"] not in matched]
        self._refresh_app_table()
        messagebox.showinfo(TITLE, (
            "Scanned {0} item(s), matched {1}.\n"
            "{2} item(s) in the folder are not on the DTT whitelist and were "
            "ignored."
        ).format(len(entries), len(matched), len(unmatched)))

    def _selected_app(self):
        selection = self.app_table.selection()
        if not selection:
            return None
        index = int(selection[0])
        return self.apps[index]

    def _set_path_manually(self):
        app = self._selected_app()
        if app is None:
            messagebox.showinfo(TITLE, "Select a row in the list first.")
            return
        path = filedialog.askopenfilename(
            title="Choose the executable or shortcut for {0}".format(
                app["process_name"]),
            filetypes=[("Program or shortcut", "*.exe;*.lnk"),
                       ("All files", "*.*")],
        )
        if path:
            app["exe_path"] = path
            app["notes"] = "picked manually"
            self._refresh_app_table()

    def _on_app_selected(self, _event=None):
        app = self._selected_app()
        if app is not None:
            self.var_manual_path.set(app.get("exe_path") or app.get("shell_target") or "")

    def _apply_manual_path(self):
        app = self._selected_app()
        if app is None:
            messagebox.showinfo(TITLE, "Select a row in the list first.")
            return
        path = self.var_manual_path.get().strip().strip('"')
        app["exe_path"] = path
        app["notes"] = "typed manually" if path else ""
        self._refresh_app_table()

    def _toggle_enabled(self):
        app = self._selected_app()
        if app is None:
            return
        app["enabled"] = not app.get("enabled", True)
        self._refresh_app_table()

    def _refresh_app_table(self):
        selection = self.app_table.selection()
        self.app_table.delete(*self.app_table.get_children())
        for index, app in enumerate(self.apps):
            path = app.get("exe_path") or app.get("shell_target") or ""
            if path:
                detail, tag = path, ""
            else:
                detail, tag = "no path found - will be reported as SKIP", "missing"
            if not app.get("enabled", True):
                tag = "disabled"
            self.app_table.insert(
                "", "end", iid=str(index),
                values=("✓" if app.get("enabled", True) else "",
                        app["process_name"], app.get("expected_mode", ""), detail),
                tags=(tag,) if tag else (),
            )
        for item in selection:
            if self.app_table.exists(item):
                self.app_table.selection_set(item)

    # -- running -----------------------------------------------------------

    def _test_connection(self):
        config = self._config_or_error()
        if config is None:
            return

        def work():
            # Layered checks rather than one connect attempt: when the DTT page
            # opens in a browser but the tool cannot reach it, what matters is
            # which layer broke.
            checks = diagnose_module.run(config)
            self.events.put(("diagnostics", checks))
            if all(check.state != diagnose_module.FAILED for check in checks):
                with Detector(config) as detector:
                    self.events.put(("mapping", detector.read()))

        self._run_in_background(work, "Connection failed")

    def _run_in_background(self, work, error_title):
        def wrapper():
            try:
                work()
            except (EsifError, StatusParseError, config_module.ConfigError,
                    ValueError, PreflightError) as exc:
                self.events.put(("error", (error_title, _with_hint(exc))))
            except Exception:  # pragma: no cover - unexpected, still surfaced
                self.events.put(("error", (error_title, traceback.format_exc())))

        threading.Thread(target=wrapper, daemon=True).start()

    def _start_run(self):
        if self.worker is not None and self.worker.is_alive():
            return
        config = self._config_or_error()
        if config is None:
            return
        if not [app for app in self.apps if app.get("enabled", True)]:
            messagebox.showinfo(TITLE, "No applications are enabled. Set them up on the "
                                   "Application Path tab first.")
            return

        self.rows = []
        self.results.delete(*self.results.get_children())
        self.cancel_event = threading.Event()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_export.configure(state="disabled")
        self.progress.configure(value=0, maximum=100)

        observer = GuiObserver(self.events)
        hwnd = self._own_hwnd()

        def work():
            try:
                with Detector(config, log=lambda m: self.events.put(("log", m))) as det:
                    launcher = WindowsLauncher(config, baseline_hwnd=hwnd)
                    runner = Runner(config, det, launcher,
                                    log=lambda m: self.events.put(("log", m)),
                                    observer=observer,
                                    cancel_event=self.cancel_event)
                    problems, status = runner.preflight()
                    if problems and config["preflight"].get("abort_on_failure", True):
                        self.events.put(("error", ("Cannot start the test",
                                                   "\n".join(problems))))
                        return
                    if config["run"]["mode"] == "stub":
                        runner.verify_stub_assumption(status)
                    rows = runner.run()
                    self.events.put(("finished", rows))
            finally:
                self.events.put(("worker_done", None))

        self.worker = threading.Thread(target=lambda: self._guarded(work), daemon=True)
        self.worker.start()

    def _guarded(self, work):
        try:
            work()
        except (EsifError, StatusParseError, PreflightError) as exc:
            self.events.put(("error", ("Test aborted", _with_hint(exc))))
        except Exception:  # pragma: no cover - unexpected, still surfaced
            self.events.put(("error", ("Test aborted", traceback.format_exc())))

    def _own_hwnd(self):
        try:
            import ctypes

            handle = self.winfo_id()
            parent = ctypes.windll.user32.GetParent(handle)
            return parent or handle
        except Exception:
            return None

    def _stop_run(self):
        self.cancel_event.set()
        self.btn_stop.configure(state="disabled")
        self.lbl_phase.configure(text="Stopping...")

    # -- event pump --------------------------------------------------------

    def _drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        self._pump_id = self.after(100, self._drain_events)

    def _handle_event(self, kind, payload):
        if kind == "case_started":
            app, round_number, index, total = payload
            self._set_live(app["process_name"], "—", None)
            self.progress.configure(maximum=total, value=index - 1)
            self.lbl_progress.configure(text="Round {0} - {1} / {2}".format(
                round_number, index, total))

        elif kind == "sample":
            app, status, elapsed = payload
            if status is not None:
                expected = app.get("expected_mode")
                matched = status.active_action_set == expected
                self._set_live(app["process_name"], status.active_action_set,
                               matched, "+{0:.1f}s  workload={1}".format(
                                   elapsed, status.workload_value))

        elif kind == "phase":
            self.lbl_phase.configure(text=payload)

        elif kind == "log":
            # Warnings go to the status line, not a dialog: this one fires on
            # every run whose timings are tight, and a modal each time would
            # train the tester to dismiss it without reading.
            if str(payload).startswith("warning:"):
                self.lbl_phase.configure(text=payload)

        elif kind == "case_finished":
            self._append_result(payload)

        elif kind == "finished":
            self.rows = payload
            self._show_final(payload)

        elif kind == "worker_done":
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            if self.rows:
                self.btn_export.configure(state="normal")

        elif kind == "whitelist":
            generated, status = payload
            self.apps = generated["apps"]
            self.settings = generated
            derived = self._adopt_mapping(status)
            messagebox.showinfo(TITLE, (
                "Read {0} whitelisted application(s) from DTT.\n\n"
                "Workload hint mapping:\n{1}"
            ).format(len(self.apps), "\n".join(
                "  hint {0} -> {1}".format(hint, name)
                for hint, name in sorted(derived.items())) or "  (none found)"))

        elif kind == "mapping":
            derived = self._adopt_mapping(payload)
            if derived:
                self.lbl_phase.configure(text="workload hint mapping: " + ", ".join(
                    "{0} -> {1}".format(hint, name)
                    for hint, name in sorted(derived.items())))

        elif kind == "diagnostics":
            report = diagnose_module.format_report(payload)
            advice = diagnose_module.advice(payload)
            failed = any(c.state == diagnose_module.FAILED for c in payload)
            body = "{0}\n\n{1}".format(report, advice)
            if failed:
                messagebox.showerror(TITLE, body)
            else:
                messagebox.showinfo(TITLE, body)

        elif kind == "error":
            title, message = payload
            self.banner.configure(text="ERROR", bg=COLOR_FAIL_BG)
            messagebox.showerror(TITLE, "{0}\n\n{1}".format(title, message))

    def _set_live(self, app_name, action_set, matched, phase=None):
        self.lbl_app.configure(text=app_name)
        self.lbl_mode.configure(text="DTT action set: {0}".format(action_set))
        if phase:
            self.lbl_phase.configure(text=phase)

        if matched is True:
            colour, text = COLOR_PASS_BG, "PASS"
        elif matched is False:
            colour, text = COLOR_RUN_BG, "TESTING..."
        else:
            colour, text = COLOR_RUN_BG, "TESTING..."
        self.banner.configure(text=text, bg=colour)
        self.live_frame.configure(bg=colour)
        for widget in (self.lbl_app, self.lbl_mode, self.lbl_phase):
            widget.configure(bg=colour)

    def _append_result(self, row):
        verdict = {"PASS": "pass", "FAIL": "fail",
                   "ERROR": "error"}.get(row.result, "skip")
        tag = verdict
        number = len(self.results.get_children()) + 1
        self.results.insert("", "end", values=(
            number, row.process_name, row.detected_mode or "-", verdict,
            "" if row.switch_latency_s is None else "{0:.2f}".format(row.switch_latency_s),
            row.reason or row.notes or "",
        ), tags=(tag,))
        self.results.see(self.results.get_children()[-1])

        colour = {"pass": COLOR_PASS_BG, "fail": COLOR_FAIL_BG,
                  "error": COLOR_ERROR_BG}.get(verdict, COLOR_IDLE_BG)
        self.banner.configure(text=verdict.upper(), bg=colour)
        self.live_frame.configure(bg=colour)
        for widget in (self.lbl_app, self.lbl_mode, self.lbl_phase):
            widget.configure(bg=colour)

    def _show_final(self, rows):
        summary = report_module.summarize(rows)
        failing = [line for line in summary
                   if line["verdict"] in ("FAIL", "INTERMITTENT")]
        errored = [line for line in summary if line["verdict"] == "ERROR"]
        tested = [line for line in summary if line["verdict"] != "NOT TESTED"]
        self.progress.configure(value=self.progress["maximum"])

        # A failure outranks an error, but an error must never be swallowed by
        # a green banner: the run did not answer the question for those
        # applications, and saying "ALL PASS" would claim that it did.
        if failing:
            self.banner.configure(text="{0} FAILED".format(len(failing)),
                                  bg=COLOR_FAIL_BG)
            self.lbl_phase.configure(text="failed: " + ", ".join(
                line["process_name"] for line in failing))
        elif errored:
            self.banner.configure(
                text="{0} NOT ANSWERED".format(len(errored)), bg=COLOR_ERROR_BG)
            self.lbl_phase.configure(
                text="no verdict for: " + ", ".join(
                    line["process_name"] for line in errored))
        else:
            self.banner.configure(text="ALL PASS", bg=COLOR_PASS_BG)
            self.lbl_phase.configure(text="every application tested passed")

        # The strip names whichever application was tested last, so leaving it
        # red would read as a verdict on that application rather than on the
        # run. It states the outcome of the run instead, on a neutral ground.
        self.lbl_app.configure(text="Test complete")
        self.lbl_mode.configure(text="{0} tested, {1} passed, {2} failed, "
                                     "{3} skipped".format(
                                         len(tested),
                                         len(tested) - len(failing),
                                         len(failing),
                                         len(summary) - len(tested)))
        self.live_frame.configure(bg=COLOR_RUN_BG)
        for widget in (self.lbl_app, self.lbl_mode, self.lbl_phase):
            widget.configure(bg=COLOR_RUN_BG)

    # -- export ------------------------------------------------------------

    def _export(self):
        if not self.rows:
            return
        output_dir = (self.vars["output"].get().strip()
                      or paths_module.default_report_dir())
        paths = report_module.timestamped_paths(output_dir, ["csv", "xlsx"])

        written = [report_module.write_simple_csv(self.rows, paths["csv"])]
        written.append(report_module.write_csv(
            self.rows, paths["csv"].replace("_report_", "_details_")))
        xlsx = report_module.write_xlsx(self.rows, paths["xlsx"])
        if xlsx:
            written.append(xlsx)

        messagebox.showinfo(TITLE, "Exported:\n\n" + "\n".join(
            os.path.abspath(path) for path in written))

    def _on_close(self):
        # Let the worker unwind before tearing down Tk: it holds a reference to
        # this window, and finalising Tk from another thread aborts the process.
        self.cancel_event.set()
        if self.worker is not None and self.worker.is_alive():
            self.lbl_phase.configure(text="Stopping, please wait...")
            self.update()
            self.worker.join(timeout=5)
            self.worker = None
        self.destroy()

    def destroy(self):
        # Stop the queue pump first, or Tcl complains about a callback whose
        # command no longer exists.
        self.cancel_event.set()
        if getattr(self, "_pump_id", None) is not None:
            try:
                self.after_cancel(self._pump_id)
            except tk.TclError:
                pass
            self._pump_id = None
        super().destroy()


def launch(config_path="config.json"):
    ValidatorApp(config_path).mainloop()
    return 0
