"""Unauthenticated SPA/static routes must never expose files outside static/."""
import ast
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.static_assets import serve_static_asset


@pytest.fixture
def static_app(tmp_path):
    public = tmp_path / 'static'
    public.mkdir()
    (public / 'index.html').write_text('<html><base href="/" data-panwatch-base><meta name="panwatch-base-path" content="">public app</html>')
    (public / 'assets').mkdir()
    (public / 'assets' / 'app.js').write_text('public javascript')
    (tmp_path / 'private-canary.txt').write_text('PRIVATE_FILE_CANARY')
    app = FastAPI()
    # Execute the actual production route function, without running unrelated
    # schedulers or requiring a production DB merely to test public serving.
    tree = ast.parse((Path(__file__).parents[1] / 'server.py').read_text())
    handler = next((node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'serve_spa'), None)
    if handler is not None:
        namespace = {'app': app, 'static_dir': str(public), 'serve_static_asset': serve_static_asset}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[])), 'server.py', 'exec'), namespace)
    else:
        # The independent subpath feature moves the production handler here.
        assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'install_spa' for node in ast.walk(tree))
        from src.web.spa import install_spa
        install_spa(app, public)
    return TestClient(app), public


@pytest.mark.parametrize('path', ['/..%2fprivate-canary.txt', '/%2e%2e/private-canary.txt', '/assets/..%2f..%2fprivate-canary.txt', '/..%5cprivate-canary.txt', '/%2fetc/passwd', '/%00'])
def test_production_spa_rejects_path_escape(static_app, path):
    client, _ = static_app
    response = client.get(path)
    assert response.status_code == 404
    assert 'PRIVATE_FILE_CANARY' not in response.text


def test_production_spa_serves_assets_and_routes(static_app):
    client, _ = static_app
    assert client.get('/assets/app.js').text == 'public javascript'
    assert 'public app' in client.get('/portfolio').text
    assert client.get('/').status_code == 200


def test_production_spa_rejects_symlink_escape(static_app):
    client, public = static_app
    (public / 'leak.txt').symlink_to(public.parent / 'private-canary.txt')
    assert client.get('/leak.txt').status_code == 404
    (public / 'index.html').unlink()
    (public / 'index.html').symlink_to(public.parent / 'private-canary.txt')
    response = client.get('/portfolio')
    assert response.status_code == 404
    assert 'PRIVATE_FILE_CANARY' not in response.text
