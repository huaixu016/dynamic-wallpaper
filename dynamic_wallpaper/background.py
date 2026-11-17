"""Read-only IDesktopWallpaper snapshot. Playback never changes these settings."""

import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
from uuid import UUID

from .win32 import bind

ole32 = C.WinDLL("ole32", use_last_error=True)
CoInitializeEx = bind(ole32, "CoInitializeEx", W.LONG, W.LPVOID, W.DWORD)
CoUninitialize = bind(ole32, "CoUninitialize", None)
CoTaskMemFree = bind(ole32, "CoTaskMemFree", None, W.LPVOID)


class GUID(C.Structure):
    _fields_ = [("Data1", W.DWORD), ("Data2", W.WORD), ("Data3", W.WORD), ("Data4", W.BYTE * 8)]

    @classmethod
    def parse(cls, value: str) -> "GUID":
        return cls.from_buffer_copy(UUID(value).bytes_le)


CoCreateInstance = bind(ole32, "CoCreateInstance", W.LONG, C.POINTER(GUID), W.LPVOID, W.DWORD,
                        C.POINTER(GUID), C.POINTER(C.c_void_p))


def check_hresult(value: int, description: str) -> int:
    if value < 0:
        raise OSError(f"{description}（HRESULT 0x{value & 0xFFFFFFFF:08X}）")
    return value


class ComObject:
    def __init__(self, pointer):
        self.pointer = C.c_void_p(pointer.value if isinstance(pointer, C.c_void_p) else pointer)

    def call(self, index, arguments, *values):
        table = C.cast(self.pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
        function = C.WINFUNCTYPE(W.LONG, C.c_void_p, *arguments)(table[index])
        return function(self.pointer, *values)

    def text(self, index, arguments=(), *values):
        output = C.c_void_p()
        try:
            check_hresult(self.call(index, (*arguments, C.POINTER(C.c_void_p)), *values, C.byref(output)),
                          "无法读取系统背景路径")
            return C.wstring_at(output.value) if output.value else ""
        finally:
            if output.value:
                CoTaskMemFree(output)

    def close(self):
        if self.pointer.value:
            self.call(2, ())
            self.pointer = C.c_void_p()


def capture_background() -> dict:
    initialized = CoInitializeEx(None, 2)
    if initialized < 0 and (initialized & 0xFFFFFFFF) != 0x80010106:
        check_hresult(initialized, "无法初始化系统背景接口")
    wallpaper = None
    try:
        pointer = C.c_void_p()
        clsid = GUID.parse("C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD")
        iid = GUID.parse("B92B56A9-8B55-4E14-9A89-0199BBB6F93B")
        check_hresult(CoCreateInstance(C.byref(clsid), None, 5, C.byref(iid), C.byref(pointer)),
                      "无法访问 Windows 桌面背景配置")
        wallpaper = ComObject(pointer)
        count = W.UINT()
        check_hresult(wallpaper.call(6, (C.POINTER(W.UINT),), C.byref(count)), "无法读取背景显示器数量")
        monitors = []
        for index in range(count.value):
            device = wallpaper.text(5, (W.UINT,), index)
            path = wallpaper.text(4, (W.LPCWSTR,), device)
            monitors.append({"device": device, "wallpaper": path})
        color, position, status = W.DWORD(), W.UINT(), W.UINT()
        check_hresult(wallpaper.call(9, (C.POINTER(W.DWORD),), C.byref(color)), "无法读取背景颜色")
        check_hresult(wallpaper.call(11, (C.POINTER(W.UINT),), C.byref(position)), "无法读取背景布局")
        check_hresult(wallpaper.call(17, (C.POINTER(W.UINT),), C.byref(status)), "无法读取背景状态")
        options, interval = W.UINT(), W.UINT()
        check_hresult(wallpaper.call(15, (C.POINTER(W.UINT), C.POINTER(W.UINT)),
                                     C.byref(options), C.byref(interval)), "无法读取幻灯片设置")
        slideshow = []
        array_pointer = C.c_void_p()
        result = wallpaper.call(13, (C.POINTER(C.c_void_p),), C.byref(array_pointer))
        if result >= 0 and array_pointer.value:
            array = ComObject(array_pointer)
            try:
                item_count = W.DWORD()
                check_hresult(array.call(7, (C.POINTER(W.DWORD),), C.byref(item_count)), "无法读取幻灯片来源")
                for index in range(item_count.value):
                    item_pointer = C.c_void_p()
                    check_hresult(array.call(8, (W.DWORD, C.POINTER(C.c_void_p)), index, C.byref(item_pointer)),
                                  "无法读取幻灯片条目")
                    item = ComObject(item_pointer)
                    try:
                        slideshow.append(item.text(5, (W.UINT,), 0x80028000))
                    finally:
                        item.close()
            finally:
                array.close()
        elif status.value & 2:
            check_hresult(result, "无法保存正在使用的幻灯片来源")
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "monitors": monitors,
            "color": color.value,
            "position": position.value,
            "status": status.value,
            "slideshow": {"sources": slideshow, "options": options.value, "interval_ms": interval.value},
        }
    finally:
        if wallpaper:
            wallpaper.close()
        if initialized >= 0:
            CoUninitialize()
