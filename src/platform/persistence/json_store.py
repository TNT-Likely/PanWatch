"""Tiny JSON file store with atomic writes.

Used for lightweight state persistence (e.g. event-driven intraday triggers).
"""

from __future__ import annotations

import json
import os
from typing import Any


def read_json(path: str, default: Any):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        return default


def write_json_atomic(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
