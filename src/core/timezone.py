"""Compatibility alias for scheduling timezone utilities."""

import sys

from src.platform.scheduling import timezone as _implementation

sys.modules[__name__] = _implementation
