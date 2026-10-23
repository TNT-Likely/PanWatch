"""Resolve persistent application files under one deployment data directory."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def get_data_dir() -> Path:
    """Keep the historical default; resolve explicit relative paths from cwd."""
    configured = os.environ.get("DATA_DIR", "").strip()
    return (Path(configured).expanduser() if configured else PROJECT_ROOT / "data").resolve()
