"""Compatibility alias for administration self-check utilities."""

import sys

from src.modules.administration import selfcheck as _implementation

sys.modules[__name__] = _implementation
