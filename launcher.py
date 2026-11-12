"""Thin launcher so a PyInstaller GUI exe can emit self-test diagnostics."""
import os
from pathlib import Path
import sys
import traceback

from transplate.app import main

if __name__ == "__main__":
    try:
        main()
    except Exception:
        if "--self-test" in sys.argv:
            location = Path(os.environ.get("RUNNER_TEMP") or os.environ.get("TEMP") or ".")
            (location / "transplate-selftest-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
