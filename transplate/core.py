"""Pure text-processing helpers, independent of screen capture or Qt."""
from __future__ import annotations

from functools import lru_cache
import re


@lru_cache(maxsize=1)
def _opencc():
    from opencc import OpenCC
    return OpenCC("t2s")


def to_simplified(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return _opencc().convert(text)


def normalize_subtitle(text: str) -> str:
    """Keep line breaks, trim repeated horizontal whitespace and empty lines."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    lines = [re.sub(r"[ \t\u3000]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)[:1200]


def has_readable_text(text: str) -> bool:
    return bool(re.search(r"[\w\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", text))
