"""Compatibility alias for scheduling expression parsing."""

import sys

from src.platform.scheduling import schedule_parser as _implementation

sys.modules[__name__] = _implementation
