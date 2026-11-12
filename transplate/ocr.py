"""RapidOCR adapter: default Chinese model bundled, other language models on demand."""
from __future__ import annotations

import numpy as np
from PIL import Image

from .core import normalize_subtitle
from .settings import data_dir


LANGUAGE_CONFIG = {
    "ch": None,
    "chinese_cht": "chinese_cht",
    "en": "en",
    "japan": "japan",
    "korean": "korean",
    "latin": "latin",
}


class OCRReader:
    def __init__(self, language: str = "ch"):
        from rapidocr import RapidOCR, ModelType, OCRVersion

        if language not in LANGUAGE_CONFIG:
            raise ValueError("不支持的 OCR 语言")
        params = {"Global.log_level": "error"}
        if language != "ch":
            model_dir = data_dir() / "ocr-models"
            model_dir.mkdir(parents=True, exist_ok=True)
            params.update({
                "Global.model_root_dir": str(model_dir),
                "Rec.lang_type": LANGUAGE_CONFIG[language],
                "Rec.ocr_version": OCRVersion.PPOCRV4,
                "Rec.model_type": ModelType.MOBILE,
            })
        self._engine = RapidOCR(params=params)

    def read(self, image: Image.Image) -> str:
        result = self._engine(np.asarray(image.convert("RGB")))
        if result is None or not result.txts:
            return ""
        rows = []
        for i, text in enumerate(result.txts):
            score = float(result.scores[i]) if result.scores is not None else 1.0
            if score < 0.45 or not str(text).strip():
                continue
            if result.boxes is not None and i < len(result.boxes):
                box = np.asarray(result.boxes[i])
                x, y = float(box[:, 0].mean()), float(box[:, 1].mean())
            else:
                x, y = 0.0, float(i)
            rows.append((y, x, str(text)))
        rows.sort(key=lambda item: (round(item[0] / 12), item[1]))
        return normalize_subtitle("\n".join(r[2] for r in rows))
