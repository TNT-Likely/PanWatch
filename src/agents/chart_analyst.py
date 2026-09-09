"""Compatibility module alias; implementation moved to modules.automation."""

import sys

from src.modules.automation import chart_analyst as _implementation

sys.modules[__name__] = _implementation
