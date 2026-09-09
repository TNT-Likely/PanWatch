"""Compatibility alias for research signal explanation."""

import sys

from src.modules.research import signal_explain as _implementation

sys.modules[__name__] = _implementation
