"""Optional production-asset smoke test; build frontend before running this file."""

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.public_path import PublicPathMiddleware
from src.web.spa import install_spa


DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
pytestmark = pytest.mark.skipif(not (DIST / "index.html").exists(), reason="build frontend first to verify production assets")


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.base = ""
        self.urls = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "base":
            self.base = attrs["href"]
        elif tag == "script" and "src" in attrs:
            self.urls.append(attrs["src"])
        elif tag == "link" and "href" in attrs:
            self.urls.append(attrs["href"])


@pytest.mark.parametrize("prefix,mode", [("", "preserve"), ("/panwatch", "preserve"), ("/apps/panwatch", "preserve"), ("/panwatch", "strip"), ("/apps/panwatch", "strip")])
def test_production_assets_and_pwa_are_confined_to_prefix(prefix, mode):
    app = FastAPI()
    app.add_middleware(PublicPathMiddleware, base_path=prefix, proxy_mode=mode)
    install_spa(app, DIST, prefix)
    client = TestClient(app)

    def get_public(path):
        assert path.startswith(prefix + "/")
        return client.get(path if mode == "preserve" else path[len(prefix):])

    for path in ("/", "/login", "/assistant/123", "/getting-started", "/portfolio", "/settings", "/opportunities", "/evaluations", "/paper-trading", "/agents", "/history", "/datasources", "/alerts"):
        response = get_public(prefix + path)
        assert response.status_code == 200
        parser = AssetParser()
        parser.feed(response.text)
        assert parser.base == prefix + "/"
        for url in parser.urls:
            if urlsplit(url).netloc:  # Existing third-party chart CDN.
                continue
            resolved = urljoin(parser.base, url)
            asset = get_public(resolved)
            assert asset.status_code == 200, resolved
            assert "text/html" not in asset.headers.get("content-type", ""), resolved

    for name in ("manifest.json", "manifest.zh-CN.json"):
        manifest_url = prefix + "/" + name
        manifest = get_public(manifest_url).json()
        for field in ("start_url", "scope"):
            assert urljoin(manifest_url, manifest[field]) == prefix + "/"
        origin = "https://panwatch.example"
        start_url = urljoin(origin + manifest_url, manifest["start_url"])
        # Manifest id resolves against start_url's ORIGIN, not manifest_url.
        # Omitting it inherits start_url and keeps root/nested installs distinct.
        identity = urljoin(origin + "/", manifest["id"]) if manifest.get("id") else start_url
        assert identity == origin + prefix + "/"
        for icon in manifest["icons"]:
            assert get_public(urljoin(manifest_url, icon["src"])).status_code == 200
    assert get_public(prefix + "/sw.js").headers["cache-control"] == "no-cache"
    # All lazy chunks are served too, not just initial HTML references.
    for asset in (DIST / "assets").iterdir():
        if asset.is_file():
            assert get_public(prefix + "/assets/" + asset.name).status_code == 200
