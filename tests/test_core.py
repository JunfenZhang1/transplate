import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from transplate.core import has_readable_text, normalize_subtitle, to_simplified
from transplate.settings import Region, Settings, load_settings, save_settings
from transplate.translator import TranslationClient, TranslationError


class CoreTests(unittest.TestCase):
    def test_t2s(self):
        self.assertEqual(to_simplified("繁體中文測試，歡迎來到臺灣。"), "繁体中文测试，欢迎来到台湾。")

    def test_normalization(self):
        self.assertEqual(normalize_subtitle("  第一  行  \n\n第二\t行 "), "第一 行\n第二 行")
        self.assertFalse(has_readable_text(" ...?! "))
        self.assertTrue(has_readable_text("カタカナ"))

    def test_settings_round_trip_without_secrets(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            saved = Settings(region=Region(-100, 30, 700, 80), mode="translate", language="japan")
            save_settings(saved, path)
            self.assertEqual(load_settings(path), saved)
            self.assertNotIn("api_key", path.read_text(encoding="utf-8"))

    def test_corrupt_settings(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{oops", encoding="utf-8")
            self.assertEqual(load_settings(path), Settings())

    def test_provider_validation(self):
        with self.assertRaises(TranslationError):
            TranslationClient("openai_compatible", "invalid", "model")
        with self.assertRaises(TranslationError):
            TranslationClient("openai_compatible", "http://localhost:11434/v1", "")


if __name__ == "__main__":
    unittest.main()
