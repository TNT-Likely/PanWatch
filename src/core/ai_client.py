"""Compatibility module alias; implementation moved to platform.ai."""
import sys
from src.platform.ai import ai_client as _implementation
sys.modules[__name__] = _implementation
