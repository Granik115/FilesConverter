# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project = Path(SPECPATH).resolve().parent
icon = project / "build" / "icon.ico"

a = Analysis(
    [str(project / "src" / "filesconverter" / "app.py")],
    pathex=[str(project / "src")],
    binaries=[],
    datas=[(str(icon), ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FilesConverter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon),
)

collect = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="FilesConverter",
)
