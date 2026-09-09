"""Compatibility alias for administration update-check utilities."""

import sys

from src.modules.administration import update_checker as _implementation

sys.modules[__name__] = _implementation
