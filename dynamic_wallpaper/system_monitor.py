import ctypes as C
import logging
import os
from ctypes import wintypes as W

from PySide6.QtCore import QObject, QTimer, Signal

from . import win32 as win
from .background import GUID
from .desktop import enumerate_monitors
from .models import CoverageKind, Monitor, Rect

log = logging.getLogger(__name__)

RegisterPowerSettingNotification = win.bind(win.user32, "RegisterPowerSettingNotification", W.HANDLE,
                                             W.HANDLE, C.POINTER(GUID), W.DWORD)
UnregisterPowerSettingNotification = win.bind(win.user32, "UnregisterPowerSettingNotification", W.BOOL, W.HANDLE)
WTSQuerySessionInformation = win.bind(win.wtsapi32, "WTSQuerySessionInformationW", W.BOOL,
                                     W.HANDLE, W.DWORD, C.c_int, C.POINTER(C.c_void_p), C.POINTER(W.DWORD))
WTSFreeMemory = win.bind(win.wtsapi32, "WTSFreeMemory", None, C.c_void_p)


class WTSINFOEX_LEVEL1(C.Structure):
    _fields_ = [("SessionId", W.ULONG), ("SessionState", C.c_int), ("SessionFlags", W.LONG),
                ("WinStationName", W.WCHAR * 33), ("UserName", W.WCHAR * 21), ("DomainName", W.WCHAR * 18),
                *[(name, C.c_int64) for name in ("LogonTime", "ConnectTime", "DisconnectTime", "LastInputTime", "CurrentTime")],
                *[(name, W.DWORD) for name in ("IncomingBytes", "OutgoingBytes", "IncomingFrames", "OutgoingFrames",
                                             "IncomingCompressedBytes", "OutgoingCompressedBytes")]]


class WTSINFOEX(C.Structure):
    _fields_ = [("Level", W.DWORD), ("Data", WTSINFOEX_LEVEL1)]


def session_locked() -> bool | None:
    pointer, size = C.c_void_p(), W.DWORD()
    if not WTSQuerySessionInformation(None, 0xFFFFFFFF, 25, C.byref(pointer), C.byref(size)):
        return None
    try:
        if size.value < C.sizeof(WTSINFOEX):
            return None
        info = C.cast(pointer, C.POINTER(WTSINFOEX)).contents
        if info.Level == 1 and info.Data.SessionFlags in {0, 1}:
            return info.Data.SessionFlags == 0
        return None
    finally:
        WTSFreeMemory(pointer)


def monitor_coverage(monitors: list[Monitor]) -> dict[str, set[CoverageKind]]:
    foreground = win.GetForegroundWindow()
    if foreground and win.class_name(foreground) in {"Progman", "WorkerW"}:
        return {}
    covered: dict[str, set[CoverageKind]] = {}
    ignored_classes = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
    for hwnd in win.window_list():
        if (not win.IsWindowVisible(hwnd) or win.IsIconic(hwnd) or win.window_pid(hwnd) == os.getpid()
                or win.class_name(hwnd) in ignored_classes):
            continue
        # Idle GPU overlays can be visible, full-screen HWNDs without obscuring the desktop.
        ex_style = win.GetWindowLongPtr(hwnd, -20)  # GWL_EXSTYLE
        if ex_style & (0x00000080 | 0x08000000):  # WS_EX_TOOLWINDOW / WS_EX_NOACTIVATE
            continue
        cloaked = W.DWORD()
        if win.DwmGetWindowAttribute(hwnd, 14, C.byref(cloaked), C.sizeof(cloaked)) >= 0 and cloaked.value:
            continue
        native_rect = W.RECT()
        if win.DwmGetWindowAttribute(hwnd, 9, C.byref(native_rect), C.sizeof(native_rect)) < 0:
            if not win.GetWindowRect(hwnd, C.byref(native_rect)):
                continue
        bounds = Rect(native_rect.left, native_rect.top, native_rect.right, native_rect.bottom)
        if min(bounds.width, bounds.height) <= 0:
            continue
        maximized = bool(win.IsZoomed(hwnd))
        caption = bool(win.GetWindowLongPtr(hwnd, -16) & 0x00C00000)  # WS_CAPTION
        for monitor in monitors:
            kind = None
            # A decorated maximized window is still maximized when an auto-hidden taskbar makes work == bounds.
            if maximized and caption and bounds.covers(monitor.work):
                kind = CoverageKind.MAXIMIZED
            elif bounds.covers(monitor.bounds):
                kind = CoverageKind.FULLSCREEN
            elif maximized and bounds.covers(monitor.work):
                kind = CoverageKind.MAXIMIZED
            if kind:
                covered.setdefault(monitor.device, set()).add(kind)
    return covered


def covered_monitors(monitors: list[Monitor]) -> set[str]:
    return set(monitor_coverage(monitors))


class SystemMonitor(QObject):
    environment_changed = Signal(object, object, object)
    dirty = Signal()

    def __init__(self, hwnd: int, parent=None):
        super().__init__(parent)
        self.hwnd = hwnd
        self.active = False
        self.locked = bool(session_locked())
        self.sleeping = False
        self.display_off = False
        self.disconnected = False
        self.hooks = []
        self.power_handles = []
        self._callback = win.WINEVENTPROC(self._event)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(250)
        self.debounce.timeout.connect(self.refresh)
        self.dirty.connect(self.mark_dirty)
        self.fallback = QTimer(self)
        self.fallback.setInterval(2000)
        self.fallback.timeout.connect(self.refresh)
        self.session_registered = bool(win.WTSRegisterSessionNotification(hwnd, 0))
        if not self.session_registered:
            log.warning("会话通知注册失败，将在壁纸开启时使用低频校验")
        guid = GUID.parse("2B84C20E-AD23-4DDF-93DB-05FFBD7EFCA5")  # GUID_SESSION_DISPLAY_STATUS
        handle = RegisterPowerSettingNotification(hwnd, C.byref(guid), 0)
        if handle:
            self.power_handles.append(handle)
        else:
            log.warning("显示器电源通知注册失败")

    def set_active(self, value: bool) -> None:
        if self.active == value:
            return
        self.active = value
        if value:
            for first, last in ((0x0003, 0x0003), (0x0016, 0x0017), (0x800B, 0x800B)):
                handle = win.SetWinEventHook(first, last, None, self._callback, 0, 0, 0x0002)
                if handle:
                    self.hooks.append(handle)
            self.fallback.start()
            self.mark_dirty()
        else:
            self.fallback.stop()
            for handle in self.hooks:
                win.UnhookWinEvent(handle)
            self.hooks.clear()

    def _event(self, _hook, event, hwnd, object_id, child_id, _thread, _time):
        if event == 0x800B and (object_id != 0 or child_id != 0 or hwnd != win.GetForegroundWindow()):
            return
        self.dirty.emit()

    def mark_dirty(self) -> None:
        # Do not restart on every location event: dragging must not starve the refresh.
        if not self.debounce.isActive():
            self.debounce.start()

    def refresh(self) -> None:
        try:
            monitors = enumerate_monitors()
            locked = session_locked()
            if locked is not None:
                self.locked = locked
            reasons = set()
            if self.locked:
                reasons.add("锁屏")
            if self.sleeping:
                reasons.add("系统睡眠")
            if self.display_off:
                reasons.add("显示器关闭")
            if self.disconnected:
                reasons.add("会话不可见")
            blocked = monitor_coverage(monitors) if self.active and not reasons else {}
            self.environment_changed.emit(monitors, reasons, blocked)
        except OSError:
            log.exception("无法更新系统显示状态")
            self.environment_changed.emit([], {"显示器状态不可用"}, set())

    def native_message(self, message: int, wparam: int, lparam: int) -> bool:
        if message in {0x007E, 0x02E0}:  # display / DPI change
            self.mark_dirty()
        elif message == 0x02B1:  # WM_WTSSESSION_CHANGE
            if wparam in {7, 8}:
                self.locked = wparam == 7
            elif wparam in {1, 2, 3, 4}:
                self.disconnected = wparam in {2, 4}
            self.refresh()
        elif message == 0x0218:  # WM_POWERBROADCAST
            if wparam == 4:
                self.sleeping = True
            elif wparam in {7, 18}:
                self.sleeping = False
            elif wparam == 0x8013 and lparam:
                guid = GUID.from_address(lparam)
                target = GUID.parse("2B84C20E-AD23-4DDF-93DB-05FFBD7EFCA5")
                length = W.DWORD.from_address(lparam + C.sizeof(GUID)).value
                if bytes(guid) == bytes(target) and length >= 4:
                    value = W.DWORD.from_address(lparam + 20).value
                    self.display_off = value == 0
            self.refresh()
            return True
        return False

    def close(self) -> None:
        self.set_active(False)
        self.debounce.stop()
        if self.session_registered:
            win.WTSUnRegisterSessionNotification(self.hwnd)
            self.session_registered = False
        for handle in self.power_handles:
            UnregisterPowerSettingNotification(handle)
        self.power_handles.clear()
