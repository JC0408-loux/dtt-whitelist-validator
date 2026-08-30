"""A do-nothing window used to test the whitelist by executable name.

DTT matches the workload hint against the foreground window's executable name.
If that match is by name alone, a whitelisted application does not have to be
installed to check that its entry works: a copy of this tool renamed to, say,
photoshop.exe and showing a window is enough to make the hint fire.

That assumption is platform-specific and must be confirmed before it is
trusted -- `--verify-stub` does exactly that, and the runner refuses to use
stub mode until it passes.
"""

import os
import shutil
import sys
import tempfile

from .winfg import IS_WINDOWS, LauncherError

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    class WNDCLASS(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

WS_OVERLAPPEDWINDOW = 0x00CF0000
SW_SHOW = 5
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
COLOR_WINDOW = 5

STUB_FLAG = "--stub-window"


def is_stub_invocation(argv=None):
    argv = sys.argv if argv is None else argv
    return STUB_FLAG in argv


def run_stub_window(title="DTT whitelist stub"):
    """Show an empty top-level window and pump messages until killed."""
    if not IS_WINDOWS:
        raise LauncherError("the stub window requires Windows")

    # Drop the inherited console so the only window this process owns is the
    # one created below; otherwise the console host would hold the foreground.
    kernel32.FreeConsole()

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg in (WM_CLOSE, WM_DESTROY):
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(wnd_proc)
    instance = kernel32.GetModuleHandleW(None)
    class_name = "DttWhitelistStub"

    wndclass = WNDCLASS()
    wndclass.style = 0
    wndclass.lpfnWndProc = proc
    wndclass.cbClsExtra = 0
    wndclass.cbWndExtra = 0
    wndclass.hInstance = instance
    wndclass.hIcon = None
    wndclass.hCursor = None
    wndclass.hbrBackground = COLOR_WINDOW + 1
    wndclass.lpszMenuName = None
    wndclass.lpszClassName = class_name

    if not user32.RegisterClassW(ctypes.byref(wndclass)):
        raise LauncherError("RegisterClassW failed: %s" % ctypes.get_last_error())

    hwnd = user32.CreateWindowExW(
        0, class_name, title, WS_OVERLAPPEDWINDOW,
        200, 200, 640, 400, None, None, instance, None,
    )
    if not hwnd:
        raise LauncherError("CreateWindowExW failed: %s" % ctypes.get_last_error())

    user32.ShowWindow(hwnd, SW_SHOW)
    user32.UpdateWindow(hwnd)
    user32.SetForegroundWindow(hwnd)

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    # `proc` must outlive the loop or Windows would call into freed memory.
    del proc


def stub_directory():
    path = os.path.join(tempfile.gettempdir(), "dtt_wl_stubs")
    os.makedirs(path, exist_ok=True)
    return path


def stub_plan(executable, frozen, app_main, temp_dir, process_name, title):
    """Decide how to run the stub window under `process_name`.

    Returns (source, destination, args) and touches nothing, so the branching
    is testable off Windows.

    A frozen build copies its own .exe, which is self-contained and can live
    anywhere.  A portable build runs under python.exe, which needs its DLLs and
    standard library beside it, so the copy has to stay in the interpreter's own
    directory and is handed the application's main script.
    """
    if frozen:
        return executable, os.path.join(temp_dir, process_name), [
            STUB_FLAG, "--title", title,
        ]

    interpreter_dir = os.path.dirname(os.path.abspath(executable))
    if not os.path.isdir(interpreter_dir):
        raise LauncherError("cannot locate the interpreter directory")
    if not app_main:
        raise LauncherError("cannot locate main.py for the stub launch")

    return executable, os.path.join(interpreter_dir, process_name), [
        app_main, STUB_FLAG, "--title", title,
    ]


def app_main_script():
    """Path to main.py beside the package, for a source or portable layout."""
    package_dir = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(os.path.dirname(package_dir), "main.py")
    return candidate if os.path.isfile(candidate) else ""


def make_stub_launch(process_name, title):
    """Create the renamed interpreter and return (exe_path, args) to run it."""
    if not IS_WINDOWS:
        raise LauncherError("stub mode requires Windows")

    source, destination, args = stub_plan(
        sys.executable,
        bool(getattr(sys, "frozen", False)),
        app_main_script(),
        stub_directory(),
        process_name,
        title,
    )

    if not os.path.exists(destination):
        try:
            shutil.copy2(source, destination)
        except OSError as exc:
            raise LauncherError(
                "could not create the stub executable at {0}: {1}".format(
                    destination, exc
                )
            )
    return destination, args


def clear_stubs():
    path = stub_directory()
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
