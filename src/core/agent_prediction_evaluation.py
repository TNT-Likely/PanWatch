"""Compatibility alias for automation prediction evaluation."""

import sys

from src.modules.automation import agent_prediction_evaluation as _implementation

sys.modules[__name__] = _implementation
