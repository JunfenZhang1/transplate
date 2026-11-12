"""Windows desktop interface for live subtitle recognition and display."""
from __future__ import annotations

import ctypes
import os
import queue
import sys

from PySide6.QtCore import Qt, QRect, QPoint, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from . import __version__
from .settings import Region, Settings, load_settings, save_settings
from .worker import Message, SubtitleWorker


def enable_dpi_awareness() -> None:
    """Avoid Windows' virtualized screenshot coordinates at 125%/150% scaling."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


def monitor_pairs():
    """Map Qt logical screen rectangles to MSS physical screen rectangles."""
    import mss
    screens = QGuiApplication.screens()
    with mss.mss() as cap:
        physical = cap.monitors[1:]
    # Qt and MSS normally enumerate displays in the same order on Windows.
    # Match the primary monitor first to reduce ordering ambiguity.
    if len(physical) == 1:
        return [(screens[0], physical[0])]
    result = []
    unmatched = list(physical)
    for screen in screens:
        geo = screen.geometry()
        target = (geo.width() * screen.devicePixelRatio(), geo.height() * screen.devicePixelRatio())
        mon = min(unmatched, key=lambda m: abs(m["width"] - target[0]) + abs(m["height"] - target[1]))
        result.append((screen, mon))
        unmatched.remove(mon)
        if not unmatched:
            break
    return result


def logical_to_physical(rect: QRect, screen) -> Region:
    for candidate, mon in monitor_pairs():
        if candidate == screen:
            geo = candidate.geometry()
            fx, fy = mon["width"] / geo.width(), mon["height"] / geo.height()
            return Region(mon["left"] + round((rect.x() - geo.x()) * fx),
                          mon["top"] + round((rect.y() - geo.y()) * fy),
                          max(1, round(rect.width() * fx)), max(1, round(rect.height() * fy)))
    raise RuntimeError("无法定位字幕所处的显示器")


def physical_to_logical(region: Region) -> QRect:
    center = (region.left + region.width // 2, region.top + region.height // 2)
    pairs = monitor_pairs()
    for screen, mon in pairs:
        if mon["left"] <= center[0] < mon["left"] + mon["width"] and mon["top"] <= center[1] < mon["top"] + mon["height"]:
            geo = screen.geometry()
            fx, fy = geo.width() / mon["width"], geo.height() / mon["height"]
            return QRect(geo.left() + round((region.left - mon["left"]) * fx),
                         geo.top() + round((region.top - mon["top"]) * fy),
                         max(80, round(region.width * fx)), max(40, round(region.height * fy)))
    raise RuntimeError("字幕区域不在当前显示器范围内，请重新选择")


class RegionSelector(QWidget):
    selected = Signal(object)
    dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(None)
        self.screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(self.screen.geometry())
        self.start: QPoint | None = None
        self.end: QPoint | None = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.show()
        self.raise_()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = event.position().toPoint()
            self.end = self.start
            self.update()

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self.end = event.position().toPoint()
        rect = QRect(self.start, self.end).normalized()
        if rect.width() >= 50 and rect.height() >= 20:
            rect.translate(self.geometry().topLeft())
            self.selected.emit(logical_to_physical(rect, self.screen))
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()

    def closeEvent(self, event):
        self.dismissed.emit()
        super().closeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 125))
        if self.start is not None and self.end is not None:
            rect = QRect(self.start, self.end).normalized()
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor("#62c8ff"), 2))
            painter.drawRect(rect)
        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(QRect(20, 20, self.width()-40, 50),
                         "拖动框选字幕区域 · Esc 取消")


class SubtitleOverlay(QWidget):
    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus |
                            Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        self.font_size = 27
        self.render_style()

    def render_style(self):
        self.label.setStyleSheet(
            "QLabel { background-color: rgba(0, 0, 0, 225); color: #ffffff; "
            f"font-size: {self.font_size}px; font-weight: 650; "
            "border-radius: 9px; padding: 6px 12px; }"
        )

    def set_caption(self, caption: str, region: Region, font_size: int) -> bool:
        if not caption.strip():
            self.hide()
            return True
        self.font_size = font_size
        self.render_style()
        box = physical_to_logical(region)
        height = max(64, min(160, box.height()))
        self.setGeometry(box.x(), box.y() + (box.height() - height)//2, box.width(), height)
        self.label.setText(caption)
        self.show()
        self.raise_()
        excluded = self.exclude_from_capture()
        if not excluded:
            # The overlay must not be OCR-ed as if it were the original subtitle.
            screen = QGuiApplication.screenAt(box.center()) or QGuiApplication.primaryScreen()
            available = screen.availableGeometry()
            above = box.top() - height - 8
            below = box.bottom() + 8
            y = above if above >= available.top() else below
            self.move(box.x(), min(max(y, available.top()), available.bottom() - height))
        return excluded

    def exclude_from_capture(self) -> bool:
        if os.name != "nt":
            return False
        try:
            # Windows 10 2004+: WDA_EXCLUDEFROMCAPTURE (0x11).
            handle = int(self.winId())
            return bool(ctypes.windll.user32.SetWindowDisplayAffinity(handle, 0x11))
        except (AttributeError, OSError, ValueError):
            return False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.preferences = load_settings()
        self.worker: SubtitleWorker | None = None
        self.messages: queue.Queue[Message] = queue.Queue()
        self.selector: RegionSelector | None = None
        self.overlay = SubtitleOverlay()
        self.setWindowTitle(f"Transplate {__version__} · 实时字幕转简体")
        self.setMinimumWidth(520)

        base = QWidget(self)
        self.setCentralWidget(base)
        outer = QVBoxLayout(base)
        outer.setSpacing(12)
        title = QLabel("实时字幕识别与简体中文覆盖")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        outer.addWidget(title)
        self.region_label = QLabel()
        region_row = QHBoxLayout()
        self.pick_button = QPushButton("框选字幕区域")
        self.pick_button.clicked.connect(self.pick_region)
        region_row.addWidget(self.pick_button)
        region_row.addWidget(self.region_label, 1)
        outer.addLayout(region_row)

        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem("繁体 → 简体（离线）", "convert")
        self.mode.addItem("其他语言 → 简体（翻译）", "translate")
        form.addRow("处理模式", self.mode)
        self.language = QComboBox()
        for name, value in [("中文／英文（默认离线）", "ch"), ("繁体中文（额外模型）", "chinese_cht"),
                            ("英文（额外模型）", "en"), ("日文（额外模型）", "japan"),
                            ("韩文（额外模型）", "korean"), ("拉丁字母语言（额外模型）", "latin")]:
            self.language.addItem(name, value)
        form.addRow("OCR 识别语言", self.language)
        self.provider = QComboBox()
        self.provider.addItem("Google 翻译（联网，无须密钥，非官方接口）", "google")
        self.provider.addItem("OpenAI 兼容 API（如本地 Ollama）", "openai_compatible")
        form.addRow("翻译服务", self.provider)
        self.url = QLineEdit()
        self.url.setPlaceholderText("http://127.0.0.1:11434/v1")
        form.addRow("API 地址", self.url)
        self.model = QLineEdit()
        form.addRow("API 模型", self.model)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("可选；仅保留在本次运行的内存中")
        form.addRow("API 密钥", self.key)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.4, 5.0)
        self.interval.setSingleStep(0.1)
        self.interval.setSuffix(" 秒")
        form.addRow("扫描间隔", self.interval)
        self.font_size = QSpinBox()
        self.font_size.setRange(14, 64)
        self.font_size.setSuffix(" px")
        form.addRow("字幕字号", self.font_size)
        outer.addLayout(form)
        self.mode.currentIndexChanged.connect(self.sync_visibility)
        self.provider.currentIndexChanged.connect(self.sync_visibility)

        controls = QHBoxLayout()
        self.start_button = QPushButton("开始实时转换")
        self.stop_button = QPushButton("停止")
        self.start_button.clicked.connect(self.start_capture)
        self.stop_button.clicked.connect(self.stop_capture)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        outer.addLayout(controls)
        self.status = QLabel("就绪。转换模式不需要网络。")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.preview = QLabel("识别预览：—")
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet("color: #777; padding: 8px;")
        outer.addWidget(self.preview)
        self.info = QLabel("建议先框选播放器底部的 1–2 行字幕。浮层不会接收鼠标点击。")
        self.info.setWordWrap(True)
        outer.addWidget(self.info)
        self.mode.setCurrentIndex(max(0, self.mode.findData(self.preferences.mode)))
        self.language.setCurrentIndex(max(0, self.language.findData(self.preferences.language)))
        self.provider.setCurrentIndex(max(0, self.provider.findData(self.preferences.provider)))
        self.url.setText(self.preferences.api_base_url)
        self.model.setText(self.preferences.model)
        self.interval.setValue(self.preferences.interval)
        self.font_size.setValue(self.preferences.font_size)
        self.sync_visibility()
        self.update_region_label()
        self.stop_button.setEnabled(False)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.consume_messages)
        self.timer.start(100)

    def update_region_label(self):
        r = self.preferences.region
        self.region_label.setText(f"{r.width}×{r.height} @ ({r.left},{r.top})" if r else "尚未选择")

    def sync_visibility(self):
        translating = self.mode.currentData() == "translate"
        local_api = translating and self.provider.currentData() == "openai_compatible"
        self.provider.setEnabled(translating)
        for item in (self.url, self.model, self.key):
            item.setEnabled(local_api)

    def current_settings(self) -> Settings:
        return Settings(
            region=self.preferences.region,
            mode=self.mode.currentData(),
            language=self.language.currentData(),
            provider=self.provider.currentData(),
            api_base_url=self.url.text().strip(),
            model=self.model.text().strip(),
            interval=self.interval.value(),
            font_size=self.font_size.value(),
        )

    def pick_region(self):
        self.stop_capture()
        self.hide()
        self.selector = RegionSelector()
        self.selector.selected.connect(self.region_selected)
        self.selector.dismissed.connect(self.show)

    def region_selected(self, region: Region):
        self.preferences.region = region
        self.update_region_label()
        self.status.setText("区域已选择；点击开始即可识别。")
        self.show()

    def start_capture(self):
        if self.worker and self.worker.is_alive():
            return
        config = self.current_settings()
        if config.region is None:
            QMessageBox.information(self, "需要字幕区域", "请先点击「框选字幕区域」。")
            return
        try:
            physical_to_logical(config.region)
            save_settings(config)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "配置错误", str(exc))
            return
        if config.mode == "translate" and config.provider == "google":
            self.status.setText("在线翻译会将识别到的字幕文本发送至 Google 翻译。")
        self.overlay.hide()
        self.messages = queue.Queue()
        self.worker = SubtitleWorker(config, self.key.text(), self.messages)
        self.worker.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.pick_button.setEnabled(False)

    def stop_capture(self):
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.overlay.hide()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.pick_button.setEnabled(True)

    def consume_messages(self):
        try:
            while True:
                msg = self.messages.get_nowait()
                if msg.kind == "caption":
                    self.preview.setText("识别预览：" + (msg.source or "—"))
                    if self.preferences.region:
                        excluded = self.overlay.set_caption(msg.value, self.preferences.region, self.font_size.value())
                        if msg.value and not excluded:
                            self.status.setText("系统不支持排除浮层录屏；字幕已移到识别区域外以防重复识别。")
                elif msg.kind == "error":
                    self.status.setText("错误：" + msg.value)
                elif msg.kind == "info":
                    self.status.setText(msg.value)
                elif msg.kind == "finished":
                    if self.worker:
                        self.stop_capture()
        except queue.Empty:
            pass

    def closeEvent(self, event):
        self.stop_capture()
        try:
            save_settings(self.current_settings())
        except OSError:
            pass
        self.overlay.close()
        super().closeEvent(event)


def self_test() -> int:
    from PIL import Image
    from .core import to_simplified
    from .ocr import OCRReader
    if to_simplified("繁體中文測試") != "繁体中文测试":
        return 1
    reader = OCRReader("ch")
    reader.read(Image.new("RGB", (480, 128), "black"))
    return 0


def main() -> None:
    enable_dpi_awareness()
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    app = QApplication(sys.argv)
    app.setApplicationName("Transplate")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
