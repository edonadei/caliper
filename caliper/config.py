"""Caliper's per-user configuration directory, independent of a project."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def config_home() -> Path:
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "caliper"
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "caliper"
