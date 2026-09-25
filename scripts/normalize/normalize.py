#!/usr/bin/env python3
"""Canonical normalization entry point (see scripts/build/normalize.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.build.normalize import main

if __name__ == "__main__":
    raise SystemExit(main())
