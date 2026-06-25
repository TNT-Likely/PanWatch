"""本地 Hermes CLI 委托 — 供 lmd_outlook 等 Agent 调用 skill + 工具链。"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_SESSION_ID_RE = re.compile(r"^session_id:\s*(\S+)\s*$", re.MULTILINE)
_DEFAULT_CLAUDE_SKILLS = Path.home() / ".claude" / "skills"

# 完整报告应包含的章节（至少其二）
_FULL_REPORT_MARKERS = (
    "## 一、",
    "## 二、",
    "五维",
    "路径推演",
    "风险提示",
)

# 仅摘要、未交付正文的信号
_SUMMARY_ONLY_MARKERS = (
    "执行摘要",
    "报告完成",
    "研究阶段（Step 2）",
    "研究阶段(Step 2)",
    "结论（Step 3）",
    "结论(Step 3)",
)

_FOLLOWUP_PROMPT = """你上一轮的回复只是研究摘要/执行摘要，不是可入库的完整报告。

请**立即**在同一话题下输出完整的老马视角 Markdown 成稿（不要再写「报告完成」「执行摘要」「Step 2/Step 3」等过程标题）。

必须包含以下二级标题（缺一不可，可在此基础上扩展三级标题）：
## 一、整体定位
## 二、五维周期定位
## 三、路径推演
## 四、诚实边界
## 五、风险提示

要求：
- 单股正文不少于 1800 字；五维须逐项展开论证，引用已研究的具体数字。
- 用第一人称「我」、老马语气；开头声明非投资建议，结尾有风险提示。
- 禁止 delegate/subagent；这就是最终交付物，直接输出全文。
"""


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


def is_incomplete_lmd_report(content: str) -> bool:
    """判断 Hermes 输出是否仅为摘要而非完整五段式报告。"""
    text = (content or "").strip()
    if len(text) < 1200:
        return True
    if any(marker in text for marker in _SUMMARY_ONLY_MARKERS):
        if not any(marker in text for marker in _FULL_REPORT_MARKERS):
            return True
    section_hits = sum(1 for marker in _FULL_REPORT_MARKERS if marker in text)
    return section_hits < 2


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
