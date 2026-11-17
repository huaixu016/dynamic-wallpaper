import argparse
import ctypes
import json
import logging
import os
import struct
import sys


def diagnose(library=None) -> int:
    from .background import capture_background
    from .desktop import DesktopHost, enumerate_monitors
    from .mpv import find_library
    from .system_monitor import session_locked
    from . import win32 as win
    report = {"python": sys.version, "bits": struct.calcsize("P") * 8, "desktop_modified": False}
    report["monitors"] = [monitor.to_dict() for monitor in enumerate_monitors()]
    report["session_locked"] = session_locked()
    try:
        from .mpv import Mpv
        path = find_library(library)
        player = Mpv(path, probe=True)
        try:
            report["mpv_library"] = str(path)
            report["mpv_version"] = player.string("mpv-version")
            report["ffmpeg_version"] = player.string("ffmpeg-version")
        finally:
            player.close()
    except (OSError, RuntimeError) as exc:
        report["mpv_error"] = str(exc)
    try:
        report["background"] = capture_background()
    except OSError as exc:
        report["background_error"] = str(exc)
    try:
        report["existing_desktop_host"] = DesktopHost().locate(create=False)
    except RuntimeError as exc:
        report["desktop_host_error"] = str(exc)
    report["desktop_windows"] = [
        {"hwnd": hwnd, "class": win.class_name(hwnd), "parent": win.GetParent(hwnd)}
        for hwnd in win.window_list() if win.class_name(hwnd) in {"Progman", "WorkerW"}
    ]
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


def configure_dpi() -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        function = user32.SetProcessDpiAwarenessContext
        function.argtypes, function.restype = [ctypes.c_void_p], ctypes.c_int
        if function(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except AttributeError:
        pass
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        function = shcore.SetProcessDpiAwareness
        function.argtypes, function.restype = [ctypes.c_int], ctypes.c_long
        if function(2) in {0, -2147024891}:  # PROCESS_PER_MONITOR_DPI_AWARE / already set
            return
    except (AttributeError, OSError):
        pass
    fallback = user32.SetProcessDPIAware
    fallback.argtypes, fallback.restype = [], ctypes.c_int
    fallback()


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows 本地视频动态壁纸")
    parser.add_argument("--renderer", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--diagnose", action="store_true", help="只读检查系统环境，不创建或修改桌面窗口")
    parser.add_argument("--mpv-library", help="指定 x64 libmpv DLL 的绝对路径")
    args = parser.parse_args()
    if os.name != "nt" or struct.calcsize("P") != 8:
        print("仅支持 Windows 10 / Windows 11 64 位系统。", file=sys.stderr)
        return 1
    if sys.getwindowsversion().build < 10240:
        print("需要 Windows 10 或更新版本。", file=sys.stderr)
        return 1
    configure_dpi()
    if args.renderer:
        from .renderer import renderer_main
        return renderer_main()
    if args.diagnose:
        return diagnose(args.mpv_library)
    from PySide6.QtWidgets import QApplication, QMessageBox
    from .storage import ConfigStore, configure_logging, data_directory
    from .win32 import SingleInstance
    app = QApplication(sys.argv[:1])
    app.setApplicationName("LocalDynamicWallpaper")
    app.setQuitOnLastWindowClosed(False)
    instance = SingleInstance()
    if instance.already_running:
        try:
            if instance.notify():
                return 0
            QMessageBox.warning(None, "程序已在运行", "暂时无法唤起已有窗口，请等待程序加载完成，或从系统托盘打开主界面。")
            return 1
        except OSError as exc:
            QMessageBox.warning(None, "无法唤起已有窗口", str(exc))
            return 1
        finally:
            instance.close()
    controller = None
    try:
        configure_logging(data_directory() / "logs")
        from .ui import ApplicationController
        controller = ApplicationController(app, ConfigStore(), instance, args.mpv_library)
        controller.window.show()
        return app.exec()
    except Exception as exc:
        logging.getLogger(__name__).exception("程序启动失败")
        QMessageBox.critical(None, "动态壁纸启动失败", str(exc))
        return 1
    finally:
        if controller is not None and controller.pool.records:
            controller.pool.close()
        instance.close()


if __name__ == "__main__":
    raise SystemExit(main())
