"""Build-time source selection must not become arbitrary pip/shell input."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/prepare-tradingagents-requirements.py"
spec = importlib.util.spec_from_file_location("ta_requirements", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
FORK = "git+https://github.com/idkwhodatis/TradingAgents.git@c12865ae3d4be7790853a1e047d067a0a9931821"


@pytest.mark.parametrize("source,expected", [("", None), ("0.6.0", "tradingagents==0.6.0"),
    ("1.2.3rc1", "tradingagents==1.2.3rc1"), (FORK, "tradingagents @ " + FORK)])
def test_source_grammar(source, expected):
    assert module.source_requirement(source) == expected


@pytest.mark.parametrize("source", [" ", "0.6.0\nother-package", "0.6.0 --index-url x", "tradingagents==0.6.0",
    "other-package==0.6.0", "-r evil.txt", "0.6.0; python_version > '3'", "$(touch owned)",
    FORK + "#subdirectory=x", FORK + "?secret=TOKEN", FORK.replace("github.com", "TOKEN@github.com"),
    FORK.replace("github.com", "user:TOKEN@github.com"), FORK.replace("git+https", "git+ssh"),
    FORK.replace("c12865ae3d4be7790853a1e047d067a0a9931821", "main"), FORK + "\n", "file:///tmp/repo", FORK.replace("github.com", "github..com"), FORK.replace("github.com", "github.-com")])
def test_bad_source_is_rejected_without_echo(source):
    with pytest.raises(ValueError) as exc:
        module.source_requirement(source)
    if source.strip():
        assert source not in str(exc.value)
    assert "TOKEN" not in str(exc.value)


def test_default_preserves_entire_manifest():
    text = (ROOT / "requirements-runtime.txt").read_text()
    assert module.prepare_manifest(text) == text
    assert "TauricResearch/TradingAgents.git@2d17df8da1536c121e4d7395ac5a5dcec9e96d6f" in text
    assert "idkwhodatis" not in text


def test_only_selected_dependency_changes_and_noneditable_is_explicit():
    text = "# comment\n-r more.txt\n-e ./packages/marketdata\nother==2\ntradingagents @ git+https://example.com/owner/repo.git@v1\n"
    expected = text.replace(text.splitlines()[-1], "tradingagents @ " + FORK)
    assert module.prepare_manifest(text, FORK) == expected
    assert module.prepare_manifest(text, FORK, non_editable=True) == expected.replace("-e ./", "./")


@pytest.mark.parametrize("text", ["other==2\n", "tradingagents==1\ntradingagents==2\n"])
def test_missing_or_duplicate_dependency_rejected(text):
    with pytest.raises(ValueError):
        module.prepare_manifest(text, FORK)


def test_cli_bad_source_does_not_echo_credentials():
    result = subprocess.run([sys.executable, str(SCRIPT), "--source", FORK.replace("github.com", "TOKEN@github.com")], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "TOKEN" not in result.stdout + result.stderr


def test_cli_rejects_wrong_working_directory(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0
    assert "repository root" in result.stderr


def test_cli_rejects_replacing_source_manifest():
    result = subprocess.run([sys.executable, str(SCRIPT), "--output", "requirements-runtime.txt"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0


def test_incompatible_custom_api_fails_availability(monkeypatch):
    from types import SimpleNamespace
    from src.modules.automation.tradingagents import upstream_compat as compat
    from src.modules.automation.tradingagents.agent import TradingAgentsAgent
    monkeypatch.setattr(compat, "vendor_router", lambda: SimpleNamespace(route_to_vendor=lambda symbol: None))
    available, error = TradingAgentsAgent._check_availability(None)
    assert not available
    assert "Unsupported TradingAgents API: route_to_vendor" in error


def test_installed_source_satisfies_runtime_contract():
    from src.modules.automation.tradingagents.upstream_compat import validate_upstream_contract
    validate_upstream_contract()


@pytest.mark.parametrize("arguments", [["--source", "--TOKEN=secret"], ["--TOKEN=secret"], ["--source", "TOKEN\nsecret"]])
def test_argument_parser_never_echoes_rejected_input(arguments):
    result = subprocess.run([sys.executable, str(SCRIPT), *arguments], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "TOKEN" not in result.stdout + result.stderr


def test_missing_graph_api_has_clear_error(monkeypatch):
    from tradingagents.graph.propagation import Propagator
    from src.modules.automation.tradingagents.upstream_compat import validate_upstream_contract
    monkeypatch.delattr(Propagator, "get_graph_args")
    with pytest.raises(ValueError, match="Unsupported TradingAgents API: get_graph_args"):
        validate_upstream_contract()
