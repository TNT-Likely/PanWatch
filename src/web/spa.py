"""Runtime-prefix-aware static SPA delivery for the single distributable image."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from src.web.public_path import normalize_base_path


_BASE_MARKER = '<base href="/" data-panwatch-base>'
_META_MARKER = '<meta name="panwatch-base-path" content="">'


def render_spa_html(html: str, base_path: str) -> str:
    prefix = normalize_base_path(base_path)
    if _BASE_MARKER not in html or _META_MARKER not in html:
        raise RuntimeError("Frontend is missing public-path markers; rebuild frontend with this server version")
    return html.replace(_BASE_MARKER, f'<base href="{prefix}/" data-panwatch-base>', 1).replace(
        _META_MARKER, f'<meta name="panwatch-base-path" content="{prefix}">', 1
    )


def _contained_path(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute() or "\\" in relative or "\x00" in relative:
        raise HTTPException(status_code=404)
    try:
        candidate = (root / relative).resolve()
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=404) from None
    if not candidate.is_relative_to(root):
        raise HTTPException(status_code=404)
    return candidate


def install_spa(app: FastAPI, directory: str | Path, base_path: str = "") -> None:
    """Register last, after all API routes; never expose files outside the build."""
    root = Path(directory).resolve()
    index = render_spa_html(_contained_path(root, "index.html").read_text(encoding="utf-8"), base_path)

    @app.get("/{path:path}", include_in_schema=False)
    async def serve_spa(path: str):
        candidate = _contained_path(root, path)
        # A bundle replacement must not turn the public shell into a symlink
        # outside the build, even though its initial HTML is cached in memory.
        _contained_path(root, "index.html")
        if path in ("", "index.html"):
            return HTMLResponse(index, headers={"Cache-Control": "no-cache"})
        if candidate.is_file():
            headers = {"Cache-Control": "no-cache"} if path == "sw.js" else None
            return FileResponse(candidate, headers=headers)
        # Missing API endpoints/assets must not become successful HTML responses.
        if path.split("/", 1)[0] in ("api", "mcp", ".well-known", "assets") or candidate.suffix:
            raise HTTPException(status_code=404)
        return HTMLResponse(index, headers={"Cache-Control": "no-cache"})
