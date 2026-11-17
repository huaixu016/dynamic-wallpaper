import ctypes as C
import logging

from PySide6.QtCore import QEvent, QObject, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
    QMenu, QMessageBox, QProxyStyle, QPushButton, QSlider, QStyle, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from . import win32 as win
from .background import capture_background
from .desktop import DesktopHost
from .engine import WallpaperEngine
from .models import CoverageKind, DisplayMode, PlaybackAction, Priority, State
from .mpv import application_directory
from .processes import RendererPool
from .system_monitor import SystemMonitor

log = logging.getLogger(__name__)


class NoFocusFrameStyle(QProxyStyle):
    def drawPrimitive(self, element, option, painter, widget=None):
        if element != QStyle.PrimitiveElement.PE_FrameFocusRect:
            super().drawPrimitive(element, option, painter, widget)


class MainWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("本地视频动态壁纸")
        icon = QIcon(str(application_directory() / "src" / "logo.ico"))
        if icon.isNull():
            icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        self.setWindowIcon(icon)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        body = QWidget(self)
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        title = QLabel("本地视频动态壁纸")
        font = title.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        self.filename = QLabel("未选择视频")
        self.filename.setWordWrap(True)
        self.filename.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.filename)
        self.choose = QPushButton("选择视频")
        self.choose.clicked.connect(controller.choose_video)
        layout.addWidget(self.choose)
        self.status = QLabel("未选择视频")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        display_row = QHBoxLayout()
        display_row.addWidget(QLabel("显示方式"))
        self.display_mode = QComboBox()
        for label, mode, tooltip in (
            ("等比填充", DisplayMode.COVER, "保持比例铺满屏幕，居中裁剪超出部分。"),
            ("等比适应", DisplayMode.CONTAIN, "完整显示视频、保持比例；左右留白使用对应视频边缘模糊拉伸填充，上下留白保持黑色。"),
            ("拉伸铺满", DisplayMode.STRETCH, "完整画面铺满屏幕，可能改变比例。"),
            ("原尺寸居中", DisplayMode.CENTER, "主画面不缩放；上下左右留白使用对应视频边缘模糊填充，超出屏幕的部分居中裁剪。"),
            ("平铺", DisplayMode.TILE, "按原始显示尺寸重复同一画面，边缘超出部分裁剪。"),
        ):
            self.display_mode.addItem(label, mode)
            self.display_mode.setItemData(self.display_mode.count() - 1, tooltip, Qt.ItemDataRole.ToolTipRole)
        self.display_mode.currentIndexChanged.connect(self._display_mode_changed)
        display_row.addWidget(self.display_mode, 1)
        layout.addLayout(display_row)
        self.audio = QCheckBox("播放视频音频")
        self.audio.setToolTip("默认静音；多显示器只输出一路音频，全部暂停时停止声音。下方音量只调整壁纸，不改变系统总音量。")
        self.audio.toggled.connect(controller.engine.set_audio_enabled)
        layout.addWidget(self.audio)
        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("视频音量"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(100)
        self.volume.setToolTip("仅控制壁纸音量；关闭音频开关时仍可预设音量。")
        self.volume.valueChanged.connect(controller.engine.set_volume)
        volume_row.addWidget(self.volume, 1)
        self.volume_label = QLabel("100%")
        self.volume_label.setMinimumWidth(self.volume_label.sizeHint().width())
        self.volume.valueChanged.connect(lambda value: self.volume_label.setText(f"{value}%"))
        volume_row.addWidget(self.volume_label)
        layout.addLayout(volume_row)
        self.policy_controls = {}
        for label, condition in (("其他应用程序最大化时", CoverageKind.MAXIMIZED),
                                 ("其他应用程序全屏时", CoverageKind.FULLSCREEN)):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            combo = QComboBox()
            for text, action in (("保持运行", PlaybackAction.KEEP), ("暂停", PlaybackAction.PAUSE),
                                 ("停止（释放内存）", PlaybackAction.STOP)):
                combo.addItem(text, action)
            combo.setCurrentIndex(2)
            combo.setToolTip("按屏幕独立生效；停止会释放该屏播放器资源，条件解除后自动恢复。锁屏、睡眠和关屏始终停止。")
            combo.currentIndexChanged.connect(lambda index, kind=condition, control=combo:
                                              controller.engine.set_policy(kind, control.itemData(index)))
            row.addWidget(combo, 1)
            layout.addLayout(row)
            self.policy_controls[condition] = combo
        priority_row = QHBoxLayout()
        priority_row.addWidget(QLabel("层级优先级"))
        self.priority = QComboBox()
        self.priority.addItem("较低", Priority.LOW)
        self.priority.addItem("最高", Priority.HIGH)
        self.priority.setToolTip("仅调整同一安全壁纸层内的顺序；无法保证与所有第三方壁纸软件兼容。")
        self.priority.currentIndexChanged.connect(self._priority_changed)
        priority_row.addWidget(self.priority, 1)
        layout.addLayout(priority_row)
        buttons = QHBoxLayout()
        self.start = QPushButton("开启壁纸")
        self.stop = QPushButton("关闭壁纸")
        self.start.clicked.connect(controller.start_wallpaper)
        self.stop.clicked.connect(controller.stop_wallpaper)
        buttons.addWidget(self.start)
        buttons.addWidget(self.stop)
        layout.addLayout(buttons)
        self._focus_style = NoFocusFrameStyle()
        self._focus_style.setParent(self)
        for control in (self.choose, self.display_mode, self.audio, self.volume, self.priority,
                        self.start, self.stop, *self.policy_controls.values()):
            control.setStyle(self._focus_style)
        self._normal_size = QSize(480, self.sizeHint().height())
        self.setMinimumSize(self._normal_size)
        self.resize(self._normal_size)
        self.taskbar_created = win.RegisterWindowMessage("TaskbarCreated")
        self._update_native_frame()

    def _update_native_frame(self):
        if QApplication.platformName() != "windows":
            return
        hwnd = int(self.winId())
        style = win.GetWindowLongPtr(hwnd, -16)
        if style & 0x00040000:  # WS_THICKFRAME; keep the independent maximize/minimize styles.
            win.check(win.SetWindowLongPtr(hwnd, -16, style & ~0x00040000), "无法禁用窗口边缘缩放")
            win.check(win.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037), "无法更新窗口边框")

    def _restore_normal_size(self):
        self._update_native_frame()
        state = self.windowState()
        if not state & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowMinimized
                        | Qt.WindowState.WindowFullScreen) and self.size() != self._normal_size:
            self.resize(self._normal_size)

    def showEvent(self, event):
        super().showEvent(event)
        self._update_native_frame()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_normal_size") and event.size() != self._normal_size:
            # Wait for the native state update so a maximize transition is not clamped as a normal resize.
            QTimer.singleShot(0, self._restore_normal_size)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "_normal_size"):
            QTimer.singleShot(0, self._restore_normal_size)

    def _priority_changed(self, index):
        self.controller.engine.set_priority(self.priority.itemData(index))

    def _display_mode_changed(self, index):
        self.controller.engine.set_display_mode(self.display_mode.itemData(index))

    def update_state(self, engine):
        self.filename.setText(engine.video.name if engine.video else "未选择视频")
        self.filename.setToolTip(str(engine.video) if engine.video else "")
        status = {
            State.EMPTY: "未选择视频", State.READY: "已就绪", State.LOADING: "正在校验或加载视频…",
            State.RUNNING: "壁纸运行中", State.PAUSED: "壁纸已暂停（手动）" if engine.user_paused else "壁纸已暂停（自动策略）",
            State.SUSPENDED: "壁纸已开启，当前暂停播放",
            State.STOPPING: "正在停止并释放资源…", State.ERROR: "视频不可用，请重新选择",
        }[engine.state]
        if engine.state == State.SUSPENDED:
            reason = "、".join(sorted(engine.reasons)) if engine.reasons else "屏幕被其他程序占用"
            status += f"：{reason}"
        elif engine.state == State.RUNNING and engine.blocked:
            status += f"（{len(engine.blocked)} 块屏幕已暂停）"
        self.status.setText(status)
        self.choose.setEnabled(not engine.busy and not self.controller.quitting)
        self.start.setEnabled(engine.video_valid and not engine.desired_enabled and not engine.busy)
        self.stop.setEnabled(engine.desired_enabled and not engine.stopping)
        self.display_mode.blockSignals(True)
        self.display_mode.setCurrentIndex(self.display_mode.findData(engine.display_mode))
        self.display_mode.blockSignals(False)
        self.audio.blockSignals(True)
        self.audio.setChecked(engine.audio_enabled)
        self.audio.blockSignals(False)
        self.volume.blockSignals(True)
        self.volume.setValue(engine.volume)
        self.volume.blockSignals(False)
        self.volume_label.setText(f"{engine.volume}%")
        self.volume.setEnabled(not self.controller.quitting)
        for condition, combo in self.policy_controls.items():
            action = engine.maximized_action if condition == CoverageKind.MAXIMIZED else engine.fullscreen_action
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(action))
            combo.blockSignals(False)
            combo.setEnabled(not self.controller.quitting)
        self.display_mode.setEnabled(not self.controller.quitting)
        self.audio.setEnabled(not self.controller.quitting)
        self.priority.blockSignals(True)
        self.priority.setCurrentIndex(1 if engine.priority == Priority.HIGH else 0)
        self.priority.blockSignals(False)

    def closeEvent(self, event):
        if self.controller.quitting:
            event.accept()
        elif QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
            event.ignore()
        else:
            answer = QMessageBox.question(self, "退出程序", "系统托盘不可用。是否停止壁纸并退出程序？")
            if answer == QMessageBox.StandardButton.Yes:
                self.controller.request_quit()
            event.ignore()

    def nativeEvent(self, event_type, pointer):
        event_type = bytes(event_type)
        if event_type in {b"windows_generic_MSG", b"windows_dispatcher_MSG"}:
            message = C.cast(int(pointer), C.POINTER(win.MSG)).contents
            if message.message == self.controller.instance.message:
                if self.controller.window is None or self.controller.quitting:
                    return True, 0
                self.controller.show_main()
                return True, 1
            if message.message == self.taskbar_created and self.controller.tray is not None:
                self.controller.tray.hide()
                self.controller.tray.show()
                if self.controller.system is not None:
                    self.controller.system.mark_dirty()
            if message.message in {0x0011, 0x0016}:  # query/end Windows session
                if message.message == 0x0011 or message.wParam:
                    self.controller.request_quit()
                return True, 1
            if self.controller.system is not None and self.controller.system.native_message(message.message, message.wParam, message.lParam):
                return True, 1
        return super().nativeEvent(event_type, pointer)


class ApplicationController(QObject):
    process_event = Signal()

    def __init__(self, app, config, instance, library=None):
        super().__init__(app)
        self.app, self.config, self.instance = app, config, instance
        self.window = self.tray = self.system = None
        self.quitting = False
        self.error_boxes = []
        self.job = win.JobObject()
        self.pool = RendererPool(self.job, self._notify)
        desktop = DesktopHost()
        if config.recover_session():
            desktop.repaint()
        self.engine = WallpaperEngine(self.pool, desktop, config, capture_background, library,
                                      changed=self.refresh, error=self.show_error)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll)
        self.process_event.connect(self.poll, Qt.ConnectionType.QueuedConnection)
        self.window = MainWindow(self)
        self.system = SystemMonitor(int(self.window.winId()), self)
        self.system.environment_changed.connect(self.engine.set_environment)
        self.tray = QSystemTrayIcon(self.window.windowIcon(), self)
        self.tray.setToolTip("本地视频动态壁纸")
        menu = QMenu(self.window)
        self.open_action = QAction("打开主界面", menu)
        self.start_action = QAction("开启壁纸", menu)
        self.stop_action = QAction("关闭壁纸", menu)
        self.pause_action = QAction("暂停播放", menu)
        self.exit_action = QAction("退出程序", menu)
        self.open_action.triggered.connect(self.show_main)
        self.start_action.triggered.connect(self.start_wallpaper)
        self.stop_action.triggered.connect(self.stop_wallpaper)
        self.pause_action.triggered.connect(self.toggle_pause)
        self.exit_action.triggered.connect(self.request_quit)
        menu.addAction(self.open_action)
        menu.addSeparator()
        menu.addAction(self.start_action)
        menu.addAction(self.pause_action)
        menu.addAction(self.stop_action)
        menu.addSeparator()
        self.priority_menu = menu.addMenu("层级优先级")
        self.priority_group = QActionGroup(self.priority_menu)
        self.priority_group.setExclusive(True)
        self.priority_actions = {}
        for label, priority in (("较低", Priority.LOW), ("最高", Priority.HIGH)):
            action = self.priority_menu.addAction(label)
            action.setCheckable(True)
            action.setData(priority)
            action.setToolTip(self.window.priority.toolTip())
            self.priority_group.addAction(action)
            self.priority_actions[priority] = action
        self.priority_group.triggered.connect(self._tray_priority_changed)
        menu.addSeparator()
        menu.addAction(self.exit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()
        self.system.refresh()
        self.refresh()
        self.instance.register_window(int(self.window.winId()))
        app.aboutToQuit.connect(self.cleanup)
        QTimer.singleShot(0, self.engine.load_saved)

    def _notify(self):
        try:
            self.process_event.emit()
        except RuntimeError:
            pass  # Qt has already been destroyed during final process cleanup.

    def poll(self):
        self.engine.tick()
        self.refresh()

    def refresh(self):
        if self.window is None or self.system is None or self.tray is None:
            return
        self.window.update_state(self.engine)
        self.start_action.setEnabled(self.window.start.isEnabled() and not self.quitting)
        self.stop_action.setEnabled(self.window.stop.isEnabled() and not self.quitting)
        self.pause_action.setText("继续播放" if self.engine.user_paused else "暂停播放")
        self.pause_action.setEnabled(self.engine.desired_enabled and not self.engine.stopping and not self.quitting)
        self.priority_menu.setEnabled(not self.quitting)
        for priority, action in self.priority_actions.items():
            action.setChecked(self.engine.priority == priority)
        self.system.set_active(self.engine.desired_enabled and not self.quitting)
        working = bool(self.pool.records or self.engine.desired_enabled or self.engine.busy)
        if working and not self.timer.isActive():
            self.timer.start()
        elif not working:
            self.timer.stop()
        if self.quitting and self.engine.settled:
            QTimer.singleShot(0, self.app.quit)

    def choose_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self.window, "选择本地视频", str(self.engine.video.parent) if self.engine.video else "",
            "MP4 视频 (*.mp4);;本地视频 (*.mp4 *.m4v *.mov *.mkv *.webm *.avi)",
        )
        if path:
            self.engine.select(path)

    def start_wallpaper(self):
        if not self.quitting:
            self.system.set_active(True)
            self.system.refresh()
            self.engine.start()
            self.refresh()

    def stop_wallpaper(self):
        self.engine.stop()

    def toggle_pause(self):
        if not self.quitting:
            self.engine.set_paused(not self.engine.user_paused)

    def show_main(self):
        if not self.quitting and self.window is not None:
            if self.window.isMinimized():
                self.window.setWindowState(self.window.windowState() & ~Qt.WindowState.WindowMinimized)
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
            if QApplication.platformName() == "windows":
                hwnd = int(self.window.winId())
                win.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0013)  # HWND_TOP, no move/size, no activation
                if not win.SetForegroundWindow(hwnd):
                    log.debug("Windows 未授予窗口前台焦点，主窗口已显示并提升层级")

    def _tray_priority_changed(self, action):
        if not self.quitting:
            self.engine.set_priority(Priority(action.data()))

    def _tray_activated(self, reason):
        if reason in {QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick}:
            self.show_main()

    def show_error(self, message):
        if self.quitting:
            return
        QTimer.singleShot(0, lambda: self._open_error(message))

    def _open_error(self, message):
        if self.quitting or any(box.text() == message for box in self.error_boxes):
            return
        self.show_main()
        box = QMessageBox(QMessageBox.Icon.Warning, "动态壁纸", message,
                          QMessageBox.StandardButton.Ok, self.window)
        self.error_boxes.append(box)
        box.finished.connect(lambda _result: self._close_error(box))
        box.open()

    def _close_error(self, box):
        if box in self.error_boxes:
            self.error_boxes.remove(box)
        box.deleteLater()

    def request_quit(self):
        if self.quitting:
            return
        self.quitting = True
        self.engine.stop()
        self.refresh()

    def cleanup(self):
        self.quitting = True
        self.timer.stop()
        self.system.close()
        self.engine.stop()
        self.pool.close()
        for event in self.pool.drain():
            self.engine.handle(event)
        self.engine._finish_session()
        self.tray.hide()
        self.instance.close()
