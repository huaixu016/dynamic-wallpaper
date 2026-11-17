import ctypes as C
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Priority(StrEnum):
    HIGH = "high"
    LOW = "low"


class DisplayMode(StrEnum):
    COVER = "cover"
    CONTAIN = "contain"
    STRETCH = "stretch"
    CENTER = "center"
    TILE = "tile"


class CoverageKind(StrEnum):
    MAXIMIZED = "maximized"
    FULLSCREEN = "fullscreen"


class PlaybackAction(StrEnum):
    KEEP = "keep"
    PAUSE = "pause"
    STOP = "stop"


def validate_volume(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError("音量必须是 0～100 的整数。")
    return value


class State(StrEnum):
    EMPTY = "empty"
    READY = "ready"
    LOADING = "loading"
    RUNNING = "running"
    PAUSED = "paused"
    SUSPENDED = "suspended"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def covers(self, other: "Rect", tolerance: int = 2) -> bool:
        return (
            self.left <= other.left + tolerance
            and self.top <= other.top + tolerance
            and self.right >= other.right - tolerance
            and self.bottom >= other.bottom - tolerance
        )

    def to_dict(self) -> dict:
        return dict(left=self.left, top=self.top, right=self.right, bottom=self.bottom)


@dataclass(frozen=True)
class Monitor:
    device: str
    bounds: Rect
    work: Rect
    primary: bool = False

    def to_dict(self) -> dict:
        return {
            "device": self.device,
            "bounds": self.bounds.to_dict(),
            "work": self.work.to_dict(),
            "primary": self.primary,
        }


VIDEO_SUFFIXES = frozenset({".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi"})


def _network_path(path: Path) -> bool:
    if str(path).startswith(("\\\\", "//")):
        return True
    if os.name == "nt" and path.drive:
        function = C.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
        function.argtypes, function.restype = [C.c_wchar_p], C.c_uint
        return function(path.drive.rstrip("\\/") + "\\") == 4  # DRIVE_REMOTE
    return False


def validate_path(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if _network_path(raw):
        raise ValueError("仅支持本机视频文件，不支持网络共享路径。")
    try:
        path = raw.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("视频文件不存在，请重新选择。") from exc
    if _network_path(path):
        raise ValueError("仅支持本机视频文件，不支持网络共享路径。")
    if not path.is_file():
        raise ValueError("请选择本地视频文件，而不是文件夹。")
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        raise ValueError("不支持该文件类型，建议选择 MP4（H.264）视频。")
    try:
        if path.stat().st_size == 0:
            raise ValueError("视频文件为空，请重新选择。")
    except FileNotFoundError as exc:
        raise ValueError("视频文件不存在，请重新选择。") from exc
    return path


def cover_geometry(width: int, height: int, video_width: int, video_height: int) -> tuple[int, int, int, int]:
    """Use integer arithmetic so the viewport is completely covered without stretch."""
    if min(width, height, video_width, video_height) <= 0:
        raise ValueError("屏幕和视频的尺寸必须大于零。")
    if width * video_height >= height * video_width:
        output_width = width
        output_height = (width * video_height + video_width - 1) // video_width
    else:
        output_height = height
        output_width = (height * video_width + video_height - 1) // video_height
    return ((width - output_width) // 2, (height - output_height) // 2, output_width, output_height)


def display_geometry(width: int, height: int, video_width: int, video_height: int,
                     mode: DisplayMode) -> tuple[int, int, int, int]:
    mode = DisplayMode(mode)
    if min(width, height, video_width, video_height) <= 0:
        raise ValueError("屏幕和视频的尺寸必须大于零。")
    if mode == DisplayMode.COVER:
        return cover_geometry(width, height, video_width, video_height)
    if mode in {DisplayMode.STRETCH, DisplayMode.TILE}:
        return 0, 0, width, height
    if mode == DisplayMode.CENTER:
        output_width, output_height = video_width, video_height
    elif width * video_height <= height * video_width:
        output_width = width
        output_height = max(1, width * video_height // video_width)
    else:
        output_height = height
        output_width = max(1, height * video_width // video_height)
    return ((width - output_width) // 2, (height - output_height) // 2, output_width, output_height)


def viewport_geometry(width: int, height: int, video_width: int, video_height: int,
                      mode: DisplayMode) -> tuple[int, int, int, int]:
    mode = DisplayMode(mode)
    geometry = display_geometry(width, height, video_width, video_height, mode)
    if ((mode == DisplayMode.CONTAIN and geometry[2] < width)
            or (mode == DisplayMode.CENTER and (geometry[2] < width or geometry[3] < height))):
        # The GPU composes the main frame and its blurred edge fill in one full-screen viewport.
        return 0, 0, width, height
    return geometry
