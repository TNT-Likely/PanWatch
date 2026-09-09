"""Compatibility alias for administration diagnostics."""

import sys

from src.modules.administration import doctor as _implementation

sys.modules[__name__] = _implementation
