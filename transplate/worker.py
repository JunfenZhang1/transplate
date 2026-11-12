"""Screen capture + OCR + optional translation, kept off the Qt GUI thread."""
from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
import time

from PIL import Image

from .core import has_readable_text, normalize_subtitle, to_simplified
from .ocr import OCRReader
from .settings import Settings
from .translator import TranslationClient


@dataclass(frozen=True)
class Message:
    kind: str  # caption | error | info | finished
    value: str = ""
    source: str = ""


class SubtitleWorker(threading.Thread):
    def __init__(self, settings: Settings, api_key: str, output: queue.Queue[Message]):
        super().__init__(name="transplate-capture", daemon=True)
        self.settings = settings
        self.api_key = api_key
        self.output = output
        self.cancel = threading.Event()

    def stop(self) -> None:
        self.cancel.set()

    def run(self) -> None:
        try:
            import mss
            if self.settings.region is None:
                raise RuntimeError("尚未选择字幕区域")
            self.output.put(Message("info", "初始化 OCR 模型中（首次选择新语言可能需要下载模型）…"))
            reader = OCRReader(self.settings.language)
            client = (TranslationClient(self.settings.provider, self.settings.api_base_url,
                                        self.settings.model, self.api_key)
                      if self.settings.mode == "translate" else None)
            self.output.put(Message("info", "正在监测字幕区域"))
            previous = ""
            blank_count = 0
            retry_after = 0.0
            with mss.mss() as screen:
                while not self.cancel.is_set():
                    started = time.monotonic()
                    shot = screen.grab(self.settings.region.as_mss())
                    image = Image.frombytes("RGB", shot.size, shot.rgb)
                    recognized = normalize_subtitle(reader.read(image))
                    if not has_readable_text(recognized):
                        blank_count += 1
                        if blank_count >= 2 and previous:
                            previous = ""
                            self.output.put(Message("caption"))
                    else:
                        blank_count = 0
                        if recognized != previous and started >= retry_after:
                            try:
                                rendered = client.translate(recognized) if client else to_simplified(recognized)
                            except Exception as exc:
                                retry_after = time.monotonic() + 5.0
                                self.output.put(Message("error", str(exc)))
                            else:
                                previous = recognized
                                retry_after = 0.0
                                self.output.put(Message("caption", rendered, recognized))
                    self.cancel.wait(max(0.05, self.settings.interval - (time.monotonic() - started)))
        except Exception as exc:
            self.output.put(Message("error", f"{type(exc).__name__}: {exc}"))
        finally:
            self.output.put(Message("finished"))
