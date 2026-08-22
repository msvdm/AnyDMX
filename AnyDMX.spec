# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for AnyDMX. One file, one window, no installer.

Built by .github/workflows/release.yml on both Windows and Linux — PyInstaller
cannot cross-compile, so each platform's binary is built on that platform.

One-file was chosen deliberately: the people this app is for want to download
one thing and double-click it. The cost is a slower first start (the bundle
unpacks to a temp directory on each launch) and a higher chance of an
antivirus false positive, because self-extracting executables look like what
they are. Nothing here is signed — a certificate costs more per year than this
project costs to run — so the README tells users what warning to expect.

settings.json is written beside the executable, not into the bundle: see
src/utils/paths.py, where app_dir() and resource_dir() answer two different
questions on purpose.
"""

import sys

block_cipher = None

# The icon is the only bundled asset. The .ico is embedded in the Windows
# executable by EXE(icon=...); the .png is what Qt shows at runtime.
datas = [("assets/AnyDMX.png", "assets")]

# Qt modules this app has never imported. Excluding them does not shrink the
# Qt shared libraries — PySide6's hook collects those wholesale — but it keeps
# the Python side honest about what is actually used.
excludes = [
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQml",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets", "PySide6.QtMultimedia", "PySide6.QtCharts",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSql", "PySide6.QtTest",
    "tkinter", "unittest", "pydoc", "pytest", "numpy", "PIL",
]

a = Analysis(
    ["AnyDMX.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
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
    name="AnyDMX",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX compression is a reliable way to get flagged
    upx_exclude=[],
    runtime_tmpdir=None,
    # No console window behind the GUI on Windows. On Linux this is ignored,
    # and the elevated helper mode prints to whatever terminal launched it.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/AnyDMX.ico" if sys.platform == "win32" else None,
)
