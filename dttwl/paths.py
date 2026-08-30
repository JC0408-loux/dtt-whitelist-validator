"""Where reports are written by default.

The folder lives under the *signed-in user's* Documents, not Public Documents.
Test machines are shared: several engineers log in to the same box, and a
report folder under Public would mix their runs together.  Some sites also lock
Public\\Documents down by policy, which would make the first export fail with a
permission error the tester cannot act on.

On a managed machine Documents is frequently redirected (OneDrive, a network
home drive), so the real location is asked for rather than assembled from
%USERPROFILE% - the assembled path would point at a folder Explorer no longer
shows.
"""

import os

REPORT_FOLDER_NAME = "DTT Whitelist Validation Reports"

# KNOWNFOLDERID for the current user's Documents.
_FOLDERID_DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"


def _known_folder(folder_id):
    """Ask Windows where a known folder really is.

    Returns None off Windows, and on any failure, so callers fall back to the
    assembled path instead of crashing.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_byte * 8),
            ]

        guid = GUID()
        if ctypes.windll.ole32.CLSIDFromString(folder_id, ctypes.byref(guid)) != 0:
            return None

        buffer = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(buffer))
        if result != 0 or not buffer.value:
            return None

        resolved = buffer.value
        ctypes.windll.ole32.CoTaskMemFree(buffer)
        return resolved
    except Exception:
        return None


def documents_dir():
    """The current user's Documents folder, following any redirection."""
    resolved = _known_folder(_FOLDERID_DOCUMENTS)
    if resolved:
        return resolved
    return os.path.join(os.path.expanduser("~"), "Documents")


def default_report_dir():
    """The out-of-the-box report folder.

    Not created here - report writing already creates the folder it is given,
    so an unused default never leaves an empty folder behind.
    """
    return os.path.join(documents_dir(), REPORT_FOLDER_NAME)
