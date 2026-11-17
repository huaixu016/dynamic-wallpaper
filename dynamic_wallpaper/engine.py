"""Testable lifecycle controller. No Qt or Win32 imports here."""

import logging
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from .models import CoverageKind, DisplayMode, Monitor, PlaybackAction, Priority, State, validate_path, validate_volume

log = logging.getLogger(__name__)


@dataclass
class Slot:
    token: str
    monitor: Monitor
    ready: bool = False
    shown: bool = False
    position: float = 0.0
    progress_key: tuple | None = None
    progress_at: float = 0.0
    paused: bool = False


@dataclass
class Transaction:
    video: Path
    initial: bool
    slots: dict[str, Slot]
    showing: bool = False


class WallpaperEngine:
    MAX_RESTARTS = 3
    RESTART_WINDOW = 60.0
    STALL_TIMEOUT = 15.0

    def __init__(self, pool, desktop, config, snapshot, library: str | None = None,
                 changed=lambda: None, error=lambda message: None, clock=time.monotonic):
        self.pool, self.desktop, self.config, self.snapshot = pool, desktop, config, snapshot
        self.library, self.changed, self.error, self.clock = library, changed, error, clock
        self.video = Path(config.video) if config.video else None
        self.video_valid = False
        self.priority = config.priority
        self.display_mode = config.display_mode
        self.audio_enabled = config.audio_enabled
        self.volume = config.volume
        self.maximized_action, self.fullscreen_action = config.maximized_action, config.fullscreen_action
        self.audio_owner: str | None = None
        self.audio_waiting: set[str] = set()
        self.desired_enabled = False
        self.user_paused = False
        self.monitors: dict[str, Monitor] = {}
        self.reasons: frozenset[str] = frozenset()
        self.blocked: frozenset[str] = frozenset()
        self.released: frozenset[str] = frozenset()
        self.auto_paused: frozenset[str] = frozenset()
        self.coverage: dict[str, frozenset[CoverageKind]] = {}
        self.active: dict[str, Slot] = {}
        self.retiring: set[str] = set()
        self.transaction: Transaction | None = None
        self.probe_token: str | None = None
        self.probe_video: Path | None = None
        self.stopping = False
        self.session_open = False
        self.last_error: str | None = None
        self.positions: dict[str, float] = {}
        self.failures: dict[str, deque] = defaultdict(deque)
        self.retry_at: dict[str, float] = {}

    @property
    def busy(self) -> bool:
        return bool(self.probe_token or self.transaction or self.stopping)

    @property
    def state(self) -> State:
        if self.stopping:
            return State.STOPPING
        if self.probe_token or self.transaction:
            return State.LOADING
        if self.desired_enabled:
            shown = [slot for slot in self.active.values() if slot.shown]
            if shown:
                paused = self.user_paused or (self.auto_paused and
                                             all(slot.monitor.device in self.auto_paused for slot in shown))
                return State.PAUSED if paused else State.RUNNING
            return State.LOADING if self._targets() else State.SUSPENDED
        if self.video_valid:
            return State.READY
        return State.ERROR if self.last_error else State.EMPTY

    @property
    def settled(self) -> bool:
        return not self.pool.records

    def _targets(self) -> list[Monitor]:
        if self.reasons:
            return []
        return [monitor for monitor in self.monitors.values() if monitor.device not in self.released]

    def _is_paused(self, device: str) -> bool:
        return self.user_paused or device in self.auto_paused

    def _report(self, message: str) -> None:
        self.last_error = message
        log.error("%s", message)
        self.error(message)
        self.changed()

    def _save(self) -> None:
        try:
            self.config.save(str(self.video) if self.video else None, self.priority,
                             self.display_mode, self.audio_enabled, self.volume,
                             self.maximized_action, self.fullscreen_action)
        except OSError as exc:
            self._report(f"当前设置已应用，但无法保存配置：{exc}")

    def load_saved(self) -> None:
        if self.video:
            self.select(self.video)
        self.changed()

    def select(self, value: str | Path) -> None:
        if self.busy:
            return
        try:
            candidate = validate_path(value)
            spec = {
                "probe": True, "video": str(candidate), "library": self.library,
                "log_directory": str(self.config.directory / "logs"),
            }
            token = self.pool.spawn(spec)
        except (OSError, ValueError, RuntimeError) as exc:
            self._report(f"无法选择视频：{exc}")
            return
        self.probe_video, self.probe_token = candidate, token
        self.last_error = None
        self.changed()

    def start(self) -> None:
        if self.busy or self.desired_enabled or not self.video_valid or not self.video:
            return
        try:
            validate_path(self.video)
        except (OSError, ValueError) as exc:
            self.video_valid = False
            self._report(f"开启壁纸失败：{exc}")
            return
        try:
            self.config.begin_session(self.snapshot())
            self.session_open = True
            self.desired_enabled = True
            self.user_paused = False
            self.last_error = None
            self._begin_transaction(self.video, initial=True)
        except (OSError, ValueError, RuntimeError) as exc:
            self.desired_enabled = False
            self._report(f"开启壁纸失败：{exc}")
            self._finish_session()
        self.changed()

    def _spawn_slot(self, monitor: Monitor, video: Path, position: float = 0) -> Slot:
        parent = self.desktop.locate()
        spec = {
            "device": monitor.device, "parent": parent, "bounds": monitor.bounds.to_dict(),
            "video": str(video), "library": self.library, "position": position,
            "display_mode": str(self.display_mode), "paused": self._is_paused(monitor.device),
            "volume": self.volume,
            "log_directory": str(self.config.directory / "logs"),
        }
        token = self.pool.spawn(spec)
        return Slot(token, monitor, position=position, progress_at=self.clock(),
                    paused=self._is_paused(monitor.device))

    def _retire(self, token: str) -> None:
        if token == self.audio_owner:
            self._mute_audio(token)
        if token in self.pool.records:
            self.retiring.add(token)
            self.pool.command(token, command="hide")
            self.pool.stop(token)
        else:
            self.retiring.discard(token)

    def _cancel_transaction(self) -> Transaction | None:
        transaction, self.transaction = self.transaction, None
        if transaction:
            for slot in transaction.slots.values():
                self._retire(slot.token)
        return transaction

    def _begin_transaction(self, video: Path, initial: bool) -> None:
        self._cancel_transaction()
        transaction = Transaction(video, initial, {})
        self.transaction = transaction
        try:
            for monitor in self._targets():
                transaction.slots[monitor.device] = self._spawn_slot(monitor, video)
            if not transaction.slots:
                self._commit_transaction()
        except (OSError, ValueError, RuntimeError) as exc:
            self._rollback(f"准备视频播放层失败：{exc}")

    def _commit_transaction(self) -> None:
        transaction = self.transaction
        if transaction is None:
            return
        self.transaction = None
        for slot in self.active.values():
            self._retire(slot.token)
        self.active = transaction.slots
        self.video, self.video_valid = transaction.video, True
        self.positions.clear()
        self.retry_at.clear()
        self.failures.clear()
        self.last_error = None
        self._save()
        self._sync_audio()
        self.changed()

    def _rollback(self, message: str) -> None:
        transaction = self._cancel_transaction()
        if transaction and transaction.initial:
            self.desired_enabled = False
            self.user_paused = False
            self.stopping = bool(self.pool.records)
        self._report(message)
        self._finish_session()

    def stop(self) -> None:
        self.desired_enabled = False
        self.user_paused = False
        self._cancel_transaction()
        self.probe_token = self.probe_video = None
        self.active.clear()
        self.retry_at.clear()
        self.positions.clear()
        # Include processes whose spawn failed after creation, and retiring generations.
        for token in list(self.pool.records):
            self._retire(token)
        self.stopping = bool(self.pool.records)
        self._finish_session()
        self.changed()

    def _finish_session(self) -> None:
        if self.desired_enabled or self.pool.records:
            return
        self.stopping = False
        if self.session_open:
            self.session_open = False
            try:
                # No SetWallpaper/registry writes: removing our layer reveals the user's background.
                self.desktop.repaint()
                self.config.end_session()
            except OSError as exc:
                self._report(f"视频已停止，但桌面重绘或会话记录清理失败：{exc}")

    def set_priority(self, value: Priority) -> None:
        self.priority = Priority(value)
        tokens = [slot.token for slot in self.active.values()]
        if self.transaction:
            tokens += [slot.token for slot in self.transaction.slots.values()]
        for token in tokens:
            self.pool.command(token, command="priority", priority=str(self.priority))
        self._save()
        self.changed()

    def set_display_mode(self, value: DisplayMode) -> None:
        self.display_mode = DisplayMode(value)
        slots = list(self.active.values())
        if self.transaction:
            slots += list(self.transaction.slots.values())
        for slot in slots:
            self.pool.command(slot.token, command="display_mode", mode=str(self.display_mode))
        self._save()
        self.changed()

    def set_paused(self, value: bool) -> None:
        if not self.desired_enabled or self.stopping or self.user_paused == bool(value):
            return
        self.user_paused = bool(value)
        self._sync_pauses()
        self.changed()

    def _sync_pauses(self) -> None:
        slots = list(self.active.values())
        if self.transaction:
            slots += list(self.transaction.slots.values())
        owner = next((slot for slot in slots if slot.token == self.audio_owner), None)
        if owner and self._is_paused(owner.monitor.device):
            self._mute_audio(owner.token)
        for slot in slots:
            paused = self._is_paused(slot.monitor.device)
            if paused != slot.paused:
                self.pool.command(slot.token, command="pause", paused=paused)
                slot.paused = paused
                slot.progress_at = self.clock()
        self._sync_audio()

    def set_volume(self, value: int) -> None:
        self.volume = validate_volume(value)
        slots = list(self.active.values())
        if self.transaction:
            slots += list(self.transaction.slots.values())
        for slot in slots:
            self.pool.command(slot.token, command="volume", value=self.volume)
        self._save()
        self.changed()

    def set_policy(self, condition: CoverageKind, action: PlaybackAction) -> None:
        condition, action = CoverageKind(condition), PlaybackAction(action)
        if condition == CoverageKind.MAXIMIZED:
            self.maximized_action = action
        else:
            self.fullscreen_action = action
        self._apply_environment()
        self._save()
        self.changed()

    def set_audio_enabled(self, value: bool) -> None:
        self.audio_enabled = bool(value)
        self._sync_audio()
        self._save()
        self.changed()

    def _mute_audio(self, token: str) -> None:
        self.audio_owner = None
        if token in self.pool.records:
            self.audio_waiting.add(token)
            self.pool.command(token, command="audio", enabled=False)

    def _sync_audio(self) -> None:
        candidates = [slot for slot in self.active.values()
                      if slot.shown and slot.token in self.pool.records and slot.token not in self.retiring
                      and slot.monitor.device not in self.released and not self._is_paused(slot.monitor.device)]
        candidates.sort(key=lambda slot: (not slot.monitor.primary, slot.monitor.device))
        target = (candidates[0].token if candidates and self.audio_enabled
                  and self.desired_enabled and not self.reasons and not self.user_paused else None)
        if self.audio_owner == target:
            return
        if self.audio_owner:
            self._mute_audio(self.audio_owner)
        # A pipe write is not an acknowledgement: never unmute another process until silence is confirmed.
        if self.audio_waiting:
            return
        if target and self.pool.command(target, command="audio", enabled=True, volume=self.volume):
            self.audio_owner = target

    def set_environment(self, monitors: list[Monitor], reasons=(), blocked=()) -> None:
        new_monitors = {monitor.device: monitor for monitor in monitors}
        new_reasons = frozenset(reasons)
        if isinstance(blocked, dict):
            coverage = {device: frozenset(CoverageKind(kind) for kind in kinds)
                        for device, kinds in blocked.items() if kinds}
        else:
            # Legacy callers only reported coverage; retain their stop behavior under the maximized policy.
            coverage = {device: frozenset({CoverageKind.MAXIMIZED}) for device in blocked}
        if (new_monitors, new_reasons, coverage) == (self.monitors, self.reasons, self.coverage):
            return
        self.monitors, self.reasons, self.coverage = new_monitors, new_reasons, coverage
        self._apply_environment()
        self.changed()

    def _apply_environment(self) -> None:
        rank = {PlaybackAction.KEEP: 0, PlaybackAction.PAUSE: 1, PlaybackAction.STOP: 2}
        actions = {}
        for device, kinds in self.coverage.items():
            choices = [self.maximized_action if kind == CoverageKind.MAXIMIZED else self.fullscreen_action
                       for kind in kinds]
            actions[device] = max(choices, key=rank.get)
        self.released = frozenset(device for device, action in actions.items() if action == PlaybackAction.STOP)
        self.auto_paused = frozenset(device for device, action in actions.items() if action == PlaybackAction.PAUSE)
        self.blocked = self.released | self.auto_paused
        for device, slot in list(self.active.items()):
            monitor = self.monitors.get(device)
            if monitor is None or self.reasons or device in self.released:
                self.positions[device] = slot.position
                self.active.pop(device)
                self._retire(slot.token)
            elif monitor != slot.monitor:
                slot.monitor = monitor
                self.pool.command(slot.token, command="geometry", bounds=monitor.bounds.to_dict())
        for device in list(self.retry_at):
            if device not in self.monitors:
                self.retry_at.pop(device, None)
                self.failures.pop(device, None)
                self.positions.pop(device, None)
        if self.transaction:
            transaction = self.transaction
            targets = {monitor.device: monitor for monitor in self._targets()}
            if (set(targets) != set(transaction.slots)
                    or any(targets[device] != slot.monitor for device, slot in transaction.slots.items())):
                self._begin_transaction(transaction.video, transaction.initial)
        elif self.desired_enabled:
            self._reconcile()
        self._sync_pauses()

    def _reconcile(self) -> None:
        if not self.desired_enabled or self.transaction or not self.video:
            return
        now = self.clock()
        for monitor in self._targets():
            if monitor.device in self.active or now < self.retry_at.get(monitor.device, 0):
                continue
            # Wait for a suspended renderer to actually exit before recreating it.
            if any(token in self.pool.records for token in self.retiring):
                continue
            try:
                self.active[monitor.device] = self._spawn_slot(monitor, self.video, self.positions.get(monitor.device, 0))
                self.retry_at.pop(monitor.device, None)
            except (OSError, ValueError, RuntimeError) as exc:
                self._schedule_failure(monitor.device, str(exc))
                if not self.desired_enabled:
                    break

    def _schedule_failure(self, device: str, message: str) -> None:
        now = self.clock()
        history = self.failures[device]
        while history and now - history[0] > self.RESTART_WINDOW:
            history.popleft()
        history.append(now)
        if len(history) > self.MAX_RESTARTS:
            self.stop()
            self._report(f"壁纸无法恢复，已停止并恢复原背景：{message}")
        else:
            self.retry_at[device] = now + 2 ** (len(history) - 1)
            log.warning("显示器 %s 将尝试第 %d 次恢复：%s", device, len(history), message)

    def _active_failure(self, device: str, message: str) -> None:
        slot = self.active.pop(device, None)
        if slot:
            self.positions[device] = slot.position
            self._retire(slot.token)
        if self.desired_enabled:
            self._schedule_failure(device, message)
        self.changed()

    def handle(self, event: dict) -> None:
        token, kind = event["token"], event["event"]
        if kind == "audio_state":
            if event.get("enabled") is False:
                self.audio_waiting.discard(token)
                self._sync_audio()
            return
        if kind == "exited":
            self.audio_waiting.discard(token)
            if token == self.audio_owner:
                self.audio_owner = None
            was_retiring = token in self.retiring
            self.retiring.discard(token)
            if was_retiring:
                self._finish_session()
                self._reconcile()
                self.changed()
                return
        elif token in self.retiring:
            return
        if token == self.probe_token:
            if kind == "ready":
                candidate = self.probe_video
                self.probe_token = self.probe_video = None
                self._retire(token)
                if self.desired_enabled:
                    self._begin_transaction(candidate, initial=False)
                else:
                    self.video, self.video_valid = candidate, True
                    self.last_error = None
                    self._save()
                self.changed()
            elif kind in {"error", "exited"}:
                self.probe_token = self.probe_video = None
                if kind != "exited":
                    self._retire(token)
                self._report(event.get("message", "视频校验进程退出，未能解码首帧。"))
            return
        transaction = self.transaction
        if transaction:
            staged = next((slot for slot in transaction.slots.values() if slot.token == token), None)
            if staged:
                if kind in {"error", "exited"}:
                    self._rollback(event.get("message", "新视频播放进程在准备阶段退出。"))
                elif kind == "ready":
                    staged.ready = True
                    if all(slot.ready for slot in transaction.slots.values()) and not transaction.showing:
                        transaction.showing = True
                        for slot in transaction.slots.values():
                            if not self.pool.command(slot.token, command="show", priority=str(self.priority)):
                                self._rollback("新视频播放层无法显示，已保留原视频。")
                                break
                elif kind == "shown":
                    staged.shown = True
                    staged.progress_at = self.clock()
                    if all(slot.shown for slot in transaction.slots.values()):
                        self._commit_transaction()
                return
        device = next((device for device, slot in self.active.items() if slot.token == token), None)
        if device is not None:
            slot = self.active[device]
            if kind in {"error", "exited"}:
                self._active_failure(device, event.get("message", "视频播放进程意外退出。"))
            elif kind == "ready":
                slot.ready = True
                if not self.pool.command(token, command="show", priority=str(self.priority)):
                    self._active_failure(device, "无法恢复视频显示。")
            elif kind == "shown":
                slot.shown = True
                slot.progress_at = self.clock()
                self._sync_audio()
                self.changed()
            elif kind == "status":
                position = event.get("position")
                if (isinstance(position, (int, float)) and not isinstance(position, bool)
                        and math.isfinite(position) and position >= 0):
                    slot.position = float(position)
                    progress = (round(position, 3), event.get("restart_count", 0))
                    if slot.progress_key != progress:
                        slot.progress_key, slot.progress_at = progress, self.clock()
        if kind == "exited":
            self._finish_session()
            self._reconcile()
            self.changed()

    def tick(self) -> None:
        self.pool.tick()
        for event in self.pool.drain():
            self.handle(event)
        now = self.clock()
        for device, slot in list(self.active.items()):
            if slot.shown and not self._is_paused(device) and now - slot.progress_at > self.STALL_TIMEOUT:
                self._active_failure(device, "视频播放位置长时间未变化。")
                if not self.desired_enabled:
                    break
        self._reconcile()
        self._sync_audio()
        self._finish_session()
