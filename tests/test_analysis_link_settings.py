"""Saved notification URL precedence and the documented subpath migration."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.modules.administration.api.settings import SettingUpdate, list_settings, update_setting
from src.modules.research import analysis_link
from src.platform.persistence.database import Base
from src.platform.persistence.models import AppSettings


@pytest.fixture
def settings_store(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(analysis_link, "SessionLocal", sessions)
    monkeypatch.setenv("PANWATCH_BASE_PATH", "/apps/panwatch")
    monkeypatch.setenv("PANWATCH_BASE_URL", "https://example.test/apps/panwatch")
    yield sessions
    engine.dispose()


@pytest.mark.parametrize("saved_url", ["https://example.test", ""])
@pytest.mark.parametrize("public_base", ["https://example.test", "https://example.test/panwatch", "https://example.test/apps/panwatch"])
def test_existing_saved_url_requires_settings_update(settings_store, saved_url, public_base):
    with settings_store() as db:
        db.add(AppSettings(key="panwatch_base_url", value=saved_url))
        db.commit()
        list_settings(db=db)
    assert analysis_link.get_base_url() == saved_url
    expected_old_link = f"{saved_url}/analysis/T/2026-10-09" if saved_url else ""
    assert analysis_link.analysis_detail_url("T", "2026-10-09") == expected_old_link

    with settings_store() as db:
        update_setting("panwatch_base_url", SettingUpdate(value=public_base), db=db)
    assert analysis_link.analysis_detail_url("AAPL", "2026-10-09") == f"{public_base}/analysis/AAPL/2026-10-09"


def test_environment_only_seeds_a_missing_setting(settings_store, monkeypatch):
    assert analysis_link.get_base_url() == ""
    with settings_store() as db:
        list_settings(db=db)
    assert analysis_link.get_base_url() == "https://example.test/apps/panwatch"
    monkeypatch.setenv("PANWATCH_BASE_URL", "https://other.example.test/changed")
    with settings_store() as db:
        list_settings(db=db)
    assert analysis_link.get_base_url() == "https://example.test/apps/panwatch"


def test_subpath_migration_documents_saved_setting_precedence():
    document = (Path(__file__).resolve().parents[1] / "docs/reverse-proxy-subpath.md").read_text()
    migration = " ".join(document.split("## Verification and migration", 1)[1].split())
    assert "update and save `panwatch_base_url` in **Settings → System**" in migration
    assert "saved database setting takes precedence over `PANWATCH_BASE_URL`" in migration
    assert "does not replace an existing saved value, including an empty one" in migration
    assert "only an initial default" in migration
    assert "open Settings to initialize it" in migration


@pytest.mark.parametrize("public_base", ["https://example.test", "https://example.test/panwatch", "https://example.test/apps/panwatch/"])
@pytest.mark.parametrize(("symbol", "encoded"), [("AAPL", "AAPL"), ("US:AAPL", "US%3AAAPL"), ("A/B ?#", "A%2FB%20%3F%23")])
def test_analysis_links_encode_route_segments_without_changing_public_base(public_base, symbol, encoded):
    assert analysis_link.analysis_detail_url(symbol, "2026/10/09", public_base) == (
        f"{public_base.rstrip('/')}/analysis/{encoded}/2026%2F10%2F09"
    )
