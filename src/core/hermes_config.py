"""Hermes CLI 全局配置 — 从 AppSettings 读取，供 local skill 报告等模块共用。"""

from __future__ import annotations

from dataclasses import dataclass

from src.web.database import SessionLocal
from src.web.models import AppSettings

HERMES_SETTING_KEYS: dict[str, str] = {
    "hermes_bin": "Hermes 可执行文件路径（留空则从 PATH 查找 hermes）",
    "hermes_profile": "Hermes 默认 profile",
    "hermes_skill_source_dir": "本地 skill 源目录（留空默认 ~/.claude/skills）",
    "hermes_model": "Hermes 默认模型（留空用 profile 默认）",
    "hermes_max_turns": "Hermes 单次最大轮数",
    "hermes_timeout_sec": "Hermes 执行超时（秒）",
    "hermes_followup_timeout_sec": "Hermes 续写超时（秒）",
    "hermes_ignore_rules": "Hermes 是否 --ignore-rules（true/false）",
    "hermes_auto_expand_summary": "检测到摘要时自动续写（true/false，老马 skill 可用）",
    "local_skill_scan_dirs": "额外 skill 扫描目录（逗号分隔，绝对路径）",
}

DEFAULT_HERMES_VALUES: dict[str, str] = {
    "hermes_bin": "",
    "hermes_profile": "",
    "hermes_skill_source_dir": "",
    "hermes_model": "",
    "hermes_max_turns": "40",
    "hermes_timeout_sec": "420",
    "hermes_followup_timeout_sec": "300",
    "hermes_ignore_rules": "true",
    "hermes_auto_expand_summary": "true",
    "local_skill_scan_dirs": "",
}


@dataclass(frozen=True)
class HermesConfig:
    hermes_bin: str = ""
    hermes_profile: str = ""
    hermes_skill_source_dir: str = ""
    hermes_model: str = ""
    hermes_max_turns: int = 40
    hermes_timeout_sec: int = 420
    hermes_followup_timeout_sec: int = 300
    hermes_ignore_rules: bool = True
    hermes_auto_expand_summary: bool = True
    local_skill_scan_dirs: str = ""


def _parse_bool(value: str | None, default: bool = True) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def _parse_int(value: str | None, default: int) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def load_hermes_config() -> HermesConfig:
    """从 AppSettings 读取 Hermes 配置；缺失项用默认值。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(AppSettings)
            .filter(AppSettings.key.in_(list(HERMES_SETTING_KEYS.keys())))
            .all()
        )
        data = {**DEFAULT_HERMES_VALUES}
        for row in rows:
            if row.key in data:
                data[row.key] = row.value or data[row.key]
    finally:
        db.close()

    return HermesConfig(
        hermes_bin=(data.get("hermes_bin") or "").strip(),
        hermes_profile=(data.get("hermes_profile") or "").strip(),
        hermes_skill_source_dir=(data.get("hermes_skill_source_dir") or "").strip(),
        hermes_model=(data.get("hermes_model") or "").strip(),
        hermes_max_turns=_parse_int(data.get("hermes_max_turns"), 40),
        hermes_timeout_sec=_parse_int(data.get("hermes_timeout_sec"), 420),
        hermes_followup_timeout_sec=_parse_int(
            data.get("hermes_followup_timeout_sec"), 300
        ),
        hermes_ignore_rules=_parse_bool(data.get("hermes_ignore_rules"), True),
        hermes_auto_expand_summary=_parse_bool(
            data.get("hermes_auto_expand_summary"), True
        ),
        local_skill_scan_dirs=(data.get("local_skill_scan_dirs") or "").strip(),
    )


def local_skill_agent_name(slug: str) -> str:
    """analysis_history / agent_runs 使用的 agent_name。"""
    return f"local_skill:{slug.strip()}"


def parse_local_skill_slug(agent_name: str | None) -> str | None:
    name = (agent_name or "").strip()
    prefix = "local_skill:"
    if name.startswith(prefix):
        slug = name[len(prefix) :].strip()
        return slug or None
    return None
