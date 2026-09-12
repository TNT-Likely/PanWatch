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
            if len(parts) < 4 or parts[2] == own_module:
                continue
            if parts[3] in {"models", "repository", "api"} or parts[3].endswith("_api"):
                module_violations.append(f"{path.relative_to(SOURCE_ROOT)} -> {imported}")

    assert platform_violations == []
    assert module_violations == []


def test_legacy_compatibility_packages_are_removed_after_migration():
    assert not (SOURCE_ROOT / "core" / "__init__.py").exists()
    assert not (SOURCE_ROOT / "agents" / "__init__.py").exists()
    assert not (SOURCE_ROOT / "web" / "database.py").exists()
    assert not (SOURCE_ROOT / "web" / "models.py").exists()
    assert not (SOURCE_ROOT / "web" / "migrations.py").exists()


def test_root_level_market_legacy_packages_are_removed_after_migration():
    assert not (SOURCE_ROOT / "collectors" / "__init__.py").exists()
    assert not (SOURCE_ROOT / "models" / "__init__.py").exists()
    assert not (SOURCE_ROOT / "compat" / "__init__.py").exists()


def test_bootstrap_is_the_only_application_factory():
    assert (SOURCE_ROOT / "bootstrap" / "application.py").is_file()
    assert not (SOURCE_ROOT / "web" / "app.py").exists()


def test_business_routers_are_owned_by_their_modules():
    """The HTTP utility package must not become a second business hierarchy."""
    assert not (SOURCE_ROOT / "web" / "api").exists()


def test_cross_cutting_runtime_support_is_not_owned_by_web_or_src_root():
    """Technical adapters live in their platform capability, not HTTP glue."""
    assert not (SOURCE_ROOT / "config.py").exists()
    assert not (SOURCE_ROOT / "web" / "log_handler.py").exists()
    assert not (SOURCE_ROOT / "web" / "stock_list.py").exists()
    assert (SOURCE_ROOT / "platform" / "runtime" / "config.py").is_file()
    assert (SOURCE_ROOT / "platform" / "observability" / "log_handler.py").is_file()
    assert (SOURCE_ROOT / "platform" / "marketdata" / "stock_list.py").is_file()


def test_marketdata_stock_list_keeps_its_project_level_cache_location():
    """Moving the adapter must not silently create a second cache under src/."""
    from src.platform.marketdata.stock_list import CACHE_FILE

    assert Path(CACHE_FILE).resolve().parent == SOURCE_ROOT.parent / "data"


def test_removed_transition_facades_are_not_reintroduced():
    """Real implementation modules are already stable public boundaries."""
    assert not (SOURCE_ROOT / "modules" / "assistant" / "models.py").exists()
    assert not (SOURCE_ROOT / "modules" / "portfolio" / "models.py").exists()
    assert not (SOURCE_ROOT / "platform" / "ai" / "client.py").exists()
    assert not (SOURCE_ROOT / "platform" / "ai" / "failover.py").exists()
    assert not (SOURCE_ROOT / "platform" / "marketdata" / "client.py").exists()
