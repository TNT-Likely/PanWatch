"""Transitional persistence facade.

The ownership change is intentionally non-breaking: legacy imports remain in
``src.web.database`` while new module code uses this stable platform path.
"""

from src.web.database import SessionLocal, engine, get_db, init_db

__all__ = ["SessionLocal", "engine", "get_db", "init_db"]

