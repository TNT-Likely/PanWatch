"""Compatibility alias for the platform scheduling implementation."""

import sys

from src.platform.scheduling import scheduler as _implementation

sys.modules[__name__] = _implementation
