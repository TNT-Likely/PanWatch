"""Compatibility alias for scheduling registration."""

import sys

from src.platform.scheduling import scheduler_registry as _implementation

sys.modules[__name__] = _implementation
