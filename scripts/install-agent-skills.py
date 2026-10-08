#!/usr/bin/env python3
"""Expose repository skills to supported agents without touching user profiles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

AGENTS = ("codex", "claude", "cursor", "gemini")
SOURCE_DIR = Path(".agents/skills")
CLAUDE_DIR = Path(".claude/skills")
OWNER = "panwatch-project-skill-installer-v1"


class InstallError(ValueError):
    pass


def safe_path(project: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise InstallError("Only repository-relative paths are accepted")
    current = project
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise InstallError(f"Refusing symlinked parent or source: {relative}")
    if not current.resolve().is_relative_to(project):
        raise InstallError(f"Path leaves this project: {relative}")
    return current


def inventory(directory: Path) -> dict[str, str]:
    result = {}
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise InstallError(f"Skill resources must not be symlinks: {relative}")
        if path.is_file():
            result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def sources(project: Path) -> list[tuple[Path, dict[str, str]]]:
    root = safe_path(project, SOURCE_DIR)
    if not root.is_dir():
        raise InstallError("Project .agents/skills directory is missing")
    result = []
    for source in sorted(root.iterdir()):
        if source.name.startswith("."):
            continue
        safe_path(project, source.relative_to(project))
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            continue
        files = inventory(source)
        text = (source / "SKILL.md").read_text(encoding="utf-8")
        header = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
        name = re.search(r"^name:\s*[\"']?([a-z0-9]+(?:-[a-z0-9]+)*)[\"']?\s*$", header[1], re.M) if header else None
        if not name or name[1] != source.name or not re.search(r"^description:\s*\S", header[1], re.M):
            raise InstallError(f"Invalid skill frontmatter: {source.name}")
        if "[TODO:" in text:
            raise InstallError(f"Unfinished skill scaffold: {source.name}")
        result.append((source, files))
    if not result:
        raise InstallError("No valid project skills found")
    return result


def read_receipt(path: Path, name: str) -> dict | None:
    if path.is_symlink():
        raise InstallError(f"Refusing symlinked installer receipt: {path.name}")
    if not path.exists():
        return None
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise InstallError(f"Invalid installer receipt: {path.name}") from exc
    if not isinstance(receipt, dict) or receipt.get("owner") != OWNER or receipt.get("name") != name or receipt.get("mode") not in ("link", "copy"):
        raise InstallError(f"Receipt is not owned by this installer: {path.name}")
    return receipt


def inspect_entry(target: Path, source: Path, receipt: dict | None) -> str:
    if not target.exists() and not target.is_symlink():
        if receipt:
            raise InstallError(f"Receipt exists but entry is missing: {target.name}")
        return "absent"
    if target.is_symlink():
        if target.resolve() != source:
            raise InstallError(f"Existing skill points elsewhere: {target.name}")
        if receipt and (receipt["mode"] != "link" or os.readlink(target) != receipt.get("link")):
            raise InstallError(f"Installed link has changed: {target.name}")
        return "link" if receipt else "unmanaged-link"
    if not target.is_dir() or not receipt or receipt["mode"] != "copy":
        raise InstallError(f"Refusing to overwrite an existing skill: {target.name}")
    if inventory(target) != receipt.get("files"):
        raise InstallError(f"Installed copy has local changes: {target.name}")
    return "copy"


def operate(project: Path, agents: tuple[str, ...], *, mode: str = "link", check: bool = False, uninstall: bool = False, dry_run: bool = False) -> list[str]:
    project = project.resolve()
    skill_sources = sources(project)
    messages = []
    plans = []
    for agent in agents:
        if agent != "claude":
            messages.append(f"{agent}: shared project source ready (.agents/skills); nothing to {'remove' if uninstall else 'install'}")
            continue
        root = safe_path(project, CLAUDE_DIR)
        for source, files in skill_sources:
            target = root / source.name
            receipt_path = root / f".{source.name}.panwatch-install.json"
            receipt = read_receipt(receipt_path, source.name)
            kind = inspect_entry(target, source, receipt)
            if uninstall:
                if kind == "unmanaged-link":
                    raise InstallError(f"Cannot uninstall an entry not owned by this tool: {source.name}")
                action = "remove" if kind != "absent" else "absent"
            elif check:
                if kind == "absent" or (kind == "copy" and inventory(target) != files):
                    raise InstallError(f"Claude entry missing or out of date: {source.name}; run installer")
                action = "ready"
            elif kind == "unmanaged-link":
                action = "ready (existing matching link, ownership unchanged)"
            elif kind == "absent":
                action = "install"
            elif kind != mode:
                raise InstallError(f"Uninstall the owned {kind} entry before changing mode: {source.name}")
            elif kind == "copy" and receipt["files"] != files:
                action = "update"
            else:
                action = "ready"
            plans.append((source, files, target, receipt_path, action))

    # Inspect every target first, so a conflict cannot leave a partially installed set.
    for source, files, target, receipt_path, action in plans:
        messages.append(f"claude: {'would ' if dry_run else ''}{action} {target.relative_to(project)}")
        if dry_run or check or action.startswith("ready") or action == "absent":
            continue
        if action == "remove":
            if target.is_symlink():
                target.unlink()
            else:
                shutil.rmtree(target)
            receipt_path.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        receipt = {"owner": OWNER, "name": source.name, "mode": mode, "files": files}
        if mode == "link":
            receipt["link"] = os.path.relpath(source, target.parent)
            try:
                target.symlink_to(receipt["link"], target_is_directory=True)
            except OSError as exc:
                raise InstallError("Cannot create project symlink; retry explicitly with --mode copy") from exc
        else:
            stage = Path(tempfile.mkdtemp(prefix=".panwatch-skill-", dir=target.parent))
            try:
                for relative in files:
                    destination = stage / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source / relative, destination)
                if action == "update":
                    shutil.rmtree(target)
                stage.rename(target)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=(*AGENTS, "all"), action="append", help="Repeat to select agents; default: all")
    parser.add_argument("--mode", choices=("link", "copy"), default="link", help="Claude project entry mode")
    parser.add_argument("--dry-run", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Verify filesystem installation without writes")
    group.add_argument("--uninstall", action="store_true", help="Remove only installer-owned Claude entries")
    args = parser.parse_args()
    agents = AGENTS if not args.agent or "all" in args.agent else tuple(dict.fromkeys(args.agent))
    try:
        messages = operate(Path(__file__).resolve().parents[1], agents, mode=args.mode, check=args.check, uninstall=args.uninstall, dry_run=args.dry_run)
    except (InstallError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Project-level skill files checked; agent runtime discovery is a separate check.")
    print("\n".join(messages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
