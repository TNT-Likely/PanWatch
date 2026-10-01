"""Serial first-isolate initialization prevents native V8 startup races."""

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from src.platform.marketdata import javascript_runtime


def test_warmup_initializes_and_closes_one_context(monkeypatch):
    factory = MagicMock()
    monkeypatch.setitem(sys.modules, "py_mini_racer", SimpleNamespace(MiniRacer=factory))
    monkeypatch.setattr(javascript_runtime, "_ready", False)
    javascript_runtime.warmup_javascript_runtime()
    javascript_runtime.warmup_javascript_runtime()
    factory.assert_called_once_with()
    factory.return_value.eval.assert_called_once_with("1 + 1")
    factory.return_value.close.assert_called_once_with()
    factory.return_value.__enter__.assert_not_called()


def test_warmup_supports_legacy_context_without_context_manager_or_close(monkeypatch):
    context = SimpleNamespace(eval=Mock(return_value=2))
    factory = Mock(return_value=context)
    monkeypatch.setitem(sys.modules, "py_mini_racer", SimpleNamespace(MiniRacer=factory))
    monkeypatch.setattr(javascript_runtime, "_ready", False)
    javascript_runtime.warmup_javascript_runtime()
    javascript_runtime.warmup_javascript_runtime()
    factory.assert_called_once_with()
    context.eval.assert_called_once_with("1 + 1")
    assert javascript_runtime._ready is True


def test_warmup_closes_context_after_evaluation_failure(monkeypatch):
    context = SimpleNamespace(eval=Mock(side_effect=RuntimeError("evaluation failed")), close=Mock())
    monkeypatch.setitem(sys.modules, "py_mini_racer", SimpleNamespace(MiniRacer=lambda: context))
    monkeypatch.setattr(javascript_runtime, "_ready", False)
    with pytest.raises(RuntimeError, match="evaluation failed"):
        javascript_runtime.warmup_javascript_runtime()
    context.close.assert_called_once_with()
    assert javascript_runtime._ready is False


def test_warmup_allows_legacy_distribution_without_native_binary(monkeypatch):
    factory = Mock(side_effect=RuntimeError('Native library not available at /tmp/libmini_racer.glibc.so'))
    monkeypatch.setitem(sys.modules, 'py_mini_racer', SimpleNamespace(MiniRacer=factory))
    monkeypatch.setattr(javascript_runtime, '_ready', False)
    javascript_runtime.warmup_javascript_runtime()
    assert javascript_runtime._ready is False


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
import sys
from py_mini_racer import MiniRacer
from src.platform.marketdata import javascript_runtime
javascript_runtime.warmup_javascript_runtime()
if not javascript_runtime._ready:
    sys.exit(77)
def evaluate(_):
    context = MiniRacer()
    try:
        return context.eval('1 + 1')
    finally:
        close = getattr(context, 'close', None)
        if callable(close):
            close()
with ThreadPoolExecutor(max_workers=8) as pool:
    assert list(pool.map(evaluate, range(32))) == [2] * 32
"""
    for _ in range(3):
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
        if result.returncode == 77:
            pytest.skip('Installed optional MiniRacer distribution has no native binary on this platform')
        assert result.returncode == 0, result.stderr[-2000:]
