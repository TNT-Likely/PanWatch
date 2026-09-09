"""Compatibility alias for persistence JSON safety utilities."""

import sys

from src.platform.persistence import json_safe as _implementation

sys.modules[__name__] = _implementation
