"""Compatibility alias for reporting PDF rendering."""

import sys

from src.modules.reporting import pdf_export as _implementation

sys.modules[__name__] = _implementation
