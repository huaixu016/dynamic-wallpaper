"""Explicit 64-bit Win32 bindings. No desktop changes at import time."""

import ctypes as C
import os
import time
from ctypes import wintypes as W

if os.name != "nt":
    raise OSError("动态壁纸仅支持 Windows 10/11 64 位系统。")

user32 = C.WinDLL("user32", use_last_error=True)
kernel32 = C.WinDLL("kernel32", use_last_error=True)
dwmapi = C.WinDLL("dwmapi", use_last_error=True)
gdi32 = C.WinDLL("gdi32", use_last_error=True)
wtsapi32 = C.WinDLL("wtsapi32", use_last_error=True)
LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
ENUMPROC = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
MONITORPROC = C.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, C.POINTER(W.RECT), W.LPARAM)
WINEVENTPROC = C.WINFUNCTYPE(None, W.HANDLE, W.DWORD, W.HWND, W.LONG, W.LONG, W.DWORD, W.DWORD)


class MONITORINFOEXW(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT),
                ("dwFlags", W.DWORD), ("szDevice", W.WCHAR * 32)]


class WNDCLASSEXW(C.Structure):
    _fields_ = [("cbSize", W.UINT), ("style", W.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", C.c_int), ("cbWndExtra", C.c_int), ("hInstance", W.HINSTANCE),
                ("hIcon", W.HICON), ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH),
                ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR), ("hIconSm", W.HICON)]


class MSG(C.Structure):
    _fields_ = [("hwnd", W.HWND), ("message", W.UINT), ("wParam", W.WPARAM),
                ("lParam", W.LPARAM), ("time", W.DWORD), ("pt", W.POINT), ("lPrivate", W.DWORD)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(C.Structure):
    _fields_ = [("PerProcessUserTimeLimit", C.c_int64), ("PerJobUserTimeLimit", C.c_int64),
                ("LimitFlags", W.DWORD), ("MinimumWorkingSetSize", C.c_size_t),
                ("MaximumWorkingSetSize", C.c_size_t), ("ActiveProcessLimit", W.DWORD),
                ("Affinity", C.c_size_t), ("PriorityClass", W.DWORD), ("SchedulingClass", W.DWORD)]


class IO_COUNTERS(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(C.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", C.c_size_t),
                ("JobMemoryLimit", C.c_size_t), ("PeakProcessMemoryUsed", C.c_size_t),
                ("PeakJobMemoryUsed", C.c_size_t)]


def bind(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = list(arguments)
    return function


FindWindow = bind(user32, "FindWindowW", W.HWND, W.LPCWSTR, W.LPCWSTR)
FindWindowEx = bind(user32, "FindWindowExW", W.HWND, W.HWND, W.HWND, W.LPCWSTR, W.LPCWSTR)
EnumWindows = bind(user32, "EnumWindows", W.BOOL, ENUMPROC, W.LPARAM)
IsWindow = bind(user32, "IsWindow", W.BOOL, W.HWND)
IsWindowVisible = bind(user32, "IsWindowVisible", W.BOOL, W.HWND)
IsIconic = bind(user32, "IsIconic", W.BOOL, W.HWND)
IsZoomed = bind(user32, "IsZoomed", W.BOOL, W.HWND)
GetClassName = bind(user32, "GetClassNameW", C.c_int, W.HWND, W.LPWSTR, C.c_int)
GetWindowLongPtr = bind(user32, "GetWindowLongPtrW", C.c_ssize_t, W.HWND, C.c_int)
SetWindowLongPtr = bind(user32, "SetWindowLongPtrW", C.c_ssize_t, W.HWND, C.c_int, C.c_ssize_t)
GetWindowRect = bind(user32, "GetWindowRect", W.BOOL, W.HWND, C.POINTER(W.RECT))
GetClientRect = bind(user32, "GetClientRect", W.BOOL, W.HWND, C.POINTER(W.RECT))
GetParent = bind(user32, "GetParent", W.HWND, W.HWND)
GetForegroundWindow = bind(user32, "GetForegroundWindow", W.HWND)
SetForegroundWindow = bind(user32, "SetForegroundWindow", W.BOOL, W.HWND)
AllowSetForegroundWindow = bind(user32, "AllowSetForegroundWindow", W.BOOL, W.DWORD)
SetProp = bind(user32, "SetPropW", W.BOOL, W.HWND, W.LPCWSTR, W.HANDLE)
GetProp = bind(user32, "GetPropW", W.HANDLE, W.HWND, W.LPCWSTR)
RemoveProp = bind(user32, "RemovePropW", W.HANDLE, W.HWND, W.LPCWSTR)
ChangeWindowMessageFilterEx = bind(user32, "ChangeWindowMessageFilterEx", W.BOOL,
                                   W.HWND, W.UINT, W.DWORD, C.c_void_p)
GetWindowThreadProcessId = bind(user32, "GetWindowThreadProcessId", W.DWORD, W.HWND, C.POINTER(W.DWORD))
SendMessageTimeout = bind(user32, "SendMessageTimeoutW", LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM,
                          W.UINT, W.UINT, C.POINTER(C.c_size_t))
SetWindowPos = bind(user32, "SetWindowPos", W.BOOL, W.HWND, W.HWND, C.c_int, C.c_int,
                    C.c_int, C.c_int, W.UINT)
MapWindowPoints = bind(user32, "MapWindowPoints", C.c_int, W.HWND, W.HWND, C.POINTER(W.POINT), W.UINT)
EnumDisplayMonitors = bind(user32, "EnumDisplayMonitors", W.BOOL, W.HDC, C.POINTER(W.RECT), MONITORPROC, W.LPARAM)
GetMonitorInfo = bind(user32, "GetMonitorInfoW", W.BOOL, W.HMONITOR, C.POINTER(MONITORINFOEXW))
RegisterClassEx = bind(user32, "RegisterClassExW", W.ATOM, C.POINTER(WNDCLASSEXW))
UnregisterClass = bind(user32, "UnregisterClassW", W.BOOL, W.LPCWSTR, W.HINSTANCE)
CreateWindowEx = bind(user32, "CreateWindowExW", W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
                      C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID)
DestroyWindow = bind(user32, "DestroyWindow", W.BOOL, W.HWND)
DefWindowProc = bind(user32, "DefWindowProcW", LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
ShowWindow = bind(user32, "ShowWindow", W.BOOL, W.HWND, C.c_int)
PeekMessage = bind(user32, "PeekMessageW", W.BOOL, C.POINTER(MSG), W.HWND, W.UINT, W.UINT, W.UINT)
TranslateMessage = bind(user32, "TranslateMessage", W.BOOL, C.POINTER(MSG))
DispatchMessage = bind(user32, "DispatchMessageW", LRESULT, C.POINTER(MSG))
RedrawWindow = bind(user32, "RedrawWindow", W.BOOL, W.HWND, C.POINTER(W.RECT), W.HRGN, W.UINT)
RegisterWindowMessage = bind(user32, "RegisterWindowMessageW", W.UINT, W.LPCWSTR)
PostMessage = bind(user32, "PostMessageW", W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
SetWinEventHook = bind(user32, "SetWinEventHook", W.HANDLE, W.DWORD, W.DWORD, W.HMODULE,
                       WINEVENTPROC, W.DWORD, W.DWORD, W.DWORD)
UnhookWinEvent = bind(user32, "UnhookWinEvent", W.BOOL, W.HANDLE)
DwmGetWindowAttribute = bind(dwmapi, "DwmGetWindowAttribute", W.LONG, W.HWND, W.DWORD, W.LPVOID, W.DWORD)
GetStockObject = bind(gdi32, "GetStockObject", W.HANDLE, C.c_int)
GetModuleHandle = bind(kernel32, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)
GetCurrentProcessId = bind(kernel32, "GetCurrentProcessId", W.DWORD)
CreateJobObject = bind(kernel32, "CreateJobObjectW", W.HANDLE, W.LPVOID, W.LPCWSTR)
SetInformationJobObject = bind(kernel32, "SetInformationJobObject", W.BOOL, W.HANDLE, C.c_int, W.LPVOID, W.DWORD)
AssignProcessToJobObject = bind(kernel32, "AssignProcessToJobObject", W.BOOL, W.HANDLE, W.HANDLE)
CloseHandle = bind(kernel32, "CloseHandle", W.BOOL, W.HANDLE)
CreateMutex = bind(kernel32, "CreateMutexW", W.HANDLE, W.LPVOID, W.BOOL, W.LPCWSTR)
WTSRegisterSessionNotification = bind(wtsapi32, "WTSRegisterSessionNotification", W.BOOL, W.HWND, W.DWORD)
WTSUnRegisterSessionNotification = bind(wtsapi32, "WTSUnRegisterSessionNotification", W.BOOL, W.HWND)


def check(result, description: str = "Windows 操作失败"):
    if not result:
        raise OSError(C.get_last_error(), description)
    return result


def class_name(hwnd: int) -> str:
    buffer = C.create_unicode_buffer(256)
    GetClassName(hwnd, buffer, len(buffer))
    return buffer.value


def window_pid(hwnd: int) -> int:
    pid = W.DWORD()
    GetWindowThreadProcessId(hwnd, C.byref(pid))
    return pid.value


def window_list() -> list[int]:
    result = []

    @ENUMPROC
    def callback(hwnd, _):
        result.append(int(hwnd))
        return True

    check(EnumWindows(callback, 0), "无法枚举桌面窗口")
    return result


class JobObject:
    def __init__(self):
        self.handle = check(CreateJobObject(None, None), "无法创建播放器进程管理对象")
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            check(SetInformationJobObject(self.handle, 9, C.byref(limits), C.sizeof(limits)),
                  "无法设置播放器进程退出策略")
        except Exception:
            self.close()
            raise

    def assign(self, process) -> None:
        check(AssignProcessToJobObject(self.handle, int(process._handle)), "无法绑定播放器子进程")

    def close(self) -> None:
        if self.handle:
            CloseHandle(self.handle)
            self.handle = None


class SingleInstance:
    MESSAGE_NAME = "LocalDynamicWallpaper.ShowMain.v1"
    MUTEX_NAME = "Local\\LocalDynamicWallpaper.v1"
    WINDOW_PROPERTY = "LocalDynamicWallpaper.MainWindow.v1"
    WINDOW_TITLE = "本地视频动态壁纸"

    def __init__(self):
        self.message = check(RegisterWindowMessage(self.MESSAGE_NAME), "无法注册单实例通知消息")
        self.window = None
        C.set_last_error(0)
        self.handle = check(CreateMutex(None, False, self.MUTEX_NAME))
        self.already_running = C.get_last_error() == 183

    def register_window(self, hwnd: int) -> None:
        check(SetProp(hwnd, self.WINDOW_PROPERTY, os.getpid()), "无法注册主窗口标识")
        self.window = hwnd
        # Only this payload-free show request is allowed across integrity levels.
        ChangeWindowMessageFilterEx(hwnd, self.message, 1, None)  # MSGFLT_ALLOW

    def _find_window(self) -> tuple[int, int] | None:
        for hwnd in window_list():
            marker = GetProp(hwnd, self.WINDOW_PROPERTY)
            if marker and marker == window_pid(hwnd):
                return hwnd, int(marker)
        # Older builds do not have the property but understand the same registered show message.
        hwnd = FindWindow(None, self.WINDOW_TITLE)
        if hwnd and class_name(hwnd).startswith("Qt"):
            return int(hwnd), window_pid(hwnd)
        return None

    def notify(self, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            target = self._find_window()
            if target:
                hwnd, pid = target
                # The desktop-launched process transfers its foreground activation permission to the owner.
                AllowSetForegroundWindow(pid)
                result = C.c_size_t()
                remaining = max(1, min(1000, int((deadline - time.monotonic()) * 1000)))
                if (SendMessageTimeout(hwnd, self.message, 0, 0, 2, remaining, C.byref(result))
                        and result.value == 1):
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    def close(self):
        if self.window and IsWindow(self.window) and GetProp(self.window, self.WINDOW_PROPERTY) == os.getpid():
            RemoveProp(self.window, self.WINDOW_PROPERTY)
        self.window = None
        if self.handle:
            CloseHandle(self.handle)
            self.handle = None
