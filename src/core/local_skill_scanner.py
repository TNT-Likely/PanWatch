"""扫描本地 Hermes/Claude skill 目录，提取元数据供 Skill 广场使用。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_SCAN_ROOTS = (
    Path.home() / ".claude" / "skills",
    Path.home() / ".cursor" / "skills",
)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
_DESC_RE = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class ScannedSkill:
    slug: str
    display_name: str
    description: str
    skill_path: str
    source_root: str
    skill_md_path: str
    last_seen_at: str


def _parse_frontmatter(text: str) -> tuple[str, str]:
    name = ""
    description = ""
    match = _FRONTMATTER_RE.match(text or "")
    block = match.group(1) if match else (text or "")[:800]
    name_match = _NAME_RE.search(block)
    if name_match:
        name = name_match.group(1).strip().strip('"').strip("'")
    desc_match = _DESC_RE.search(block)
    if desc_match:
        description = desc_match.group(1).strip().strip('"').strip("'")
        description = re.sub(r"\s+", " ", description)
    return name, description


def _expand_scan_roots(extra_dirs: str = "") -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        resolved = path.expanduser().resolve()
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        if resolved.is_dir():
            roots.append(resolved)

    for root in _DEFAULT_SCAN_ROOTS:
        add(root)

    for part in (extra_dirs or "").split(","):
        part = part.strip()
        if part:
            add(Path(part))

    return roots


def _is_safe_child(root: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def scan_local_skills(extra_dirs: str = "") -> list[ScannedSkill]:
    """扫描本地 skill 目录，返回去重后的 skill 列表（按 slug 合并，后者覆盖前者）。"""
    now = datetime.now(timezone.utc).isoformat()
    by_slug: dict[str, ScannedSkill] = {}

    for root in _expand_scan_roots(extra_dirs):
        try:
            entries = sorted(root.iterdir())
        except OSError as e:
            logger.warning("local_skill_scanner: 无法读取目录 %s: %s", root, e)
            continue

        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            if not _is_safe_child(root, entry):
                continue

            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue

            try:
                raw = skill_md.read_text(encoding="utf-8")
            except OSError as e:
                logger.debug("local_skill_scanner: 读取失败 %s: %s", skill_md, e)
                continue

            fm_name, fm_desc = _parse_frontmatter(raw)
            slug = entry.name
            display_name = fm_name or slug
            description = fm_desc or f"本地 skill：{slug}"

            by_slug[slug] = ScannedSkill(
                slug=slug,
                display_name=display_name,
                description=description,
                skill_path=str(entry),
                source_root=str(root),
                skill_md_path=str(skill_md),
                last_seen_at=now,
            )

    return sorted(by_slug.values(), key=lambda s: s.display_name.lower())
