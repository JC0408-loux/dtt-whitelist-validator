# PyInstaller spec for the DTT whitelist validation tool.
#
# One-file build on purpose: stub mode copies this executable under another
# name to exercise a whitelist entry, which only works if the .exe is
# self-contained.

block_cipher = None

a = Analysis(
    ["../main.py"],
    pathex=[".."],
    binaries=[],
    datas=[],
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
