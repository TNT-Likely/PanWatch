"""Compatibility alias for automation catalog services."""

import sys

from src.modules.automation import agent_catalog as _implementation

sys.modules[__name__] = _implementation
