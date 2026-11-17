"""Native dependency collection and a standalone smoke check for Windows bundles."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def select_ffi_binaries(imports) -> list[tuple[str, str]]:
    binaries = {}
    for name, resolved in imports:
        if not name.lower().startswith(("ffi", "libffi")) or not name.lower().endswith(".dll"):
            continue
        if not resolved or not Path(resolved).is_file():
            raise RuntimeError(f"无法找到 _ctypes 依赖的 {name}，请检查当前 Python 环境的 DLL 安装。")
        binaries[name.lower()] = (str(resolved), ".")
    return [binaries[name] for name in sorted(binaries)]


def python_dll_search_paths() -> list[str]:
    paths = []
    for prefix in dict.fromkeys((sys.prefix, sys.base_prefix)):
        root = Path(prefix)
        paths.extend((root, root / "DLLs", root / "Library" / "bin"))
    return list(dict.fromkeys(str(path) for path in paths if path.is_dir()))


def ctypes_binaries() -> list[tuple[str, str]]:
    import _ctypes
    from PyInstaller.depend.bindepend import get_imports

    extension = Path(_ctypes.__file__)
    search_paths = list(dict.fromkeys((str(extension.parent), *python_dll_search_paths())))
    # Conda keeps libffi outside DLLs; collect the exact name imported by this Python build.
    return select_ffi_binaries(get_imports(str(extension), search_paths=search_paths))


def standalone_environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("PYTHON", "CONDA"))
           and key.upper() != "DYNAMIC_WALLPAPER_MPV_DLL"}
    system_root = env.get("SystemRoot", env.get("SYSTEMROOT", r"C:\Windows"))
    env["PATH"] = os.pathsep.join((str(Path(system_root) / "System32"), system_root))
    return env


def verify_bundle(directory: Path) -> dict:
    directory = directory.resolve()
    executable = directory / "LocalDynamicWallpaper.exe"
    helper = directory / "LocalDynamicWallpaperRenderer.exe"
    for path in (executable, helper):
        if not path.is_file():
            raise RuntimeError(f"打包输出不完整：缺少 {path.name}")
    # Both EXEs share the same Python archive and DLLs. The console helper exposes startup failures.
    with TemporaryDirectory(prefix="wallpaper-bundle-check-") as cwd:
        try:
            result = subprocess.run([str(helper), "--diagnose"], cwd=cwd, env=standalone_environment(),
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"打包后独立运行自检失败：{exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"退出码 {result.returncode}"
        raise RuntimeError(f"打包后独立运行自检失败：\n{detail}")
    try:
        report = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"打包后自检没有返回有效诊断结果：\n{result.stdout}") from exc
    if not isinstance(report, dict) or report.get("bits") != 64:
        raise RuntimeError("打包后自检未能确认 64 位 Python 运行库。")
    if "mpv_error" in report or not report.get("mpv_library"):
        raise RuntimeError(f"打包后播放器运行库无法加载：{report.get('mpv_error', '缺少运行库路径')}")
    bundled_library = Path(report["mpv_library"]).resolve()
    if not bundled_library.is_relative_to(directory):
        raise RuntimeError("打包后自检使用了输出目录以外的播放器运行库。")
    return report
