"""Browser installation must work with empty, reused, and stale data volumes."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("cached_directory", [None, "chromium-1000", "chromium_headless_shell-1000"])
def test_setup_reconciles_browser_cache_with_current_playwright(monkeypatch, tmp_path, cached_directory):
    import server

    browser_dir = tmp_path / "playwright"
    if cached_directory:
        (browser_dir / cached_directory).mkdir(parents=True)
    monkeypatch.setenv("DOCKER", "1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_SKIP_BROWSER_INSTALL", raising=False)
    install = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr("subprocess.run", install)

    server.setup_playwright()

    assert browser_dir.is_dir()
    assert install.call_args.args[0] == ["playwright", "install", "chromium", "--only-shell"]
    assert install.call_args.kwargs["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(browser_dir)


def test_setup_preserves_explicit_browser_path(monkeypatch, tmp_path):
    import server

    browser_dir = tmp_path / "custom-browsers"
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browser_dir))
    monkeypatch.delenv("PLAYWRIGHT_SKIP_BROWSER_INSTALL", raising=False)
    install = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr("subprocess.run", install)

    server.setup_playwright()

    assert install.call_args.kwargs["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(browser_dir)


@pytest.mark.parametrize("skip_install", [False, True])
def test_setup_skips_install_for_local_development_or_explicit_opt_out(monkeypatch, skip_install):
    import server

    monkeypatch.setenv("DOCKER", "1" if skip_install else "0")
    monkeypatch.setenv("PLAYWRIGHT_SKIP_BROWSER_INSTALL", "1" if skip_install else "0")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    install = Mock()
    monkeypatch.setattr("subprocess.run", install)

    server.setup_playwright()

    install.assert_not_called()
