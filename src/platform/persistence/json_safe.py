from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any


def to_jsonable(value: Any) -> Any:
    """Best-effort convert arbitrary Python objects to JSON-safe values."""
    return _convert(value, seen=set())


def _convert(value: Any, seen: set[int]) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Enum):
        return _convert(value.value, seen)

    oid = id(value)
    if oid in seen:
        return "<circular>"

    if isinstance(value, dict):
        seen.add(oid)
        out = {str(k): _convert(v, seen) for k, v in value.items()}
        seen.discard(oid)
        return out

    if isinstance(value, (list, tuple, set)):
        seen.add(oid)
        out = [_convert(v, seen) for v in value]
        seen.discard(oid)
        return out

    if is_dataclass(value):
        seen.add(oid)
        out = _convert(asdict(value), seen)
        seen.discard(oid)
        return out

    if hasattr(value, "dict") and callable(getattr(value, "dict")):
        try:
            return _convert(value.dict(), seen)
        except Exception:
            pass

    if hasattr(value, "__dict__"):
        seen.add(oid)
        data = {
            k: _convert(v, seen)
            for k, v in vars(value).items()
            if not k.startswith("_")
        }
        seen.discard(oid)
        return data

    return str(value)
