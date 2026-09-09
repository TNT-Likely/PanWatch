"""Compatibility alias for automation run records."""

import sys

from src.modules.automation import agent_runs as _implementation

sys.modules[__name__] = _implementation
