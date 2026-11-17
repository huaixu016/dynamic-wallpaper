import ctypes as C
import logging
from ctypes import wintypes as W

from . import win32 as win
from .models import DisplayMode, Monitor, Priority, Rect, viewport_geometry

log = logging.getLogger(__name__)


def enumerate_monitors() -> list[Monitor]:
    monitors = []
    failures = []

    @win.MONITORPROC
    def callback(handle, _dc, _rect, _data):
        info = win.MONITORINFOEXW()
        info.cbSize = C.sizeof(info)
        if not win.GetMonitorInfo(handle, C.byref(info)):
            failures.append(C.get_last_error())
            return False
        bounds = Rect(info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom)
        work = Rect(info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom)
        if bounds.width > 0 and bounds.height > 0:
            monitors.append(Monitor(info.szDevice, bounds, work, bool(info.dwFlags & 1)))
        return True

    win.check(win.EnumDisplayMonitors(None, None, callback, 0), "无法读取显示器布局")
    if failures:
        raise OSError(failures[0], "无法读取显示器信息")
    return sorted(monitors, key=lambda monitor: (not monitor.primary, monitor.device))


class DesktopHost:
    def __init__(self):
        self.hwnd: int | None = None

    @staticmethod
    def valid(hwnd: int | None) -> bool:
        if not hwnd or not win.IsWindow(hwnd) or win.class_name(hwnd) != "WorkerW":
            return False
        progman = win.FindWindow("Progman", None)
        if (not progman or win.window_pid(hwnd) != win.window_pid(progman)
                or win.FindWindowEx(hwnd, None, "SHELLDLL_DefView", None)):
            return False
        parent = win.GetParent(hwnd)
        if parent and parent != progman:
            return False
        area = W.RECT()
        return bool(win.GetClientRect(hwnd, C.byref(area)) and area.right > area.left and area.bottom > area.top)

    def locate(self, create: bool = True) -> int:
        if self.valid(self.hwnd):
            return self.hwnd
        progman = win.FindWindow("Progman", None)
        if not progman:
            raise RuntimeError("未找到 Windows 桌面，请等待 Explorer 启动后重试。")
        attempts = [(0, 0), (0xD, 0), (0xD, 1)] if create else [None]
        for attempt in attempts:
            if attempt is not None:
                result = C.c_size_t()
                win.SendMessageTimeout(progman, 0x052C, *attempt, 0x0002, 250, C.byref(result))
            for hwnd in win.window_list():
                if win.FindWindowEx(hwnd, None, "SHELLDLL_DefView", None):
                    sibling = win.FindWindowEx(None, hwnd, "WorkerW", None)
                    if self.valid(sibling):
                        self.hwnd = int(sibling)
                        return self.hwnd
            # Some Windows 11 shells put both layers inside Progman.
            if win.FindWindowEx(progman, None, "SHELLDLL_DefView", None):
                child = win.FindWindowEx(progman, None, "WorkerW", None)
                if self.valid(child):
                    self.hwnd = int(child)
                    return self.hwnd
        raise RuntimeError("无法定位图标下方的安全壁纸层；未修改系统背景。当前桌面结构可能不兼容。")

    def repaint(self) -> None:
        for hwnd in win.window_list():
            if win.class_name(hwnd) in {"Progman", "WorkerW"}:
                win.RedrawWindow(hwnd, None, None, 0x0001 | 0x0004 | 0x0080)
        self.hwnd = None


class NativeVideoWindow:
    """Own only our child HWNDs; never change or destroy Explorer's windows."""

    def __init__(self, parent: int, bounds: Rect, display_mode: DisplayMode = DisplayMode.COVER):
        if not DesktopHost.valid(parent):
            raise RuntimeError("桌面宿主已失效。")
        self.parent = parent
        self.bounds = bounds
        self.display_mode = DisplayMode(display_mode)
        self.video_size: tuple[int, int] | None = None
        self.container = None
        self.viewport = None
        self.instance = win.GetModuleHandle(None)
        self.class_name = f"LocalDynamicWallpaper.Renderer.{win.GetCurrentProcessId()}"
        self._wndproc = win.WNDPROC(self._procedure)
        window_class = win.WNDCLASSEXW()
        window_class.cbSize = C.sizeof(window_class)
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = self.instance
        window_class.lpszClassName = self.class_name
        # A stock black brush. No application-owned GDI brush to leak.
        window_class.hbrBackground = win.GetStockObject(4)  # BLACK_BRUSH
        win.check(win.RegisterClassEx(C.byref(window_class)), "无法注册壁纸播放窗口")
        self._registered = True
        try:
            style = 0x40000000 | 0x08000000 | 0x04000000 | 0x02000000  # CHILD, DISABLED, clip siblings/children
            self.container = win.check(win.CreateWindowEx(0x08000000, self.class_name, "", style,
                                                        0, 0, bounds.width, bounds.height,
                                                        parent, None, self.instance, None), "无法创建桌面视频容器")
            self.viewport = win.check(win.CreateWindowEx(0x08000000, self.class_name, "", style | 0x10000000,
                                                       0, 0, bounds.width, bounds.height,
                                                       self.container, None, self.instance, None), "无法创建视频输出窗口")
            self.resize(bounds)
        except Exception:
            self.close()
            raise

    def _procedure(self, hwnd, message, wparam, lparam):
        if message == 0x0021:  # WM_MOUSEACTIVATE
            return 3  # MA_NOACTIVATE
        if message == 0x0084:  # WM_NCHITTEST; disabled children also do not receive input.
            return -1
        return win.DefWindowProc(hwnd, message, wparam, lparam)

    def resize(self, bounds: Rect) -> None:
        if not DesktopHost.valid(self.parent) or not win.IsWindow(self.container):
            raise RuntimeError("桌面窗口失效，需要重新创建壁纸。")
        point = W.POINT(bounds.left, bounds.top)
        C.set_last_error(0)
        result = win.MapWindowPoints(None, self.parent, C.byref(point), 1)
        if result == 0 and C.get_last_error():
            raise C.WinError(C.get_last_error())
        win.check(win.SetWindowPos(self.container, None, point.x, point.y, bounds.width, bounds.height,
                                   0x0004 | 0x0010), "无法适配显示器区域")
        self.bounds = bounds
        self._fit_video()

    def set_video_size(self, width: int, height: int) -> None:
        self.video_size = (width, height)
        self._fit_video()

    def set_display_mode(self, mode: DisplayMode) -> None:
        self.display_mode = DisplayMode(mode)
        self._fit_video()

    def _fit_video(self) -> None:
        if self.video_size:
            x, y, width, height = viewport_geometry(self.bounds.width, self.bounds.height,
                                                  *self.video_size, self.display_mode)
        else:
            x, y, width, height = 0, 0, self.bounds.width, self.bounds.height
        win.check(win.SetWindowPos(self.viewport, None, x, y, width, height, 0x0004 | 0x0010),
                  "无法调整视频显示区域")
        win.RedrawWindow(self.container, None, None, 0x0001 | 0x0004 | 0x0080)

    def set_priority(self, priority: Priority) -> None:
        # HWND_TOP/HWND_BOTTOM here apply only inside the wallpaper parent.
        insert_after = 0 if priority == Priority.HIGH else 1
        win.check(win.SetWindowPos(self.container, insert_after, 0, 0, 0, 0,
                                   0x0001 | 0x0002 | 0x0010), "无法调整壁纸层级")

    def show(self, priority: Priority) -> None:
        self.set_priority(priority)
        win.ShowWindow(self.container, 4)  # SW_SHOWNOACTIVATE

    def hide(self) -> None:
        if self.container:
            win.ShowWindow(self.container, 0)

    def pump(self) -> None:
        message = win.MSG()
        while win.PeekMessage(C.byref(message), None, 0, 0, 1):
            win.TranslateMessage(C.byref(message))
            win.DispatchMessage(C.byref(message))

    def close(self) -> None:
        if self.container and win.IsWindow(self.container):
            win.DestroyWindow(self.container)
        self.container = self.viewport = None
        if getattr(self, "_registered", False):
            win.UnregisterClass(self.class_name, self.instance)
            self._registered = False
