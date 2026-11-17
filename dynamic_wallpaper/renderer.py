"""Renderer entry point. Wait for Job Object assignment before loading any media."""

import hashlib
import json
import logging
import queue
import sys
import threading
import time
from pathlib import Path

from .models import DisplayMode, Priority, Rect, validate_path, validate_volume
from .mpv import Mpv, MpvError, find_library
from .storage import configure_logging


log = logging.getLogger(__name__)


def send(event: str, **payload) -> None:
    sys.stdout.write(json.dumps({"event": event, **payload}, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def read_commands(commands: queue.Queue) -> None:
    try:
        for line in sys.stdin:
            if len(line) > 1_048_576:
                raise ValueError("进程控制消息过长。")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("进程控制消息格式错误。")
            commands.put(value)
    except Exception as exc:
        commands.put({"command": "protocol_error", "message": str(exc)})
    finally:
        commands.put({"command": "stop"})


def renderer_main() -> int:
    player = window = None
    try:
        # Parent writes boot only after the process is bound to its kill-on-close Job.
        boot_line = sys.stdin.readline(1_048_577)
        if not boot_line:
            return 0
        if len(boot_line) > 1_048_576:
            raise ValueError("播放器启动参数过长。")
        boot = json.loads(boot_line)
        if not isinstance(boot, dict) or boot.get("command") != "boot":
            raise ValueError("播放器启动协议错误。")
        spec = boot["spec"]
        suffix = hashlib.sha256(str(spec.get("device", "probe")).encode()).hexdigest()[:12]
        configure_logging(Path(spec["log_directory"]), f"renderer-{suffix}")
        commands = queue.Queue(maxsize=1024)
        threading.Thread(target=read_commands, args=(commands,), daemon=True, name="renderer-input").start()
        path = validate_path(spec["video"])
        library = find_library(spec.get("library"))
        probe = bool(spec.get("probe"))
        display_mode = DisplayMode(spec.get("display_mode", "cover"))
        if not probe:
            from .desktop import NativeVideoWindow
            window = NativeVideoWindow(int(spec["parent"]), Rect(**spec["bounds"]), display_mode)
        player = Mpv(library, int(window.viewport) if window else None, probe)
        volume = validate_volume(spec.get("volume", 100))
        if not probe:
            player.set_volume(volume)
        paused = spec.get("paused") is True and not probe
        if paused:
            player.set_paused(True)
        start = float(spec.get("position", 0))
        player.command("loadfile", str(path), "replace")
        send("hello")
        deadline = time.monotonic() + (12 if probe else 18)
        heartbeat = time.monotonic()
        ready = False
        restarted = False
        restart_count = 0
        file_loaded = False
        shown = False
        while True:
            if window:
                window.pump()
            while True:
                try:
                    command = commands.get_nowait()
                except queue.Empty:
                    break
                action = command.get("command")
                if action == "stop":
                    return 0
                if action == "protocol_error":
                    raise ValueError(command["message"])
                if action == "show" and window:
                    window.show(Priority(command["priority"]))
                    shown = True
                    send("shown")
                elif action == "hide" and window:
                    player.set_audio(False)
                    shown = False
                    window.hide()
                    send("audio_state", enabled=False)
                elif action == "pause" and window:
                    paused = command.get("paused") is True
                    player.set_paused(paused)
                elif action == "volume" and window:
                    volume = validate_volume(command["value"])
                    player.set_volume(volume)
                elif action == "audio" and window:
                    enabled = command.get("enabled") is True and shown and not paused
                    if enabled:
                        volume = validate_volume(command.get("volume", volume))
                        player.set_volume(volume)
                    player.set_audio(enabled)
                    send("audio_state", enabled=enabled)
                elif action == "priority" and window:
                    window.set_priority(Priority(command["priority"]))
                elif action == "display_mode" and window:
                    display_mode = DisplayMode(command["mode"])
                    window.set_display_mode(display_mode)
                    player.set_display_mode(display_mode, window.bounds.width, window.bounds.height)
                elif action == "geometry" and window:
                    window.resize(Rect(**command["bounds"]))
                    player.set_display_mode(display_mode, window.bounds.width, window.bounds.height)
            for _ in range(128):
                event_id, error, end = player.event()
                if event_id == Mpv.NONE:
                    break
                if error < 0:
                    player._check(error, "播放器事件")
                if event_id == Mpv.SHUTDOWN:
                    raise MpvError("视频播放器意外关闭。")
                if event_id == Mpv.FILE_LOADED:
                    file_loaded = True
                if event_id == Mpv.PLAYBACK_RESTART:
                    restarted = True
                    restart_count += 1
                if event_id == Mpv.VIDEO_RECONFIG and window:
                    size = player.video_size()
                    if size:
                        window.set_video_size(*size)
                        player.set_display_mode(display_mode, window.bounds.width, window.bounds.height)
                if event_id == Mpv.END_FILE and end:
                    reason, error = end
                    if error < 0 or reason == 4:
                        player._check(error if error < 0 else -13, "解码视频")
                    if reason not in {0, 2}:
                        raise MpvError("视频播放异常结束。")
                    if not ready:
                        raise MpvError("视频没有可解码的视频轨道或首帧无法输出。")
            size = player.video_size()
            if not ready and file_loaded and restarted and size and player.flag("vo-configured"):
                if start > 0 and not probe:
                    player.command("seek", f"{start:.3f}", "absolute+keyframes")
                    start = 0
                    restarted = False
                    continue
                if window:
                    window.set_video_size(*size)
                    player.set_display_mode(display_mode, window.bounds.width, window.bounds.height)
                ready = True
                send("ready", width=size[0], height=size[1], hwdec=player.string("hwdec-current"))
                if probe:
                    return 0
            now = time.monotonic()
            if not ready and now >= deadline:
                raise MpvError("视频加载超时，可能没有有效的视频轨道或当前设备无法解码。")
            if now >= heartbeat:
                if window:
                    from . import win32 as win
                    from .desktop import DesktopHost
                    if not DesktopHost.valid(window.parent) or not win.IsWindow(window.container):
                        raise MpvError("Explorer 桌面宿主已失效。")
                send("status", position=player.number("time-pos"), restart_count=restart_count,
                     hwdec=player.string("hwdec-current"))
                heartbeat = now + 1
            time.sleep(0.01 if not ready else 0.025)
    except Exception as exc:
        log.exception("视频进程失败")
        try:
            send("error", message=str(exc))
        except (OSError, ValueError):
            pass
        return 1
    finally:
        # Destroy the decoder before the HWND it renders into.
        try:
            if player:
                player.close()
        finally:
            if window:
                window.close()
