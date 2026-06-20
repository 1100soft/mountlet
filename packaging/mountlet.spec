# -*- mode: python ; coding: utf-8 -*-

import sys
import tomllib
from pathlib import Path


root = Path.cwd()
project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
version = project["project"]["version"]
icon = root / "src" / "mountlet" / "assets" / "icon.png"
hidden_imports = ["AppKit", "Foundation", "objc"] if sys.platform == "darwin" else []

a = Analysis(
    [str(root / "packaging" / "mountlet_desktop.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(icon), "mountlet/assets")],
    hiddenimports=hidden_imports,
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
    name="Mountlet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Mountlet",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Mountlet.app",
        icon=str(icon),
        bundle_identifier="com.ericholt.mountlet",
        version=version,
        info_plist={
            "CFBundleDisplayName": "Mountlet",
            "CFBundleName": "Mountlet",
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
        },
    )
