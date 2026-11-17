import json
import logging
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .mpv import application_directory

log = logging.getLogger(__name__)


def renderer_arguments() -> list[str]:
    if getattr(sys, "frozen", False):
        helper = Path(sys.executable).with_name("LocalDynamicWallpaperRenderer.exe")
        if not helper.is_file():
            raise RuntimeError("安装不完整：缺少 LocalDynamicWallpaperRenderer.exe。")
        return [str(helper), "--renderer"]
    return [sys.executable, "-m", "dynamic_wallpaper", "--renderer"]


@dataclass
class ProcessRecord:
    process: subprocess.Popen
    started: float
    heartbeat: float
    ready: bool = False
    stopping: float | None = None
    failure_reported: bool = False
    reader: threading.Thread | None = None


class RendererPool:
    START_TIMEOUT = 22.0
    HEARTBEAT_TIMEOUT = 8.0
    STOP_TIMEOUT = 3.0

    def __init__(self, job, notify=lambda: None, clock=time.monotonic):
        self.job, self.notify, self.clock = job, notify, clock
        self.records: dict[str, ProcessRecord] = {}
        self.events = queue.Queue()

    def _emit(self, token: str, event: str, **payload) -> None:
        self.events.put({"token": token, "event": event, **payload})
        self.notify()

    def spawn(self, spec: dict) -> str:
        token = uuid.uuid4().hex
        arguments = renderer_arguments()
        process = subprocess.Popen(
            arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="strict", bufsize=1,
            cwd=str(application_directory()), creationflags=0x08000000,
        )
        try:
            self.job.assign(process)
        except Exception:
            process.kill()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            raise
        now = self.clock()
        record = ProcessRecord(process, now, now)
        self.records[token] = record
        record.reader = threading.Thread(target=self._read, args=(token, process), daemon=True,
                                         name=f"renderer-{token[:8]}")
        record.reader.start()
        if not self.command(token, command="boot", spec=spec):
            self.stop(token)
            raise RuntimeError("无法向播放器发送启动参数。")
        return token

    def _read(self, token: str, process) -> None:
        try:
            while True:
                line = process.stdout.readline(1_048_577)
                if not line:
                    break
                if len(line) > 1_048_576:
                    raise ValueError("播放器返回了过长的控制消息。")
                value = json.loads(line)
                if not isinstance(value, dict) or not isinstance(value.get("event"), str):
                    raise ValueError("播放器控制消息格式错误。")
                value.pop("token", None)
                event = value.pop("event")
                self._emit(token, event, **value)
        except Exception as exc:
            self._emit(token, "error", message=f"播放器通信失败：{exc}")
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
        finally:
            code = process.wait()
            self._emit(token, "exited", code=code)

    def command(self, token: str, **message) -> bool:
        record = self.records.get(token)
        if record is None or record.process.poll() is not None:
            return False
        try:
            record.process.stdin.write(json.dumps(message, ensure_ascii=True) + "\n")
            record.process.stdin.flush()
            return True
        except (OSError, ValueError):
            self._emit(token, "error", message="播放器控制管道已关闭。")
            return False

    def stop(self, token: str) -> None:
        record = self.records.get(token)
        if record is not None and record.stopping is None:
            record.stopping = self.clock()
            self.command(token, command="stop")

    def drain(self) -> list[dict]:
        result = []
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            record = self.records.get(event["token"])
            if record:
                if event["event"] in {"hello", "ready", "shown", "status"}:
                    record.heartbeat = self.clock()
                if event["event"] == "ready":
                    record.ready = True
                if event["event"] == "exited":
                    self.records.pop(event["token"], None)
                    record.process.stdin.close()
                    record.process.stdout.close()
            result.append(event)
        return result

    def tick(self) -> None:
        now = self.clock()
        for token, record in list(self.records.items()):
            if record.stopping is not None:
                if now - record.stopping >= self.STOP_TIMEOUT and record.process.poll() is None:
                    log.warning("播放器退出超时，终止进程 %s", record.process.pid)
                    record.process.kill()
                continue
            if record.failure_reported:
                continue
            startup_expired = not record.ready and now - record.started > self.START_TIMEOUT
            heartbeat_expired = record.ready and now - record.heartbeat > self.HEARTBEAT_TIMEOUT
            if startup_expired or heartbeat_expired:
                record.failure_reported = True
                self._emit(token, "error", message="播放器加载超时。" if startup_expired else "播放器停止响应。")
                self.stop(token)

    def close(self) -> None:
        for token in list(self.records):
            self.stop(token)
        deadline = self.clock() + self.STOP_TIMEOUT
        records = list(self.records.values())
        for record in records:
            try:
                record.process.wait(timeout=max(0.05, deadline - self.clock()))
            except subprocess.TimeoutExpired:
                record.process.kill()
                record.process.wait(timeout=5)
        for record in records:
            if record.reader:
                record.reader.join(timeout=1)
        self.job.close()
