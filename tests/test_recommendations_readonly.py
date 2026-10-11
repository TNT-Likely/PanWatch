"""Read requests cannot refresh persistent entry candidates."""

from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.research.api import recommendations


@pytest.fixture
def client(monkeypatch):
    listing = Mock(return_value={"snapshot_date": "2026-10-11", "count": 0, "items": []})
    refresh = Mock(return_value={"snapshot_date": "2026-10-11", "count": 1, "items": []})
    signals = Mock()
    monkeypatch.setattr(recommendations, "list_entry_candidates", listing)
    monkeypatch.setattr(recommendations, "refresh_entry_candidates", refresh)
    monkeypatch.setattr(recommendations, "refresh_strategy_signals", signals)
    app = FastAPI()
    app.include_router(recommendations.router, prefix="/api/recommendations")
    with TestClient(app) as http:
        yield http, listing, refresh, signals


@pytest.mark.parametrize("query", ["", "?refresh=false", "?refresh=0"])
def test_candidate_get_only_lists_saved_rows(client, query):
    http, listing, refresh, signals = client
    response = http.get(f"/api/recommendations/entry-candidates{query}")
    assert response.status_code == 200
    assert response.json() == listing.return_value
    listing.assert_called_once()
    refresh.assert_not_called()
    signals.assert_not_called()


@pytest.mark.parametrize("value", ["true", "1", "yes"])
def test_legacy_get_refresh_requires_explicit_post(client, value):
    http, listing, refresh, signals = client
    response = http.get("/api/recommendations/entry-candidates", params={"refresh": value})
    assert response.status_code == 400
    error = response.json()["detail"]
    assert error["code"] == "entry_candidates_refresh_requires_post"
    assert "POST /api/recommendations/entry-candidates/refresh" in error["message"]
    listing.assert_not_called()
    refresh.assert_not_called()
    signals.assert_not_called()


@pytest.mark.parametrize("path", ["/entry-candidates?refresh=true", "/entry-candidates/refresh"])
def test_head_never_refreshes_candidates(client, path):
    http, listing, refresh, signals = client
    assert http.head(f"/api/recommendations{path}").status_code == 405
    listing.assert_not_called()
    refresh.assert_not_called()
    signals.assert_not_called()


def test_explicit_post_refreshes_candidates_and_signals(client):
    http, listing, refresh, signals = client
    response = http.post(
        "/api/recommendations/entry-candidates/refresh",
        params={"max_inputs": 40, "market_scan_limit": 25},
    )
    assert response.status_code == 200
    assert response.json() == refresh.return_value
    refresh.assert_called_once_with(max_inputs=40, market_scan_limit=25)
    signals.assert_called_once_with(snapshot_date="2026-10-11", rebuild_candidates=False)
    listing.assert_not_called()


def test_get_cannot_invoke_post_refresh_endpoint(client):
    http, listing, refresh, signals = client
    assert http.get("/api/recommendations/entry-candidates/refresh").status_code == 405
    listing.assert_not_called()
    refresh.assert_not_called()
    signals.assert_not_called()
