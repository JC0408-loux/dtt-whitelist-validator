"""Window and taskbar icon.

Windows draws a taskbar button using the icon of the process's Application User
Model ID, not the icon of the window.  A tool launched through python.exe - and
the portable build always is - therefore inherits Python's identity and Python's
icon, no matter what the window itself is carrying.  Giving the process its own
AppUserModelID before the first window exists is what actually changes the
taskbar button; Tk's own ``iconbitmap`` only reaches the title bar.

A shortcut carrying the icon does not help either: the .lnk is drawn with it,
but the button that appears once the process is running belongs to the process.

Two details that are easy to get wrong on 64-bit Windows are handled below:

* ``winfo_id()`` returns the HWND of Tk's *inner* frame.  The taskbar reads the
  top-level wrapper window, which is that window's parent.
* ``HICON`` is a pointer.  Left at ctypes' default ``c_int`` return type it is
  truncated above 2 GB, and the icon silently fails to apply.
"""

import os
import sys

APP_ID = "DTT.WhitelistValidator"
ICON_NAME = "DTT_App_Icon.ico"


def _candidate_dirs():
    """Every root the icon may sit under, most specific first."""
    here = os.path.dirname(os.path.abspath(__file__))
    dirs = []
    # PyInstaller unpacks bundled data into a temp dir named by _MEIPASS; the
    # directory holding the .exe contains no data files at all.
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        dirs.append(bundle)
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    dirs.append(os.path.dirname(here))  # repo root / portable app folder
    dirs.append(here)
    dirs.append(os.getcwd())
    return dirs


def icon_file():
    """Absolute path to the .ico, or None when it is not shipped."""
    for directory in _candidate_dirs():
        for candidate in (os.path.join(directory, "icon", ICON_NAME),
                          os.path.join(directory, ICON_NAME)):
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
    return None


def set_app_user_model_id(app_id=APP_ID):
    """Give this process its own taskbar identity.

    Must run *before* the first window is created: once the taskbar button
    exists it has already been filed under the host interpreter's identity.
    Returns True when Windows accepted it.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception:
        return False


def apply_window_icon(window, path=None):
    """Put the icon on the title bar and the taskbar button.

    Returns True when at least one of the two took effect.  Never raises: a
    missing or unreadable icon must not stop the tool from running.
    """
    path = path or icon_file()
    if not path:
        return False

    applied = False
    try:
        # `default` makes it the icon for dialogs this window opens too.
        window.iconbitmap(default=path)
        applied = True
    except Exception:
        pass

    if os.name != "nt":
        return applied

    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]

        # The wrapper window only exists once Tk has processed the creation.
        window.update_idletasks()
        inner = window.winfo_id()
        hwnd = user32.GetParent(inner) or inner

        WM_SETICON = 0x0080
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        for which, size in ((0, 16), (1, 32)):  # ICON_SMALL, ICON_BIG
            handle = user32.LoadImageW(
                None, path, IMAGE_ICON, size, size, LR_LOADFROMFILE)
            if handle:
                user32.SendMessageW(hwnd, WM_SETICON, which, handle)
                applied = True
    except Exception:
        pass

    return applied
