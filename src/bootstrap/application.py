"""Temporary application facade while legacy routers are migrated.

New composition belongs here.  The legacy app remains the concrete factory
until each router has a module-owned replacement.
"""

from __future__ import annotations

from fastapi import FastAPI


def get_application() -> FastAPI:
    """Return the compatible FastAPI application instance."""
    from src.web.app import app

    return app

