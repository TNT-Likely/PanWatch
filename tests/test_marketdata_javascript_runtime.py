"""Serial first-isolate initialization prevents native V8 startup races."""

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.platform.marketdata import javascript_runtime


def test_warmup_initializes_and_closes_one_context(monkeypatch):
    factory = MagicMock()
    monkeypatch.setitem(sys.modules, "py_mini_racer", SimpleNamespace(MiniRacer=factory))
    monkeypatch.setattr(javascript_runtime, "_ready", False)
    javascript_runtime.warmup_javascript_runtime()
    javascript_runtime.warmup_javascript_runtime()
    factory.assert_called_once_with()
    factory.return_value.__enter__.return_value.eval.assert_called_once_with("1 + 1")
    factory.return_value.__exit__.assert_called_once()


def test_warmup_failure_is_not_marked_ready(monkeypatch):
    factory = MagicMock(side_effect=RuntimeError("initialization failed"))
    monkeypatch.setitem(sys.modules, "py_mini_racer", SimpleNamespace(MiniRacer=factory))
    monkeypatch.setattr(javascript_runtime, "_ready", False)
    with pytest.raises(RuntimeError, match="initialization failed"):
        javascript_runtime.warmup_javascript_runtime()
    assert javascript_runtime._ready is False


def test_warmup_allows_optional_dependency_to_be_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "py_mini_racer", None)
    monkeypatch.setattr(javascript_runtime, "_ready", False)
    javascript_runtime.warmup_javascript_runtime()
    assert javascript_runtime._ready is False


def test_fresh_process_warmup_then_concurrent_native_contexts():
    pytest.importorskip("py_mini_racer")
    script = """
from concurrent.futures import ThreadPoolExecutor
from py_mini_racer import MiniRacer
from src.platform.marketdata.javascript_runtime import warmup_javascript_runtime
warmup_javascript_runtime()
def evaluate(_):
    with MiniRacer() as context:
        return context.eval('1 + 1')
with ThreadPoolExecutor(max_workers=8) as pool:
    assert list(pool.map(evaluate, range(32))) == [2] * 32
"""
    for _ in range(3):
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr[-2000:]
