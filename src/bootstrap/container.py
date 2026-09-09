"""Dependency composition helpers.

This module deliberately contains no business rules.  Module services will be
constructed here as legacy entry points are migrated.
"""

from __future__ import annotations

from sqlalchemy.orm import Session


def session_scope(session: Session) -> Session:
    """Make the initial explicit dependency boundary testable."""
    return session

