#!/usr/bin/env python3
"""Entry point: python3 wallpick.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wallpick.ui import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
