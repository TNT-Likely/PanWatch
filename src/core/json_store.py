"""Compatibility alias for persistence JSON storage."""

import sys

from src.platform.persistence import json_store as _implementation

sys.modules[__name__] = _implementation
