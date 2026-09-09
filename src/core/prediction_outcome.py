"""Compatibility alias for research prediction evaluation."""

import sys

from src.modules.research import prediction_outcome as _implementation

sys.modules[__name__] = _implementation
