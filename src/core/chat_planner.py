"""Compatibility alias for assistant planning."""

import sys

from src.modules.assistant import chat_planner as _implementation

sys.modules[__name__] = _implementation
