"""Version discovery must not depend on the process working directory."""

from __future__ import annotations

from pathlib import Path


def test_version_endpoint_finds_the_repository_version_outside_project_root(monkeypatch, tmp_path):
    from src.modules.administration.api.settings import VERSION_FILE, get_app_version

    expected_file = Path(__file__).resolve().parents[1] / "VERSION"
    assert VERSION_FILE == expected_file

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APP_VERSION", raising=False)

    expected = expected_file.read_text(encoding="utf-8").strip()
    assert get_app_version() == expected
