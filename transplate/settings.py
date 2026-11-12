"""Local preferences; translation credentials are deliberately not persisted."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    width: int
    height: int

    def as_mss(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class Settings:
    region: Region | None = None
    mode: str = "convert"         # convert | translate
    language: str = "ch"          # ch | chinese_cht | en | japan | korean | latin
    provider: str = "google"      # google | openai_compatible
    api_base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "qwen2.5:3b"
    interval: float = 0.3
    font_size: int = 27


def data_dir() -> Path:
    base = Path(os.environ.get("APPDATA") or (Path.home() / ".config"))
    return base / "Transplate"


def load_settings(path: Path | None = None) -> Settings:
    path = path or data_dir() / "settings.json"
    result = Settings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return result
        for key in ("mode", "language", "provider", "api_base_url", "model"):
            if isinstance(payload.get(key), str):
                setattr(result, key, payload[key])
        if isinstance(payload.get("interval"), (int, float)):
            result.interval = min(5.0, max(0.2, float(payload["interval"])))
        if isinstance(payload.get("font_size"), int):
            result.font_size = min(64, max(14, payload["font_size"]))
        if isinstance(payload.get("region"), dict):
            r = payload["region"]
            rect = Region(*(int(r[k]) for k in ("left", "top", "width", "height")))
            if 0 < rect.width <= 10000 and 0 < rect.height <= 10000:
                result.region = rect
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        pass
    return result


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or data_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
