import threading
import queue
import time
import unittest

from PIL import Image, ImageDraw

from transplate.worker import FrameChangeDetector, LatestSubtitleTranslator


class DetectorTests(unittest.TestCase):
    def test_identical_frame_is_skipped(self):
        detector = FrameChangeDetector()
        image = Image.new("RGB", (640, 90), "black")
        self.assertTrue(detector.changed(image))
        self.assertFalse(detector.changed(image.copy()))

    def test_new_subtitle_is_detected(self):
        detector = FrameChangeDetector()
        original = Image.new("RGB", (640, 90), "black")
        changed = original.copy()
        ImageDraw.Draw(changed).rectangle((100, 25, 400, 50), fill="white")
        detector.changed(original)
        self.assertTrue(detector.changed(changed))


class DummyTranslator:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def translate(self, text):
        if text == "old":
            self.started.set()
            self.release.wait(timeout=2)
        return "译文-" + text


class TranslatorTests(unittest.TestCase):
    def test_old_translation_result_never_overwrites_newer_subtitle(self):
        client = DummyTranslator()
        output = queue.Queue()
        cancel = threading.Event()
        worker = LatestSubtitleTranslator(client, output, cancel)
        worker.start()
        try:
            worker.submit(1, "old")
            self.assertTrue(client.started.wait(timeout=2))
            worker.submit(2, "new")
            client.release.set()
            result = output.get(timeout=2)
            self.assertEqual((result.kind, result.source), ("caption", "new"))
            self.assertTrue(output.empty())
        finally:
            cancel.set()
            worker.stop()
            worker.join(timeout=3)

    def test_blank_subtitle_invalidates_pending_translation(self):
        client = DummyTranslator()
        output = queue.Queue()
        cancel = threading.Event()
        worker = LatestSubtitleTranslator(client, output, cancel)
        worker.start()
        try:
            worker.submit(1, "old")
            self.assertTrue(client.started.wait(timeout=2))
            worker.submit(2, "")
            client.release.set()
            time.sleep(0.1)
            self.assertTrue(output.empty())
        finally:
            cancel.set()
            worker.stop()
            worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
