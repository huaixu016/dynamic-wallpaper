# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import sys

root = Path(SPECPATH)
sys.path.insert(0, str(root))
from dynamic_wallpaper.mpv import find_library
from tools.bundle_support import ctypes_binaries, python_dll_search_paths

# Resolve native standard-library dependencies in unactivated Conda/venv installations as well.
os.environ["PATH"] = os.pathsep.join([*python_dll_search_paths(), os.environ.get("PATH", "")])
library = find_library()
if library.name not in {"mpv-2.dll", "libmpv-2.dll", "mpv-1.dll"}:
    raise ValueError("打包时请使用标准名称 mpv-2.dll、libmpv-2.dll 或 mpv-1.dll")
mpv_binaries = [(str(path), "vendor/mpv") for path in library.parent.glob("*.dll")]
runtime_binaries = ctypes_binaries()
icon = str(root / "src" / "logo.ico")

analysis = Analysis(
    [str(root / "launcher.py")], pathex=[str(root)], binaries=mpv_binaries + runtime_binaries,
    datas=[(str(root / "vendor/mpv/README.md"), "vendor/mpv"), (icon, "src")],
    hiddenimports=["dynamic_wallpaper.renderer"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
gui_exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True,
    name="LocalDynamicWallpaper", debug=False, strip=False, upx=False, console=False,
    manifest=str(root / "windows.manifest"), icon=icon,
)
renderer_exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True,
    name="LocalDynamicWallpaperRenderer", debug=False, strip=False, upx=False, console=True,
    manifest=str(root / "windows.manifest"), icon=icon,
)
collection = COLLECT(gui_exe, renderer_exe, analysis.binaries, analysis.datas, strip=False, upx=False,
                     name="LocalDynamicWallpaper")
