# PyInstaller spec for the DTT whitelist validation tool.
#
# One-file build on purpose: stub mode copies this executable under another
# name to exercise a whitelist entry, which only works if the .exe is
# self-contained.

import os

block_cipher = None

# The window looks for icon/DTT_App_Icon.ico under a set of roots that includes
# sys._MEIPASS, so the file has to travel inside the bundle - a one-file build
# unpacks data there, never next to the .exe.
#
# SPECPATH, not os.getcwd(): build.bat runs PyInstaller from the repository
# root, so a cwd-relative "../icon" would point outside the repository.
ICON = os.path.abspath(os.path.join(SPECPATH, "..", "icon", "DTT_App_Icon.ico"))

a = Analysis(
    ["../main.py"],
    pathex=[".."],
    binaries=[],
    datas=[(ICON, "icon")],
    hiddenimports=["openpyxl"],
    hookspath=[],
    runtime_hooks=[],
    # tkinter is the GUI, so it must stay in the bundle.
    excludes=["unittest", "pydoc", "test"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="dtt-wl-validator",
    # Gives the .exe its icon in Explorer and on the taskbar. The taskbar also
    # needs the AppUserModelID set in dttwl/appicon.py.
    icon=ICON,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
