"""Architecture boundaries for the modular-monolith migration.

The legacy ``core``/``web`` paths are intentionally excluded while they are
being migrated.  New code must start with the target dependency direction.
"""

from __future__ import annotations

import ast
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


def _imports_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _python_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*.py") if path.name != "__pycache__")


def test_platform_does_not_import_modules_and_modules_do_not_cross_import_storage():
    platform_root = SOURCE_ROOT / "platform"
    modules_root = SOURCE_ROOT / "modules"

    assert platform_root.is_dir(), "platform package must exist"
    assert modules_root.is_dir(), "modules package must exist"

    platform_violations: list[str] = []
    module_violations: list[str] = []
    for path in _python_files(platform_root):
        for imported in _imports_in(path):
            if imported == "src.modules" or imported.startswith("src.modules."):
                platform_violations.append(f"{path.relative_to(SOURCE_ROOT)} -> {imported}")

    for path in _python_files(modules_root):
        own_module = path.relative_to(modules_root).parts[0]
        for imported in _imports_in(path):
            if not imported.startswith("src.modules."):
                continue
            parts = imported.split(".")
            if len(parts) >= 4 and parts[2] != own_module and parts[3] in {"models", "repository"}:
                module_violations.append(f"{path.relative_to(SOURCE_ROOT)} -> {imported}")

    assert platform_violations == []
    assert module_violations == []


def test_legacy_compatibility_packages_are_removed_after_migration():
    assert not (SOURCE_ROOT / "core" / "__init__.py").exists()
    assert not (SOURCE_ROOT / "agents" / "__init__.py").exists()
    assert not (SOURCE_ROOT / "web" / "database.py").exists()
    assert not (SOURCE_ROOT / "web" / "models.py").exists()
    assert not (SOURCE_ROOT / "web" / "migrations.py").exists()
