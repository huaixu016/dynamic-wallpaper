"""Fail fast if the native runtime is missing; never ship a known-unplayable build."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from dynamic_wallpaper.mpv import find_library
    from tools.bundle_support import ctypes_binaries, verify_bundle
    try:
        library = find_library()
        runtime_binaries = ctypes_binaries()
    except (ImportError, OSError, RuntimeError) as exc:
        print(f"无法打包：{exc}", file=sys.stderr)
        return 1
    print(f"使用播放器运行库：{library}", flush=True)
    for source, _destination in runtime_binaries:
        print(f"补齐 Python 运行库依赖：{source}", flush=True)
    result = subprocess.call([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                              str(ROOT / "wallpaper.spec")], cwd=ROOT)
    if result:
        return result
    try:
        verify_bundle(ROOT / "dist" / "LocalDynamicWallpaper")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("打包完成：独立运行自检通过（不依赖构建机的 Python/Conda DLL 搜索路径）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
