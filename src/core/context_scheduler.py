"""Compatibility alias for research maintenance scheduling."""

import sys

from src.modules.research import context_scheduler as _implementation

sys.modules[__name__] = _implementation
