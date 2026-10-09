"""Public instants from UTC persistence, independent of deployment timezone."""

from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator


def as_utc(value: datetime) -> datetime:
    """SQLite removes tzinfo from UTC columns; restore it at the boundary."""
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(timezone.utc)


UTCDateTime = Annotated[datetime, AfterValidator(as_utc)]
