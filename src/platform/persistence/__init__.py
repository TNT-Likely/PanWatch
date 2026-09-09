"""Database engine, ORM base and migrations shared by the application.

Business tables are physically registered here, while ownership of their
behavior remains in the relevant module service.
"""

from .database import SessionLocal, engine, get_db, init_db
from .models import Base

__all__ = ["Base", "SessionLocal", "engine", "get_db", "init_db"]
