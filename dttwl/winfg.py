"""Windows process launching and foreground-window control.

DTT decides the workload hint from the *foreground* window, not from which
processes exist, so simply starting an executable proves nothing.  Everything
here is ctypes against user32/kernel32 so the tool keeps to the standard
library and packages into a single executable.
"""

import os
import subprocess
import sys
import time

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:  # pragma: no cover - import guard for development on other platforms
    ctypes = None
    wintypes = None
    user32 = None
    kernel32 = None

SW_RESTORE = 9
SW_SHOW = 5
WM_CLOSE = 0x0010
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260
CREATE_NEW_PROCESS_GROUP = 0x00000200


class LauncherError(Exception):
    pass


def _require_windows():
    if not IS_WINDOWS:
        raise LauncherError("this operation requires Windows")


# --------------------------------------------------------------------------
# process enumeration
# --------------------------------------------------------------------------

if IS_WINDOWS:

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * MAX_PATH),
        ]


def list_processes():
    """Return [(pid, lowercase exe name)] for every running process."""
    _require_windows()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        raise LauncherError("CreateToolhelp32Snapshot failed")
    processes = []
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not kernel32.Process32First(snapshot, ctypes.byref(entry)):
            return processes
        while True:
            processes.append(
                (int(entry.th32ProcessID), entry.szExeFile.decode("mbcs", "replace").lower())
            )
            if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return processes


def pids_for_process_name(process_name):
    target = process_name.lower()
    return [pid for pid, name in list_processes() if name == target]


def process_name_for_pid(pid):
    for other, name in list_processes():
        if other == pid:
            return name
    return ""


# --------------------------------------------------------------------------
# window helpers
# --------------------------------------------------------------------------


def _window_pid(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _window_title(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def top_level_windows(pids):
    """Visible, titled top-level windows belonging to any of `pids`."""
    _require_windows()
    wanted = set(pids)
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if _window_pid(hwnd) not in wanted:
            return True
        if not _window_title(hwnd):
            return True
        found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return found


def foreground_window():
    _require_windows()
    return user32.GetForegroundWindow()


def foreground_process_name():
    """Lowercase executable name owning the foreground window."""
    _require_windows()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    return process_name_for_pid(_window_pid(hwnd))


def force_foreground(hwnd):
    """Bring `hwnd` to the front, working around focus-stealing prevention.

    Windows only lets the process that owns the current foreground window hand
    focus away, so the calling thread attaches its input queue to that window's
    thread for the duration of the call.  A synthetic ALT tap is the documented
    fallback when the attach is refused.
    """
    _require_windows()
    if not hwnd:
        return False

    user32.ShowWindow(hwnd, SW_RESTORE)

    current = user32.GetForegroundWindow()
    if current == hwnd:
        return True

    this_thread = kernel32.GetCurrentThreadId()
    other_thread = user32.GetWindowThreadProcessId(current, None) if current else 0

    attached = False
    if other_thread and other_thread != this_thread:
        attached = bool(user32.AttachThreadInput(this_thread, other_thread, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(this_thread, other_thread, False)

    if user32.GetForegroundWindow() == hwnd:
        return True

    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)
    return user32.GetForegroundWindow() == hwnd


# --------------------------------------------------------------------------
# launching
# --------------------------------------------------------------------------


class LaunchedApp:
    def __init__(self, process_name, popen=None, pre_existing=None):
        self.process_name = process_name.lower()
        self.popen = popen
        # Processes of this name that were already running. They must never be
        # closed - closing an Edge that was already open would take the DTT
        # page down with it - and their presence also disables the force-kill
        # entirely, because a helper this launch created may belong to their
        # process tree. Their windows may still be brought to the foreground:
        # see foreground_candidates.
        self.pre_existing = set(pre_existing or ())
        self.pids = []
        self.hwnd = None

    def refresh_pids(self):
        self.pids = pids_for_process_name(self.process_name)
        if self.popen is not None and self.popen.pid not in self.pids:
            if self.popen.poll() is None:
                self.pids.append(self.popen.pid)
        return self.pids

    def owned_pids(self):
        """The processes this launch is responsible for."""
        return [pid for pid in self.pids if pid not in self.pre_existing]

    def foreground_candidates(self):
        """Sets of PIDs to look for a window in, most specific first.

        Owning a process is not the same as owning a window. A multi-process
        application - anything built on Electron, and the browsers - serves a
        new window from the instance that is already running and spawns helper
        processes that own no window at all. The launch therefore *does* own
        processes, none of which has anything to bring forward, while the
        window that must reach the foreground belongs to a process that existed
        before the test started.

        So: try the processes this launch created, and if none of them has a
        visible top-level window, try every process of this name. That is also
        what DTT matches on - the executable name - so the state being measured
        is the one the platform actually sees.

        Closing never widens like this; see close_app.
        """
        owned = self.owned_pids()
        if owned:
            yield owned
        yield list(self.pids)

    def joined_existing_instance(self):
        """True when this application was already running before the launch."""
        return bool(self.pre_existing)


def launch(exe_path=None, shell_target=None, args=None, process_name=None,
           working_dir=None):
    """Start an application, either by path or through a shell target.

    `shell_target` covers Store apps, which have no stable path and are started
    as `explorer.exe shell:AppsFolder\\<AUMID>`; those run detached, so the
    process is tracked by name instead of by handle.
    """
    _require_windows()

    if shell_target:
        name = (process_name or "").lower()
        if not name:
            raise LauncherError("a shell target also needs process_name")
        before = set(pids_for_process_name(name))
        # An AUMID has no spaces, so explorer.exe can be trusted with it.
        subprocess.Popen(["explorer.exe", shell_target], close_fds=True)
        return LaunchedApp(name, pre_existing=before)

    if not exe_path:
        raise LauncherError("either exe_path or shell_target is required")
    if not os.path.isfile(exe_path):
        raise LauncherError("file not found: {0}".format(exe_path))

    name = (process_name or os.path.basename(exe_path)).lower()
    before = set(pids_for_process_name(name))

    if exe_path.lower().endswith(".lnk"):
        # os.startfile, not "explorer.exe <path>": explorer re-parses its own
        # command line, so a shortcut whose path contains a space is split and
        # it opens a folder window instead - which then owns the foreground and
        # makes every reading wrong.
        os.startfile(exe_path)
        return LaunchedApp(name, pre_existing=before)

    popen = subprocess.Popen(
        [exe_path] + list(args or []),
        cwd=working_dir or os.path.dirname(exe_path) or None,
        creationflags=CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    return LaunchedApp(name, popen=popen, pre_existing=before)


def wait_for_foreground(app, timeout=30.0, poll=0.25):
    """Wait for the app to show a window, then push it to the foreground.

    Returns the monotonic timestamp at which the app was confirmed foreground,
    which is the t0 every switch-latency measurement is relative to.
    """
    _require_windows()
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        app.refresh_pids()
        for pids in app.foreground_candidates():
            for hwnd in top_level_windows(pids):
                if force_foreground(hwnd):
                    app.hwnd = hwnd
                    if foreground_process_name() == app.process_name:
                        return time.monotonic()
        time.sleep(poll)

    raise LauncherError(
        "{0} never reached the foreground within {1:.0f}s".format(
            app.process_name, timeout
        )
    )


def close_app(app, grace_seconds=5.0):
    """Ask the app to close, then force it down if it will not go.

    Returns a note describing anything unusual, or an empty string.
    """
    _require_windows()
    app.refresh_pids()
    owned = app.owned_pids()
    if not owned:
        # The application joined an instance that was already running, as a
        # browser does. Closing it would take that instance's other windows
        # with it, so it is left alone.
        return "left running: it joined an already-running process"

    for hwnd in top_level_windows(owned):
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        app.refresh_pids()
        if not app.owned_pids():
            return ""
        time.sleep(0.25)

    app.refresh_pids()
    if app.joined_existing_instance():
        # The application was already open before the test. The processes this
        # launch added are helpers inside that instance - Electron and the
        # browsers spawn one per window - and `taskkill /T` walks the process
        # tree, so killing a helper takes the user's session with it. That is
        # what put "The window terminated unexpectedly (reason: 'killed')" on
        # a tester's screen and lost the VS Code they were working in.
        #
        # A stray helper process is recoverable; somebody's editor is not.
        return ("left running: {0} was already open before the test, so the "
                "processes this launch added were not force-closed"
                .format(app.process_name))

    for pid in app.owned_pids():
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    return "force-terminated after {0:.0f}s grace".format(grace_seconds)


def console_window():
    """Handle of this process's console window, or 0 when there is none."""
    _require_windows()
    return kernel32.GetConsoleWindow()


def focus_self(hwnd=None):
    """Return focus to this tool's own window, the neutral baseline state.

    The validator is not on the whitelist, so with its window in front DTT
    reports no workload hint and falls back to the default action set.  The
    GUI passes its own window handle; the console build falls back to the
    console window.
    """
    _require_windows()
    hwnd = hwnd or console_window()
    if not hwnd:
        return False
    return force_foreground(hwnd)
