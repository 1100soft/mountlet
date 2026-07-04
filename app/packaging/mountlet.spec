# -*- mode: python ; coding: utf-8 -*-

import sys
import os
import subprocess
import tomllib
from pathlib import Path


root = Path.cwd()
project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
version = project["project"]["version"]
assets_dir = root / "src" / "mountlet" / "assets"
icon_png = assets_dir / "icon.png"
icon = assets_dir / ("icon.icns" if sys.platform == "darwin" else "icon.png")
hidden_imports = ["AppKit", "Foundation", "objc"] if sys.platform == "darwin" else []
rclone_name = "rclone.exe" if sys.platform == "win32" else "rclone"
rclone_path = os.environ.get("MOUNTLET_BUNDLED_RCLONE_PATH")
bundled_rclone = Path(rclone_path) if rclone_path else root / "vendor" / "rclone" / rclone_name
binaries = [(str(bundled_rclone), "vendor/rclone")] if bundled_rclone.is_file() else []


def macos_openssl_binaries():
    if sys.platform != "darwin":
        return []
    candidates = [
        Path("/opt/homebrew/opt/openssl@3/lib"),
        Path("/usr/local/opt/openssl@3/lib"),
    ]
    try:
        prefix = subprocess.check_output(["brew", "--prefix", "openssl@3"], text=True).strip()
    except Exception:
        prefix = ""
    if prefix:
        candidates.insert(0, Path(prefix) / "lib")
    libraries = []
    for lib_dir in candidates:
        libcrypto = lib_dir / "libcrypto.3.dylib"
        libssl = lib_dir / "libssl.3.dylib"
        if libcrypto.is_file() and libssl.is_file():
            libraries.extend([(str(libcrypto), "."), (str(libssl), ".")])
            break
    return libraries


binaries.extend(macos_openssl_binaries())

a = Analysis(
    [str(root / "packaging" / "mountlet_desktop.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=[(str(assets_dir), "mountlet/assets")],
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
            "LSMinimumSystemVersion": os.environ.get("MACOSX_DEPLOYMENT_TARGET", "11.0"),
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
        },
    )
