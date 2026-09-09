"""Compatibility alias for platform persistence database services."""

import sys

from src.platform.persistence import database as _implementation

sys.modules[__name__] = _implementation
