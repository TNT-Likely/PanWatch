"""Compatibility alias for administration token utilities."""

import sys

from src.modules.administration import pat as _implementation

sys.modules[__name__] = _implementation
