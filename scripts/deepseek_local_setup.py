"""One-use loopback DeepSeek V4 Flash launcher."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gemini_local_setup import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], forced_provider="deepseek"))
