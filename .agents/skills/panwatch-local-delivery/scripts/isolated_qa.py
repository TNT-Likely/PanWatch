#!/usr/bin/env python3
"""Prepare a private source snapshot and verify known PanWatch isolation paths."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import uuid

OWNER = "panwatch-isolated-qa-v1"
SKILL_PATH = Path(".agents/skills/panwatch-local-delivery")
EXCLUDED = {".git", ".docs", ".venv", "venv", ".claude", ".cursor", ".gemini", ".codex", ".pytest_cache", ".worktrees", ".superpowers", "__pycache__", "node_modules", "dist", "build", "static", "data", ".vite"}
LOCAL_MODULES = ("marketdata", "pan_agent", "pan_agent_token_meter", "pan_agent_tool_research")


class IsolationError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(files: dict[str, str]) -> str:
    return digest(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


def inside(root: Path, path: Path) -> Path:
    if not path.resolve().is_relative_to(root):
        raise IsolationError(f"Path escapes this QA run: {path.name}")
    current = path
    while current != root:
        if current.is_symlink():
            raise IsolationError(f"Symlink is not allowed in QA-owned paths: {path.name}")
        if current.parent == current:
            raise IsolationError("QA path has no run parent")
        current = current.parent
    return path


def included(relative: Path) -> bool:
    return not (
        any(part in EXCLUDED for part in relative.parts)
        or relative.name == "AGENTS.md"
        or relative.name.startswith(".env")
        or relative.name.endswith((".pyc", ".tsbuildinfo", ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".log", ".bak"))
    )


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def source_files(repo: Path) -> dict[str, str]:
    paths = subprocess.check_output(["git", "-C", str(repo), "ls-files", "--cached", "--others", "--exclude-standard", "-z"]).decode().split("\0")
    files = {}
    for name in sorted(set(paths)):
        if not name or not included(Path(name)):
            continue
        path = inside(repo, repo / name)
        if not path.exists():
            continue  # Preserve a deletion in the exported working tree.
        if not path.is_file():
            raise IsolationError(f"Cannot snapshot a submodule or non-file: {name}")
        files[name] = digest(path.read_bytes())
    return files


def snapshot_files(source: Path) -> dict[str, str]:
    files = {}
    for directory, dirs, names in os.walk(source, followlinks=False):
        base = Path(directory)
        for name in dirs:
            if name != "node_modules":
                inside(source, base / name)
        dirs[:] = [name for name in dirs if name not in EXCLUDED]
        for name in names:
            path = base / name
            relative = path.relative_to(source)
            if included(relative):
                inside(source, path)
                files[relative.as_posix()] = digest(path.read_bytes())
    return files


def assignment(path: Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = [node.value for node in ast.walk(tree) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    if len(values) != 1:
        raise IsolationError(f"Review changed path mapping: {path.name}:{name}")
    return values[0]


def check_mapping(source: Path) -> None:
    expressions = (
        ("src/platform/persistence/database.py", "DB_PATH", "os.path.join(os.path.dirname(__file__), '..', '..', '..', 'data', 'panwatch.db')"),
        ("src/platform/marketdata/stock_list.py", "PROJECT_ROOT", "Path(__file__).resolve().parents[3]"),
        ("src/platform/marketdata/stock_list.py", "DATA_DIR", "PROJECT_ROOT / 'data'"),
        ("server.py", "static_dir", "os.path.join(os.path.dirname(__file__), 'static')"),
        ("server.py", "bundle_path", "os.path.join(os.path.dirname(__file__), 'data', 'ca-bundle.pem')"),
    )
    for file, name, expression in expressions:
        expected = ast.parse(expression, mode="eval").body
        if ast.dump(assignment(source / file, name)) != ast.dump(expected):
            raise IsolationError(f"Known mapping changed; review before service import: {file}:{name}")
    config = assignment(source / "src/platform/runtime/config.py", "model_config")
    if not isinstance(config, ast.Dict):
        raise IsolationError("Settings configuration needs a new isolation review")
    fields = {key.value: value.value for key, value in zip(config.keys, config.values) if isinstance(key, ast.Constant) and isinstance(value, ast.Constant)}
    if fields.get("env_file") != ".env":
        raise IsolationError("Settings env_file mapping changed")


def tree_inventory(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        inside(root, path)
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return result


def prepare(repo: Path, parent: Path | None, python: Path, port: int | None = None) -> Path:
    repo = repo.resolve()
    if Path(git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
        raise IsolationError("--repo must be the repository root")
    python = python.absolute()  # Do not resolve a venv executable to the system Python.
    if not python.is_file() or not os.access(python, os.X_OK):
        raise IsolationError("Choose an existing Python executable with project dependencies")
    parent = (parent or Path(tempfile.gettempdir())).resolve()
    if parent.is_relative_to(repo):
        raise IsolationError("QA run must be outside the protected checkout")
    files = source_files(repo)
    if not files or f"{SKILL_PATH}/SKILL.md" not in files:
        raise IsolationError("Project skill is absent from the source snapshot")
    if port is None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
    if not 1024 <= port <= 65535:
        raise IsolationError("QA port must be between 1024 and 65535")
    run = Path(tempfile.mkdtemp(prefix="panwatch-qa-", dir=parent)).resolve()
    run.chmod(0o700)
    for name in ("source", "private", "tmp", "cache", "evidence", "report"):
        (run / name).mkdir(mode=0o700)
    source = run / "source"
    for relative, expected in files.items():
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / relative, destination)
        if digest(destination.read_bytes()) != expected:
            raise IsolationError("Working tree changed during export; prepare a new run")
    check_mapping(source)
    (source / "data").mkdir(mode=0o700)
    (source / ".env").touch(mode=0o600)
    run_id = str(uuid.uuid4())
    (run / ".panwatch-qa-run").write_text(run_id, encoding="utf-8")
    (run / ".panwatch-qa-run").chmod(0o600)
    credentials = {"username": f"qa-{run_id[:8]}", "password": secrets.token_urlsafe(32), "jwt_secret": secrets.token_hex(32)}
    write_json(run / "private/credentials.json", credentials)
    manifest = {
        "owner": OWNER, "run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
        "protected_repo": str(repo), "base_sha": git(repo, "rev-parse", "HEAD"),
        "source_files": files, "source_hash": content_hash(files),
        "skill_version": re.search(r'^\s+version:\s*[\"\x27]?([\w.-]+)', (source / SKILL_PATH / "SKILL.md").read_text(), re.M)[1],
        "skill_hash": content_hash({name: sha for name, sha in files.items() if name.startswith(f"{SKILL_PATH}/")}),
        "python": str(python), "port": port, "origin": f"http://127.0.0.1:{port}",
        "credentials_hash": digest((run / "private/credentials.json").read_bytes()),
        "paths": {"cwd": "source", "database": "source/data/panwatch.db", "stock_cache": "source/data/stock_list_cache.json", "data": "source/data", "static": "source/static", "config": "source/.env", "private": "private", "tmp": "tmp", "cache": "cache", "evidence": "evidence", "report": "report"},
    }
    write_json(run / "manifest.json", manifest)
    preflight(run, check_source=True)
    return run


def load_run(run: Path) -> tuple[Path, dict]:
    if run.is_symlink():
        raise IsolationError("Run directory must not be a symlink")
    run = run.resolve()
    if stat.S_IMODE(run.stat().st_mode) & 0o077:
        raise IsolationError("QA run directory must be private (0700)")
    for relative in ("manifest.json", ".panwatch-qa-run", "private", "private/credentials.json"):
        inside(run, run / relative)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("owner") != OWNER or (run / ".panwatch-qa-run").read_text() != manifest.get("run_id"):
        raise IsolationError("QA ownership marker mismatch")
    repo = Path(manifest["protected_repo"]).resolve()
    if run.is_relative_to(repo) or repo.is_relative_to(run):
        raise IsolationError("QA run overlaps the protected checkout")
    return run, manifest


def preflight(run: Path, *, check_source: bool = False, require_frontend: bool = False) -> dict:
    run, manifest = load_run(run)
    for relative in manifest["paths"].values():
        value = Path(relative)
        if value.is_absolute() or ".." in value.parts:
            raise IsolationError("Manifest contains an invalid QA path")
        inside(run, run / value)
    for name in ("source", "private", "tmp", "cache", "evidence", "report", "source/data"):
        path = inside(run, run / name)
        if not path.is_dir():
            raise IsolationError(f"Required QA directory is missing: {name}")
    source = run / "source"
    files = snapshot_files(source)
    if files != manifest["source_files"] or content_hash(files) != manifest["source_hash"]:
        raise IsolationError("QA source content differs from its manifest")
    check_mapping(source)
    env = inside(run, source / ".env")
    if env.read_bytes() != b"":
        raise IsolationError("QA .env must be the generated empty configuration")
    for path in (run / "private/credentials.json", env):
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise IsolationError(f"Private configuration has unsafe permissions: {path.name}")
    if digest((run / "private/credentials.json").read_bytes()) != manifest["credentials_hash"]:
        raise IsolationError("QA credential identity changed")
    # Check all data/cache paths, including files created after preparation.
    for name in ("data",):
        tree_inventory(source / name)
    tree_inventory(run / "cache")
    if check_source and (git(Path(manifest["protected_repo"]), "rev-parse", "HEAD") != manifest["base_sha"] or content_hash(source_files(Path(manifest["protected_repo"]))) != manifest["source_hash"]):
        raise IsolationError("Protected working tree changed; prepare a new QA run")
    if not 1024 <= manifest["port"] <= 65535 or manifest["origin"] != f"http://127.0.0.1:{manifest['port']}":
        raise IsolationError("QA origin must be loopback with the recorded port")
    if require_frontend or (source / "static").exists():
        static = inside(run, source / "static")
        if not (static / "index.html").is_file() or tree_inventory(static) != manifest.get("frontend_files"):
            raise IsolationError("Frontend is missing, unsealed or changed")
    return manifest


def seal_frontend(run: Path) -> dict:
    manifest = preflight(run, check_source=True)
    run = run.resolve()
    dist = inside(run, run / "source/frontend/dist")
    if not (dist / "index.html").is_file():
        raise IsolationError("Build the QA frontend before sealing it")
    files = tree_inventory(dist)
    static = inside(run, run / "source/static")
    if static.exists():
        raise IsolationError("Static build already exists; use a new run for a new build")
    shutil.copytree(dist, static)
    manifest.update({"frontend_files": files, "frontend_hash": content_hash(files), "frontend_built_at": datetime.now(timezone.utc).isoformat()})
    write_json(run / "manifest.json", manifest)
    preflight(run, require_frontend=True)
    return manifest


def child_environment(run: Path) -> dict[str, str]:
    run = run.resolve()
    credentials = json.loads((run / "private/credentials.json").read_text())
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR") if key in os.environ}
    env.update({"TZ": "Asia/Shanghai", "DATA_DIR": str(run / "source/data"), "TMPDIR": str(run / "tmp"), "TMP": str(run / "tmp"), "TEMP": str(run / "tmp"), "XDG_CACHE_HOME": str(run / "cache"), "PLAYWRIGHT_BROWSERS_PATH": str(run / "cache/playwright"), "PLAYWRIGHT_SKIP_BROWSER_INSTALL": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "DEV_RELOAD": "0", "NO_PROXY": "localhost,127.0.0.1,::1", "AUTH_USERNAME": credentials["username"], "AUTH_PASSWORD": credentials["password"], "JWT_SECRET": credentials["jwt_secret"]})
    return env


# The child verifies editable-package origins before importing any application DB.
BOOTSTRAP = r'''
import importlib.util, json, os, pathlib, subprocess, sys
from datetime import datetime, timezone
run = pathlib.Path(sys.argv[1]).resolve()
source = run / "source"
sys.path[:0] = [str(source)] + [str(p) for p in sorted((source / "packages").glob("*/src"))]
origins = {}
for name in ("marketdata", "pan_agent", "pan_agent_token_meter", "pan_agent_tool_research"):
    spec = importlib.util.find_spec(name)
    if spec is None or spec.origin is None or not pathlib.Path(spec.origin).resolve().is_relative_to(source):
        raise SystemExit("QA module origin rejected: " + name)
    origins[name] = spec.origin
from src.platform.persistence import database
db = pathlib.Path(database.DB_PATH).resolve()
engine_db = pathlib.Path(database.engine.url.database).resolve()
if db != source / "data/panwatch.db" or engine_db != db:
    raise SystemExit("QA effective database path rejected")
manifest = json.loads((run / "manifest.json").read_text())
try:
    identity = subprocess.check_output(["ps", "-p", str(os.getpid()), "-o", "lstart=", "-o", "command="], text=True).strip()
except (OSError, subprocess.CalledProcessError):
    identity = None
options = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
runtime = {"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat(), "process_identity": identity, "run_id": manifest["run_id"], "source_hash": manifest["source_hash"], "frontend_hash": manifest.get("frontend_hash"), "origin": manifest["origin"], "module_origins": origins, "database": str(db), "config": str(source / ".env"), "cwd": str(pathlib.Path.cwd()), "readiness": "not_checked", "source_drift_allowed": options.get("allow_source_drift", False), "api_only": options.get("api_only", False)}
path = run / "private/runtime.json"
path.write_text(json.dumps(runtime, indent=2) + "\n")
path.chmod(0o600)
import uvicorn
uvicorn.run("server:app", host="127.0.0.1", port=manifest["port"], reload=False)
'''


def assert_port_available(port: int) -> None:
    with socket.socket() as sock:
        # Match the server's restart behavior: TIME_WAIT is not a live service.
        # listen() still rejects another active listener, including on macOS.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            sock.listen(1)
        except OSError as exc:
            raise IsolationError("QA port is occupied; never reuse the service already listening") from exc


def serve(run: Path, *, api_only: bool, allow_source_drift: bool) -> None:
    manifest = preflight(run, check_source=not allow_source_drift, require_frontend=not api_only)
    run = run.resolve()
    assert_port_available(manifest["port"])
    python = Path(manifest["python"])
    if not python.is_file() or not os.access(python, os.X_OK):
        raise IsolationError("Recorded Python executable is unavailable")
    env = child_environment(run)
    os.chdir(run / "source")
    options = json.dumps({"api_only": api_only, "allow_source_drift": allow_source_drift})
    os.execve(str(python), [str(python), "-c", BOOTSTRAP, str(run), options], env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--repo", type=Path, default=Path.cwd())
    prepare_parser.add_argument("--parent", type=Path)
    prepare_parser.add_argument("--python", type=Path, default=Path(sys.executable))
    prepare_parser.add_argument("--port", type=int)
    for command in ("preflight", "seal-frontend", "serve"):
        sub = commands.add_parser(command)
        sub.add_argument("--run", type=Path, required=True)
        if command == "preflight":
            sub.add_argument("--check-source", action="store_true")
            sub.add_argument("--require-frontend", action="store_true")
        if command == "serve":
            sub.add_argument("--api-only", action="store_true")
            sub.add_argument("--allow-source-drift", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            run = prepare(args.repo, args.parent, args.python, args.port)
            print(f"Private QA run: {run}\nCredentials remain in private/credentials.json; no service was started.")
        elif args.command == "preflight":
            result = preflight(args.run, check_source=args.check_source, require_frontend=args.require_frontend)
            print(f"PASS: isolation preflight; run={result['run_id']}; source={result['source_hash']}; business acceptance not executed")
        elif args.command == "seal-frontend":
            result = seal_frontend(args.run)
            print(f"Sealed QA frontend: {result['frontend_hash']}; UI acceptance not executed")
        else:
            serve(args.run, api_only=args.api_only, allow_source_drift=args.allow_source_drift)
    except (IsolationError, OSError, ValueError, KeyError, SyntaxError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
