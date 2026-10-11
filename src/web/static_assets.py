"""Contain every SPA/static response within its configured public directory."""
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse


def serve_static_asset(static_dir: str, path: str) -> FileResponse:
    root = Path(static_dir).resolve()

    def contained(relative: str) -> Path:
        if Path(relative).is_absolute() or '\\' in relative or '\x00' in relative:
            raise HTTPException(status_code=404, detail='Not found')
        try:
            candidate = (root / relative).resolve()
        except (OSError, RuntimeError, ValueError):
            raise HTTPException(status_code=404, detail='Not found') from None
        if not candidate.is_relative_to(root):
            raise HTTPException(status_code=404, detail='Not found')
        return candidate

    candidate = contained(path)
    if candidate.is_file():
        return FileResponse(candidate)
    index = contained('index.html')
    if not index.is_file():
        raise HTTPException(status_code=404, detail='Not found')
    return FileResponse(index)
