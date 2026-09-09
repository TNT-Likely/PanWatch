"""Compatibility alias for automation suggestions."""

import sys

from src.modules.automation import suggestion_pool as _implementation

sys.modules[__name__] = _implementation
