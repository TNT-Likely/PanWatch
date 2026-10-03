"""Run blocking database operations with a worker-owned session."""

import asyncio
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.orm import Session


T = TypeVar("T")


async def run_db_operation(bind, operation: Callable[[Session], T]) -> T:
    """Finish an in-flight transaction before propagating cancellation.

    Return plain data or fully loaded values; the worker closes its session
    before returning and never shares the caller's identity map.
    """
    def run():
        with Session(bind=bind, expire_on_commit=False) as db:
            return operation(db)

    pending = asyncio.create_task(asyncio.to_thread(run))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        await pending
        raise
