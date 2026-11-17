import json
import logging
import logging.handlers
import os
import tempfile
from pathlib import Path

from .models import DisplayMode, PlaybackAction, Priority, validate_volume


log = logging.getLogger(__name__)


def data_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "LocalDynamicWallpaper"


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class ConfigStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or data_directory()
        self.path = self.directory / "config.json"
        self.video: str | None = None
        self.priority = Priority.LOW
        self.display_mode = DisplayMode.COVER
        self.audio_enabled = False
        self.volume = 100
        self.maximized_action = self.fullscreen_action = PlaybackAction.STOP
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("配置根节点不是对象")
            video = data.get("last_video")
            if isinstance(video, str) and video:
                self.video = video
            self.priority = Priority(data.get("priority", "low"))
            try:
                self.display_mode = DisplayMode(data.get("display_mode", "cover"))
            except (ValueError, TypeError):
                log.warning("显示方式无效，使用等比填充")
            audio = data.get("audio_enabled", False)
            if isinstance(audio, bool):
                self.audio_enabled = audio
            try:
                self.volume = validate_volume(data.get("volume", 100))
            except ValueError:
                log.warning("音量无效，使用 100%")
            for field in ("maximized_action", "fullscreen_action"):
                try:
                    setattr(self, field, PlaybackAction(data.get(field, "stop")))
                except (ValueError, TypeError):
                    log.warning("自动策略 %s 无效，使用停止", field)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            log.warning("配置无法读取，使用安全默认值", exc_info=True)
            self.priority = Priority.LOW

    def save(self, video: str | None, priority: Priority, display_mode: DisplayMode = DisplayMode.COVER,
             audio_enabled: bool = False, volume: int = 100,
             maximized_action: PlaybackAction = PlaybackAction.STOP,
             fullscreen_action: PlaybackAction = PlaybackAction.STOP) -> None:
        volume = validate_volume(volume)
        maximized_action, fullscreen_action = PlaybackAction(maximized_action), PlaybackAction(fullscreen_action)
        atomic_json(self.path, {"version": 3, "last_video": video, "priority": str(priority),
                               "display_mode": str(display_mode), "audio_enabled": audio_enabled,
                               "volume": volume, "maximized_action": str(maximized_action),
                               "fullscreen_action": str(fullscreen_action)})
        self.video, self.priority = video, priority
        self.display_mode, self.audio_enabled = display_mode, audio_enabled
        self.volume = volume
        self.maximized_action, self.fullscreen_action = maximized_action, fullscreen_action

    def begin_session(self, snapshot: dict) -> None:
        atomic_json(self.directory / "session.json", {
            "version": 1,
            "changes_system_wallpaper": False,
            "snapshot": snapshot,
        })

    def recover_session(self) -> bool:
        """Remove only records that prove this version never changed system wallpaper settings."""
        path = self.directory / "session.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("changes_system_wallpaper") is not False:
                log.warning("保留无法安全确认的旧会话记录：%s", path)
                return False
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except (OSError, ValueError, TypeError):
            log.warning("无法清理旧会话记录：%s", path, exc_info=True)
            return False

    def end_session(self) -> None:
        (self.directory / "session.json").unlink(missing_ok=True)


def configure_logging(directory: Path, name: str = "application") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        directory / f"{name}.log", maxBytes=1_048_576, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
