"""Fast, change-aware OCR; asynchronous translation keeps capture responsive."""
from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
import time

import numpy as np
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


class FrameChangeDetector:
    """Avoid repeat OCR on identical frames; force periodic resampling as backup."""

    def __init__(self):
        self._previous: np.ndarray | None = None

    def changed(self, image: Image.Image) -> bool:
        sample = np.asarray(image.convert("L").resize((160, 40), Image.Resampling.BILINEAR))
        previous, self._previous = self._previous, sample
        if previous is None or previous.shape != sample.shape:
            return True
        delta = np.abs(sample.astype(np.int16) - previous.astype(np.int16))
        # Conservative thresholds: avoid skipping small subtitle edits.
        return bool(np.mean(delta) > 0.6 or np.count_nonzero(delta >= 10) > 10)


class LatestSubtitleTranslator(threading.Thread):
    """Only the most recent subtitle is allowed to update the overlay."""

    def __init__(self, client: TranslationClient, output: queue.Queue[Message],
                 cancel: threading.Event):
        super().__init__(name="transplate-translation", daemon=True)
        self.client = client
        self.output = output
        self.cancel = cancel
        self.changed = threading.Condition()
        self.latest: tuple[int, str] = (0, "")

    def submit(self, generation: int, text: str) -> None:
        with self.changed:
            self.latest = (generation, text)
            self.changed.notify()

    def is_latest(self, generation: int) -> bool:
        with self.changed:
            return self.latest[0] == generation and not self.cancel.is_set()

    def stop(self) -> None:
        with self.changed:
            self.changed.notify_all()

    def run(self) -> None:
        handled = 0
        while not self.cancel.is_set():
            with self.changed:
                self.changed.wait_for(
                    lambda: self.latest[0] > handled or self.cancel.is_set(),
                    timeout=0.4,
                )
                if self.cancel.is_set():
                    return
                generation, source = self.latest
            if generation <= handled:
                continue
            handled = generation
            if not source:
                continue
            try:
                rendered = self.client.translate(source)
            except Exception as exc:
                if self.is_latest(generation):
                    self.output.put(Message("error", str(exc)))
                    # Keep retrying the latest subtitle after a short backoff.
                    if not self.cancel.wait(3):
                        with self.changed:
                            if self.latest[0] == generation:
                                handled = generation - 1
                continue
            if self.is_latest(generation):
                self.output.put(Message("caption", rendered, source))


class SubtitleWorker(threading.Thread):
    def __init__(self, settings: Settings, api_key: str, output: queue.Queue[Message]):
        super().__init__(name="transplate-capture", daemon=True)
        self.settings = settings
        self.api_key = api_key
        self.output = output
        self.cancel = threading.Event()
        self._translator: LatestSubtitleTranslator | None = None

    def stop(self) -> None:
        self.cancel.set()
        if self._translator:
            self._translator.stop()

    def run(self) -> None:
        try:
            import mss
            if self.settings.region is None:
                raise RuntimeError("尚未选择字幕区域")
            self.output.put(Message("info", "正在初始化 OCR 模型（新语种首次使用需下载模型）…"))
            reader = OCRReader(self.settings.language)
            if self.cancel.is_set():
                return
            if self.settings.mode == "translate":
                client = TranslationClient(
                    self.settings.provider, self.settings.api_base_url,
                    self.settings.model, self.api_key,
                )
                self._translator = LatestSubtitleTranslator(client, self.output, self.cancel)
                self._translator.start()
            self.output.put(Message("info", "正在监测字幕区域（低延迟模式）"))
            last_text = ""
            blank_count = 0
            generation = 0
            detector = FrameChangeDetector()
            last_ocr_at = 0.0
            with mss.mss() as screen:
                while not self.cancel.is_set():
                    started = time.monotonic()
                    shot = screen.grab(self.settings.region.as_mss())
                    image = Image.frombytes("RGB", shot.size, shot.rgb)
                    visually_changed = detector.changed(image)
                    # Even when pixels look unchanged, retry OCR periodically.
                    if (not visually_changed and blank_count == 0
                            and started - last_ocr_at < 1.5):
                        self.cancel.wait(max(0.02, self.settings.interval -
                                             (time.monotonic() - started)))
                        continue
                    last_ocr_at = started
                    recognized = normalize_subtitle(reader.read(image))
                    if not has_readable_text(recognized):
                        blank_count += 1
                        if blank_count >= 2 and last_text:
                            last_text = ""
                            generation += 1
                            if self._translator:
                                self._translator.submit(generation, "")
                            self.output.put(Message("caption"))
                    elif recognized != last_text:
                        blank_count = 0
                        last_text = recognized
                        generation += 1
                        if self._translator:
                            self._translator.submit(generation, recognized)
                        else:
                            self.output.put(Message("caption", to_simplified(recognized),
                                                    recognized))
                    else:
                        blank_count = 0
                    self.cancel.wait(max(0.02, self.settings.interval -
                                         (time.monotonic() - started)))
        except Exception as exc:
            self.output.put(Message("error", f"{type(exc).__name__}: {exc}"))
        finally:
            self.stop()
            self.output.put(Message("finished"))
