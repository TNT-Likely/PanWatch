"""Compatibility alias for platform persistence migrations."""

import sys

from src.platform.persistence import migrations as _implementation

sys.modules[__name__] = _implementation
