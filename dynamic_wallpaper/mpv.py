"""Small libmpv client binding; no Qt dependency in renderer processes."""

import ctypes as C
import hashlib
import os
import struct
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from .models import DisplayMode, validate_volume
from .shaders import center_shader, contain_shader, tile_shader


class MpvError(RuntimeError):
    pass


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def find_library(explicit: str | None = None) -> Path:
    if explicit:
        candidates = [Path(explicit)]
    elif os.environ.get("DYNAMIC_WALLPAPER_MPV_DLL"):
        candidates = [Path(os.environ["DYNAMIC_WALLPAPER_MPV_DLL"])]
    else:
        folders = [application_directory() / "vendor" / "mpv"]
        if getattr(sys, "frozen", False):
            folders.append(Path(sys.executable).parent / "vendor" / "mpv")
        candidates = [folder / name for folder in folders for name in ("mpv-2.dll", "libmpv-2.dll", "mpv-1.dll")]
    for candidate in candidates:
        if candidate.is_file():
            path = candidate.resolve()
            with path.open("rb") as stream:
                if stream.read(2) != b"MZ":
                    raise MpvError("mpv 运行库不是有效的 Windows DLL。")
                stream.seek(0x3C)
                offset_data = stream.read(4)
                if len(offset_data) != 4:
                    raise MpvError("mpv 运行库文件损坏。")
                stream.seek(struct.unpack("<I", offset_data)[0])
                header = stream.read(6)
                if header[:4] != b"PE\0\0" or header[4:] != b"\x64\x86":
                    raise MpvError("请使用 Windows x64 版本的 libmpv 运行库。")
            return path
    raise MpvError("缺少 libmpv：请将 x64 mpv-2.dll 及其依赖放到 vendor/mpv，或使用 --mpv-library 指定路径。")


class MpvEvent(C.Structure):
    _fields_ = [("event_id", C.c_int), ("error", C.c_int), ("reply_userdata", C.c_uint64), ("data", C.c_void_p)]


class EndFile(C.Structure):
    _fields_ = [("reason", C.c_int), ("error", C.c_int), ("playlist_entry_id", C.c_int64),
                ("playlist_insert_id", C.c_int64), ("playlist_insert_num_entries", C.c_int)]


class Mpv:
    NONE, SHUTDOWN, START_FILE, END_FILE, FILE_LOADED = 0, 1, 6, 7, 8
    VIDEO_RECONFIG, PLAYBACK_RESTART = 17, 21

    def __init__(self, library: Path, hwnd: int | None = None, probe: bool = False):
        self.handle = None
        self._shader_directory = None
        self._display_key = None
        self._probe = probe
        self._volume = 100
        self._audio_enabled = False
        self._audio_opened = False
        self._directory = os.add_dll_directory(str(library.parent))
        try:
            # libmpv uses the C calling convention, not WinAPI stdcall.
            self.lib = C.CDLL(str(library), winmode=0x00000100 | 0x00001000)
            signatures = {
                "mpv_client_api_version": (C.c_ulong, []),
                "mpv_create": (C.c_void_p, []),
                "mpv_initialize": (C.c_int, [C.c_void_p]),
                "mpv_set_option_string": (C.c_int, [C.c_void_p, C.c_char_p, C.c_char_p]),
                "mpv_set_property_string": (C.c_int, [C.c_void_p, C.c_char_p, C.c_char_p]),
                "mpv_get_property": (C.c_int, [C.c_void_p, C.c_char_p, C.c_int, C.c_void_p]),
                "mpv_get_property_string": (C.c_void_p, [C.c_void_p, C.c_char_p]),
                "mpv_command": (C.c_int, [C.c_void_p, C.POINTER(C.c_char_p)]),
                "mpv_wait_event": (C.POINTER(MpvEvent), [C.c_void_p, C.c_double]),
                "mpv_error_string": (C.c_char_p, [C.c_int]),
                "mpv_free": (None, [C.c_void_p]),
                "mpv_terminate_destroy": (None, [C.c_void_p]),
            }
            for name, (result, arguments) in signatures.items():
                function = getattr(self.lib, name)
                function.restype, function.argtypes = result, arguments
            version = self.lib.mpv_client_api_version()
            if version >> 16 not in {1, 2}:
                raise MpvError(f"不兼容的 libmpv 客户端 API：{version >> 16}.{version & 0xFFFF}")
            self.handle = self.lib.mpv_create()
            if not self.handle:
                raise MpvError("无法创建视频播放器。")
            options = {
                "config": "no", "load-scripts": "no", "ytdl": "no",
                "audio": "no", "mute": "yes", "sub": "no", "sub-auto": "no", "audio-file-auto": "no",
                "input-default-bindings": "no", "input-vo-keyboard": "no", "osc": "no",
                "idle": "yes", "terminal": "no", "cache": "no", "keepaspect": "yes", "panscan": "0",
                "access-references": "no", "demuxer": "lavf",
                "demuxer-lavf-o": "protocol_whitelist=file",
            }
            if probe:
                options.update({"vo": "null", "hwdec": "no", "vd-lavc-threads": "2"})
            else:
                if not hwnd:
                    raise MpvError("缺少视频输出窗口。")
                options.update({"wid": str(hwnd), "vo": "gpu", "gpu-api": "d3d11", "gpu-context": "d3d11",
                                "hwdec": "auto-safe", "loop-file": "inf", "force-window": "yes"})
            for name, value in options.items():
                self._check(self.lib.mpv_set_option_string(self.handle, name.encode(), value.encode()),
                            f"播放器选项 {name}")
            self._check(self.lib.mpv_initialize(self.handle), "初始化播放器")
        except Exception:
            self.close()
            raise

    def _check(self, result: int, operation: str) -> None:
        if result < 0:
            error = self.lib.mpv_error_string(result).decode("utf-8", errors="replace")
            raise MpvError(f"{operation}失败：{error}")

    def command(self, *values: str) -> None:
        arguments = (C.c_char_p * (len(values) + 1))(*(value.encode("utf-8") for value in values), None)
        self._check(self.lib.mpv_command(self.handle, arguments), "视频命令")

    def set_property(self, name: str, value: str) -> None:
        self._check(self.lib.mpv_set_property_string(self.handle, name.encode(), value.encode("utf-8")),
                    f"播放器属性 {name}")

    def set_paused(self, paused: bool) -> None:
        self.set_property("pause", "yes" if paused and not self._probe else "no")

    def set_volume(self, value: int) -> None:
        self._volume = validate_volume(value)
        self.set_property("volume", str(self._volume))

    def _fade_volume(self, start: int, end: int) -> None:
        if start == end:
            return
        for step in range(1, 7):
            value = start + (end - start) * step / 6
            self.set_property("volume", f"{value:.3f}")
            time.sleep(0.012)

    def set_audio(self, enabled: bool) -> None:
        volume = getattr(self, "_volume", 100)
        if enabled and not self._probe:
            if getattr(self, "_audio_enabled", False):
                return
            self.set_property("volume", "0")
            if not getattr(self, "_audio_opened", False):
                self.set_property("aid", "auto")
                self._audio_opened = True
            self.set_property("mute", "no")
            self._fade_volume(0, volume)
            self._audio_enabled = True
        else:
            if getattr(self, "_audio_enabled", False):
                self._fade_volume(volume, 0)
                # Allow queued fading samples to reach the shared output before muting the session.
                buffer = self.number("options/audio-buffer")
                time.sleep(min(0.25, max(0.0, buffer if buffer is not None else 0.2)))
            self.set_property("mute", "yes")
            if self._probe or not getattr(self, "_audio_opened", False):
                self.set_property("aid", "no")
            else:
                # Keep the selected stream alive: closing WASAPI on each checkbox change can pop on hardware.
                self.set_property("volume", str(volume))
            self._audio_enabled = False


    def set_display_mode(self, mode: DisplayMode, width: int, height: int) -> None:
        mode = DisplayMode(mode)
        size = self.video_size()
        rotation = int(self.number("video-out-params/rotate") or 0) % 360
        key = mode, width, height, size, rotation
        if key == self._display_key:
            return
        source = None
        if size:
            if mode == DisplayMode.TILE:
                source = tile_shader(width, height, *size)
            elif mode == DisplayMode.CONTAIN:
                source = contain_shader(width, height, *size)
            elif mode == DisplayMode.CENTER:
                source = center_shader(width, height, *size)
        self.set_property("keepaspect", "no" if source or mode in {DisplayMode.STRETCH, DisplayMode.TILE} else "yes")
        if source:
            if self._shader_directory is None:
                self._shader_directory = TemporaryDirectory(prefix="local-wallpaper-shader-")
            digest = hashlib.sha256(source.encode()).hexdigest()[:16]
            shader = Path(self._shader_directory.name) / f"{mode}-{digest}.glsl"
            if not shader.exists():
                shader.write_text(source, encoding="utf-8")
            # Content-addressed paths avoid mpv retaining a cached shader during asynchronous resizes.
            self.set_property("glsl-shaders", str(shader))
        else:
            self.set_property("glsl-shaders", "")
        self._display_key = key

    def string(self, name: str) -> str | None:
        pointer = self.lib.mpv_get_property_string(self.handle, name.encode())
        if not pointer:
            return None
        try:
            return C.string_at(pointer).decode("utf-8", errors="replace")
        finally:
            self.lib.mpv_free(pointer)

    def number(self, name: str) -> float | None:
        value = C.c_double()
        if self.lib.mpv_get_property(self.handle, name.encode(), 5, C.byref(value)) < 0:
            return None
        return value.value

    def flag(self, name: str) -> bool:
        value = C.c_int()
        result = self.lib.mpv_get_property(self.handle, name.encode(), 3, C.byref(value))
        return result >= 0 and bool(value.value)

    def video_size(self) -> tuple[int, int] | None:
        width = self.number("video-out-params/dw") or self.number("video-params/dw")
        height = self.number("video-out-params/dh") or self.number("video-params/dh")
        if not width or not height or min(width, height) <= 0:
            return None
        # video-out-params describes frames after filters; residual rotation is applied by VO.
        rotation = self.number("video-out-params/rotate") or 0
        if int(rotation) % 180:
            width, height = height, width
        return int(width), int(height)

    def event(self) -> tuple[int, int, tuple[int, int] | None]:
        pointer = self.lib.mpv_wait_event(self.handle, 0)
        event = pointer.contents
        end = None
        if event.event_id == self.END_FILE and event.data:
            payload = C.cast(event.data, C.POINTER(EndFile)).contents
            end = payload.reason, payload.error
        return event.event_id, event.error, end

    def close(self) -> None:
        if self.handle:
            self.lib.mpv_terminate_destroy(self.handle)
            self.handle = None
        directory = getattr(self, "_directory", None)
        if directory:
            directory.close()
            self._directory = None
        shader_directory = getattr(self, "_shader_directory", None)
        if shader_directory:
            shader_directory.cleanup()
            self._shader_directory = None
