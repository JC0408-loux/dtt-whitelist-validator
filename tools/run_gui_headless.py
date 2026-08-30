"""Open the real window on a machine with neither Windows nor DTT.

The tool has exactly one Windows-dependent seam - `WindowsLauncher` - and
everything else (the WebSocket client, the ESIF framing, the XML parsing, the
arbitration, the reports, the whole tkinter UI) is platform-neutral. Replace
that one object and the window runs anywhere.

What stands in for the platform lives in `tests/mock_dtt.py`, and none of it is
a stub returning canned answers:

  MockDttServer   opens a real TCP socket and speaks real WebSocket, so
                  wsclient.py performs a genuine RFC 6455 handshake against it
  DttSimulator    loads the real status XML captured from a test machine and
                  recomputes the Workload-dependent minterms when the
                  foreground changes, so it behaves like DTT rather than like a
                  recording
  FakeLauncher    replaces WindowsLauncher, with a configurable hint debounce
                  and switches for "fails to launch" and "never reaches the
                  foreground"

Use this to *look at* a change before claiming it works. A green test suite
says the covered paths still behave; it says nothing about what the window
shows. The v0.2 report folder defaulted to C:\\Users\\Public\\Documents and
shipped, with the suite green - one glance at the Settings tab would have
caught it.

Requires a display and tkinter:

    sudo apt-get install -y python3-tk xvfb
    python -m pip install -r requirements-dev.txt

Open it and poke at it:

    xvfb-run -a python tools/run_gui_headless.py

Drive a whole sweep and screenshot each step (used to build the SOP deck):

    xvfb-run -a --server-args="-screen 0 1400x900x24" \\
        python tools/run_gui_headless.py --capture docs/shots

On a machine with a real display, drop `xvfb-run` and the window just opens.
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests.lnk_builder import build_lnk
from tests.mock_dtt import DttSimulator, FakeLauncher, MockDttServer

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
WL1 = os.path.join(FIXTURES, "status_wl1.xml")
WL2 = os.path.join(FIXTURES, "status_wl2.xml")

SHOT_APPS = {"cinebench.exe", "msedge.exe", "chrome.exe"}


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.02)


def wait_until(app, predicate, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        if predicate():
            return True
        time.sleep(0.05)
    return False


def shot(app, out_dir, name):
    """Screenshot the window. Needs ImageMagick's `import`."""
    app.update()
    app.update_idletasks()
    time.sleep(0.4)
    app.update()
    path = os.path.join(out_dir, name + ".png")
    subprocess.run(["import", "-window", str(app.winfo_id()), path],
                   check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    print("captured", path)


def start(port=8888, debounce=1.6):
    """A running mock DTT and a window wired to it."""
    from dttwl import gui as gui_module

    simulator = DttSimulator(WL1, extra_fixtures=[WL2])
    server = MockDttServer(simulator, port=port).start()

    # The one seam that needs Windows.
    gui_module.WindowsLauncher = (
        lambda config, **kwargs: FakeLauncher(simulator, debounce=debounce))

    workdir = tempfile.mkdtemp(prefix="dttwl_headless_")
    app = gui_module.ValidatorApp(config_path=os.path.join(workdir, "config.json"))
    app.geometry("1060x720")
    app.vars["host"].set("localhost")
    app.vars["port"].set(str(server.port))
    app.vars["rounds"].set("1")
    app.vars["poll"].set("0.4")
    app.vars["debounce"].set("2.0")
    app.vars["samples"].set("3")
    app.vars["timeout"].set("15.0")
    return gui_module, simulator, server, app, workdir


def shortcut_folder(workdir):
    folder = os.path.join(workdir, "DTT Test Apps")
    os.makedirs(folder, exist_ok=True)
    for label, target in {
        "Cinebench.lnk": "C:\\Tools\\Cinebench\\cinebench.exe",
        "Microsoft Edge.lnk": "C:\\Program Files\\Microsoft\\Edge\\msedge.exe",
        "Google Chrome.lnk": "C:\\Program Files\\Google\\Chrome\\chrome.exe",
    }.items():
        with open(os.path.join(folder, label), "wb") as handle:
            handle.write(build_lnk(target))
    return folder


def capture(out_dir):
    gui_module, _simulator, server, app, workdir = start()
    os.makedirs(out_dir, exist_ok=True)

    dialogs = []
    gui_module.messagebox.showinfo = lambda *a, **k: dialogs.append(a[-1])
    gui_module.messagebox.showerror = lambda *a, **k: dialogs.append(a[-1])
    gui_module.messagebox.showwarning = lambda *a, **k: dialogs.append(a[-1])

    reports = os.path.join(workdir, "reports")
    app.vars["output"].set(reports)
    pump(app, 0.6)
    shot(app, out_dir, "01_launch")

    app.notebook.select(app.tab_settings)
    pump(app, 0.4)
    shot(app, out_dir, "02_settings")

    app._test_connection()
    wait_until(app, lambda: dialogs, 40)
    pump(app, 0.4)
    shot(app, out_dir, "03_settings_after_connect")
    dialogs.clear()

    app.notebook.select(app.tab_apps)
    app._reload_whitelist()
    wait_until(app, lambda: len(app.apps) > 0, 30)
    pump(app, 0.5)
    shot(app, out_dir, "04_whitelist_loaded")

    app.var_folder.set(shortcut_folder(workdir))
    app._scan_folder()
    pump(app, 0.8)
    shot(app, out_dir, "05_paths_matched")

    for entry in app.apps:
        entry["enabled"] = entry["process_name"] in SHOT_APPS
    app._refresh_app_table()
    app.notebook.select(app.tab_test)
    pump(app, 0.4)
    shot(app, out_dir, "06_test_tab_idle")

    app._start_run()
    wait_until(app, lambda: app.banner["text"] == "TESTING...", 40)
    pump(app, 1.2)
    shot(app, out_dir, "07_running")

    wait_until(app, lambda: len(app.rows) >= len(SHOT_APPS), 120)
    wait_until(app, lambda: str(app.btn_start["state"]) == "normal", 30)
    pump(app, 0.8)
    shot(app, out_dir, "08_complete")

    app._on_close()
    server.stop()


def interactive():
    _gui, _sim, server, app, _workdir = start()
    print("Window is up against the mock DTT on port {0}. "
          "Close it to exit.".format(server.port))
    try:
        app.mainloop()
    finally:
        server.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--capture", metavar="DIR",
        help="drive a full sweep and write a screenshot per step into DIR")
    args = parser.parse_args()
    if args.capture:
        capture(args.capture)
    else:
        interactive()


if __name__ == "__main__":
    main()
