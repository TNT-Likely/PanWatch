"""本地 Hermes CLI 委托 — 供 lmd_outlook 等 Agent 调用 skill + 工具链。"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_SESSION_ID_RE = re.compile(r"^session_id:\s*(\S+)\s*$", re.MULTILINE)
_DEFAULT_CLAUDE_SKILLS = Path.home() / ".claude" / "skills"

# 完整老马视角成稿应覆盖的主题（至少其三）
_FULL_REPORT_MARKERS = (
    "整体定位",
    "五维",
    "路径推演",
    "诚实边界",
    "风险提示",
)

# 仅摘要、未交付正文的信号
_SUMMARY_ONLY_MARKERS = (
    "执行摘要",
    "报告完成",
    "报告已完成",
    "输出在回复正文中",
    "研究阶段（Step 2）",
    "研究阶段(Step 2)",
    "结论（Step 3）",
    "结论(Step 3)",
)

# 非最终成稿的中间产物文件名片段
_NON_FINAL_REPORT_NAME_PARTS = (
    "_Research_",
    "基本面和行业信息",
    "行业信息汇总",
)

_FOLLOWUP_PROMPT = """你上一轮的回复只是研究摘要/执行摘要或文件 diff，不是可入库的完整报告。

请**立即**在同一话题下输出完整的老马视角 Markdown 成稿（不要再写「报告完成」「执行摘要」「Step 2/Step 3」「review diff」等过程内容）。

必须包含以下二级标题（缺一不可，可在此基础上扩展三级标题）：
## 一、整体定位
## 二、五维周期定位
## 三、路径推演
## 四、诚实边界
## 五、风险提示

要求：
- 单股正文不少于 1800 字；五维须逐项展开论证，引用已研究的具体数字。
- 用第一人称「我」、老马语气；开头声明非投资建议，结尾有风险提示。
- 禁止 delegate/subagent；禁止输出 git diff / review diff；这就是最终交付物，直接输出全文。
"""

_FINAL_REPORT_TAG = "老马产业周期分析"


def find_hermes_bin(custom_bin: str = "") -> str | None:
    """解析 Hermes 可执行文件路径；custom_bin 优先，否则查 PATH。"""
    if (custom_bin or "").strip():
        path = Path(custom_bin.strip()).expanduser()
        if path.is_file():
            return str(path)
    return shutil.which("hermes")


def is_hermes_available(custom_bin: str = "") -> bool:
    """Hermes CLI 是否在 PATH 或指定路径可用。"""
    return find_hermes_bin(custom_bin) is not None


def _profile_skills_dir(profile: str) -> Path:
    return Path.home() / ".hermes" / "profiles" / profile / "skills"


def ensure_hermes_profile_skill(
    profile: str,
    skill: str,
    *,
    skill_source_dir: str = "",
) -> None:
    """确保指定 profile 能加载 skill（非 default profile 常缺 ~/.claude/skills）。"""
    if not (profile or "").strip() or not (skill or "").strip():
        return

    skills_root = _profile_skills_dir(profile.strip())
    target = skills_root / skill.strip()
    if target.exists():
        return

    source_root = (
        Path(skill_source_dir).expanduser() if skill_source_dir else _DEFAULT_CLAUDE_SKILLS
    )
    source = source_root / skill.strip()
    if not source.is_dir():
        logger.warning(
            "hermes_runner: skill 源目录不存在，跳过 symlink: %s", source
        )
        return

    skills_root.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=True)
    logger.info("hermes_runner: 已为 profile=%s symlink skill %s", profile, skill)


def parse_hermes_stdout(raw: str) -> tuple[str, str]:
    """从 hermes chat -Q 输出中提取最终回复与 session_id。"""
    session_id = ""
    match = _SESSION_ID_RE.search(raw or "")
    if match:
        session_id = match.group(1).strip()
    text = _SESSION_ID_RE.sub("", raw or "")
    return text.strip(), session_id


def is_diff_artifact(content: str) -> bool:
    """判断 Hermes 输出是否为 review diff / git diff 等工具产物。"""
    text = (content or "").strip()
    if not text:
        return False
    lower = text.lower()
    if "review diff" in lower:
        return True
    if lower.startswith("diff --git") or "diff a/" in lower or "diff b/" in lower:
        return True
    lines = text.splitlines()
    hunk_lines = sum(1 for ln in lines if ln.startswith("@@ "))
    plus_minus = sum(
        1 for ln in lines if ln.startswith("+") or ln.startswith("-")
    )
    if hunk_lines >= 1 and plus_minus >= 8:
        if not any(marker in text for marker in _FULL_REPORT_MARKERS):
            return True
    if plus_minus >= 12 and plus_minus / max(len(lines), 1) > 0.25:
        if not any(marker in text for marker in _FULL_REPORT_MARKERS):
            return True
    return False


def is_incomplete_lmd_report(content: str) -> bool:
    """判断 Hermes 输出是否仅为摘要而非完整五段式报告。"""
    text = (content or "").strip()
    if is_diff_artifact(text):
        return True
    if len(text) < 1200:
        return True
    if any(marker in text for marker in _SUMMARY_ONLY_MARKERS):
        if not any(marker in text for marker in _FULL_REPORT_MARKERS):
            return True
    section_hits = sum(1 for marker in _FULL_REPORT_MARKERS if marker in text)
    return section_hits < 3


def find_lmd_report_file(
    reports_dir: Path | str,
    symbol: str,
    *,
    analysis_date: date | None = None,
    min_mtime: float | None = None,
) -> Path | None:
    """在 reports/ 下查找老马视角成稿（优先最终报告，跳过 Research 底稿）。"""
    root = Path(reports_dir)
    if not root.is_dir():
        return None

    sym = (symbol or "").strip()
    if not sym:
        return None

    from src.core.report_paths import iter_report_md_files

    date_token = analysis_date.strftime("%Y%m%d") if analysis_date else ""
    candidates: list[Path] = []

    for path in iter_report_md_files(root):
        name = path.name
        if sym not in name:
            continue
        if any(part in name for part in _NON_FINAL_REPORT_NAME_PARTS):
            continue
        if date_token and not name.endswith(f"_{date_token}.md"):
            continue
        if _FINAL_REPORT_TAG in name or "老马" in name:
            candidates.append(path)

    if not candidates:
        for path in iter_report_md_files(root):
            name = path.name
            if sym not in name:
                continue
            if any(part in name for part in _NON_FINAL_REPORT_NAME_PARTS):
                continue
            if date_token and not name.endswith(f"_{date_token}.md"):
                continue
            candidates.append(path)

    if min_mtime is not None:
        candidates = [p for p in candidates if p.stat().st_mtime >= min_mtime]

    if not candidates:
        return None

    return max(candidates, key=lambda p: p.stat().st_mtime)


def resolve_lmd_report_content(
    content: str,
    *,
    symbol: str,
    reports_dir: Path | str,
    analysis_date: date | None = None,
    started_at: float | None = None,
) -> str:
    """Hermes 若返回 diff/摘要，尝试从磁盘读取已落盘的成稿。"""
    text = (content or "").strip()
    if not is_incomplete_lmd_report(text) and not is_diff_artifact(text):
        return text

    min_mtime = (started_at - 120.0) if started_at is not None else None
    report_path = find_lmd_report_file(
        reports_dir,
        symbol,
        analysis_date=analysis_date,
        min_mtime=min_mtime,
    )
    if not report_path or not report_path.is_file():
        return text

    try:
        disk_content = report_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("hermes_runner: 读取成稿失败 %s: %s", report_path, exc)
        return text

    if not disk_content or is_diff_artifact(disk_content):
        return text
    if is_incomplete_lmd_report(disk_content) and len(disk_content) <= len(text):
        return text

    logger.info(
        "hermes_runner: 使用磁盘成稿 %s 替代 Hermes 输出 (disk=%s, stdout=%s)",
        report_path.name,
        len(disk_content),
        len(text),
    )
    return disk_content


def _build_hermes_cmd(
    bin_path: str,
    *,
    profile: str,
    skill: str,
    query: str,
    max_turns: int,
    model: str,
    ignore_rules: bool,
    resume_session_id: str = "",
) -> list[str]:
    cmd: list[str] = [bin_path]
    if profile:
        cmd.extend(["-p", profile])
    cmd.append("chat")
    if ignore_rules:
        cmd.append("--ignore-rules")
    cmd.extend(["-Q", "--source", "tool", "--accept-hooks", "--yolo"])
    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])
    else:
        cmd.extend(["-s", skill])
    cmd.extend(["-q", query, "--max-turns", str(max(1, int(max_turns)))])
    if (model or "").strip():
        cmd.extend(["-m", model.strip()])
    return cmd


async def _run_hermes_cmd(
    cmd: list[str],
    *,
    profile: str,
    timeout_sec: float,
) -> tuple[str, str]:
    env = None
    if profile:
        env = {**os.environ, "HERMES_PROFILE": profile}

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=max(30.0, float(timeout_sec)),
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"Hermes 执行超时（>{timeout_sec:.0f}s）") from None

    stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")
    stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")

    if proc.returncode != 0:
        tail = (stderr or stdout).strip()[-800:]
        raise RuntimeError(
            f"Hermes 退出码 {proc.returncode}"
            + (f": {tail}" if tail else "")
        )

    content, session_id = parse_hermes_stdout(stdout)
    if not content:
        raise RuntimeError("Hermes 未返回有效内容")
    if stderr.strip():
        logger.debug("hermes stderr: %s", stderr.strip()[:500])
    return content, session_id


async def run_hermes_chat(
    *,
    query: str,
    skill: str = "lmd-finance-perspective",
    hermes_bin: str = "",
    hermes_profile: str = "",
    skill_source_dir: str = "",
    max_turns: int = 40,
    timeout_sec: float = 420,
    model: str = "",
    ignore_rules: bool = True,
    auto_expand_summary: bool = True,
    followup_timeout_sec: float = 300,
    report_fallback_dir: Path | str = "",
    report_fallback_symbol: str = "",
    report_fallback_date: date | None = None,
) -> str:
    """非交互调用 Hermes chat，预加载 skill，返回完整 Markdown 报告。

    Raises:
        RuntimeError: Hermes 不可用、超时或非零退出码。
    """
    bin_path = find_hermes_bin(hermes_bin)
    if not bin_path:
        raise RuntimeError(
            "未找到 Hermes CLI，请安装并确保 `hermes` 在 PATH 中，"
            "或在 Agent 配置中设置 hermes_bin"
        )

    profile = (hermes_profile or "").strip()
    if profile:
        ensure_hermes_profile_skill(
            profile, skill, skill_source_dir=skill_source_dir
        )

    cmd = _build_hermes_cmd(
        bin_path,
        profile=profile,
        skill=skill,
        query=query,
        max_turns=max_turns,
        model=model,
        ignore_rules=ignore_rules,
    )
    logger.info(
        "hermes_runner 启动: profile=%s skill=%s max_turns=%s ignore_rules=%s",
        profile or "(default)",
        skill,
        max_turns,
        ignore_rules,
    )

    started_at = time.time()
    content, session_id = await _run_hermes_cmd(
        cmd, profile=profile, timeout_sec=timeout_sec
    )

    if auto_expand_summary and session_id and is_incomplete_lmd_report(content):
        logger.info(
            "hermes_runner: 检测到摘要式输出(len=%s)，续写完整报告 session=%s",
            len(content),
            session_id,
        )
        follow_cmd = _build_hermes_cmd(
            bin_path,
            profile=profile,
            skill=skill,
            query=_FOLLOWUP_PROMPT,
            max_turns=max(15, max_turns // 2),
            model=model,
            ignore_rules=ignore_rules,
            resume_session_id=session_id,
        )
        expanded, _ = await _run_hermes_cmd(
            follow_cmd,
            profile=profile,
            timeout_sec=followup_timeout_sec,
        )
        if expanded and (
            not is_incomplete_lmd_report(expanded) or len(expanded) > len(content)
        ):
            content = expanded

    if (report_fallback_dir or "").__str__().strip() and (report_fallback_symbol or "").strip():
        content = resolve_lmd_report_content(
            content,
            symbol=report_fallback_symbol.strip(),
            reports_dir=report_fallback_dir,
            analysis_date=report_fallback_date,
            started_at=started_at,
        )

    return content


async def test_hermes_connection(
    *,
    hermes_bin: str = "",
    hermes_profile: str = "",
    skill: str = "",
    skill_source_dir: str = "",
    timeout_sec: float = 60,
) -> dict:
    """轻量探测 Hermes 是否可用。"""
    bin_path = find_hermes_bin(hermes_bin)
    if not bin_path:
        return {
            "ok": False,
            "message": "未找到 Hermes CLI",
            "bin": "",
        }

    probe_skill = (skill or "lmd-finance-perspective").strip()
    profile = (hermes_profile or "").strip()
    if profile:
        ensure_hermes_profile_skill(
            profile, probe_skill, skill_source_dir=skill_source_dir
        )

    cmd = _build_hermes_cmd(
        bin_path,
        profile=profile,
        skill=probe_skill,
        query="请仅回复：Hermes 连接正常",
        max_turns=3,
        model="",
        ignore_rules=True,
    )
    try:
        content, session_id = await _run_hermes_cmd(
            cmd, profile=profile, timeout_sec=max(15.0, float(timeout_sec))
        )
        return {
            "ok": True,
            "message": "Hermes 连接正常",
            "bin": bin_path,
            "profile": profile or "default",
            "skill": probe_skill,
            "reply_preview": (content or "")[:200],
            "session_id": session_id,
        }
    except Exception as e:
        return {
            "ok": False,
            "message": str(e),
            "bin": bin_path,
            "profile": profile or "default",
            "skill": probe_skill,
        }
