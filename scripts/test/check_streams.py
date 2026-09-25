#!/usr/bin/env python3
"""Canonical stream health entry point (see scripts/validate/check_streams.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.validate.check_streams import main

if __name__ == "__main__":
    raise SystemExit(main())
