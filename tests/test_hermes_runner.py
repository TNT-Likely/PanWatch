"""hermes_runner 单元测试。"""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from src.core.hermes_runner import (
    ensure_hermes_profile_skill,
    find_hermes_bin,
    find_lmd_report_file,
    is_diff_artifact,
    is_hermes_available,
    is_incomplete_lmd_report,
    parse_hermes_stdout,
    resolve_lmd_report_content,
    run_hermes_chat,
)


def test_parse_hermes_stdout_strips_session_id():
    """解析 Hermes -Q 输出时应去掉 session_id 行并保留 session_id。"""
    raw = "\n\nsession_id: 20260622_abc123\n\n# 报告标题\n正文"
    content, session_id = parse_hermes_stdout(raw)
    assert content == "# 报告标题\n正文"
    assert session_id == "20260622_abc123"


def test_is_incomplete_lmd_report_detects_summary():
    """执行摘要式短回复应判定为不完整。"""
    summary = "报告完成。以下是执行摘要：\n\n研究阶段（Step 2）：\n✅ 产业链"
    assert is_incomplete_lmd_report(summary) is True


def test_is_incomplete_lmd_report_accepts_full_sections():
    """含五段式章节的 long 文应判定为完整。"""
    full = (
        "## 一、整体定位\n" + "正文" * 400 + "\n"
        "## 二、五维周期定位\n" + "分析" * 400 + "\n"
        "## 三、路径推演\n路径\n## 四、诚实边界\n边界\n## 五、风险提示\n风险"
    )
    assert is_incomplete_lmd_report(full) is False


def test_is_diff_artifact_detects_review_diff():
    """review diff 输出应判定为 diff 产物。"""
    diff = "review diff a/reports/000960_Research_20260626.md →\n@@ -1,3 +1,5 @@\n-old\n+new"
    assert is_diff_artifact(diff) is True
    assert is_incomplete_lmd_report(diff) is True


def test_resolve_lmd_report_content_reads_disk(tmp_path):
    """stdout 为 diff 时应回退读取 reports/ 成稿。"""
    report = tmp_path / "锡业股份_000960_老马产业周期分析_20260626.md"
    report.write_text(
        "## 一、整体定位\n" + "正文" * 500 + "\n## 二、五维周期定位\n分析\n",
        encoding="utf-8",
    )
    diff = "review diff a/reports/000960_Research_20260626.md →\n@@ -1 +1 @@\n+x"
    out = resolve_lmd_report_content(
        diff,
        symbol="000960",
        reports_dir=tmp_path,
        analysis_date=date(2026, 6, 26),
    )
    assert "## 一、整体定位" in out
    assert "review diff" not in out


def test_find_lmd_report_file_skips_research_draft(tmp_path):
    """应优先最终成稿，跳过 Research 底稿。"""
    (tmp_path / "000960_Research_20260626.md").write_text("research", encoding="utf-8")
    final = tmp_path / "锡业股份_000960_老马产业周期分析_20260626.md"
    final.write_text("final", encoding="utf-8")
    found = find_lmd_report_file(
        tmp_path,
        "000960",
        analysis_date=date(2026, 6, 26),
    )
    assert found == final


def test_find_hermes_bin_custom_path(tmp_path):
    """自定义 hermes_bin 应优先于 PATH。"""
    fake = tmp_path / "hermes"
    fake.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    fake.chmod(0o755)
    assert find_hermes_bin(str(fake)) == str(fake)


def test_run_hermes_chat_success():
    """Hermes 正常退出时应返回解析后的正文。"""

    async def _run():
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (
            b"\nsession_id: x\n\n" + "报告内容".encode("utf-8"),
            b"",
        )
        mock_proc.returncode = 0
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()
        captured: dict = {}

        async def fake_exec(*args, **kwargs):
            captured["cmd"] = list(args)
            captured["env"] = kwargs.get("env")
            return mock_proc

        with patch(
            "src.core.hermes_runner.find_hermes_bin", return_value="/usr/bin/hermes"
        ), patch(
            "src.core.hermes_runner.asyncio.create_subprocess_exec",
            side_effect=fake_exec,
        ), patch("src.core.hermes_runner.ensure_hermes_profile_skill"):
            out = await run_hermes_chat(
                query="测试",
                timeout_sec=60,
                hermes_profile="agent-1-qingbaoxianfeng",
            )
        assert out == "报告内容"
        assert "--ignore-rules" in captured["cmd"]
        assert captured["cmd"][:4] == [
            "/usr/bin/hermes",
            "-p",
            "agent-1-qingbaoxianfeng",
            "chat",
        ]

    asyncio.run(_run())


def test_run_hermes_chat_auto_expand_summary():
    """摘要式输出应触发 session 续写完整报告。"""

    async def _run():
        summary = "报告完成。执行摘要：研究阶段 Step 2 完成"
        full = (
            "## 一、整体定位\n" + "x" * 500 + "\n## 二、五维周期定位\n"
            + "y" * 500 + "\n## 三、路径推演\n## 四、诚实边界\n## 五、风险提示\n"
        )
        responses = [
            (f"\nsession_id: sid1\n\n{summary}".encode(), b""),
            (f"\nsession_id: sid1\n\n{full}".encode(), b""),
        ]

        async def fake_exec(*args, **kwargs):
            mock_proc = AsyncMock()
            body, err = responses.pop(0)
            mock_proc.communicate.return_value = (body, err)
            mock_proc.returncode = 0
            return mock_proc

        with patch(
            "src.core.hermes_runner.find_hermes_bin", return_value="/usr/bin/hermes"
        ), patch(
            "src.core.hermes_runner.asyncio.create_subprocess_exec",
            side_effect=fake_exec,
        ), patch("src.core.hermes_runner.ensure_hermes_profile_skill"):
            out = await run_hermes_chat(query="测试", timeout_sec=60)
        assert "## 一、整体定位" in out
        assert "执行摘要" not in out or len(out) > len(summary)

    asyncio.run(_run())


def test_run_hermes_chat_missing_binary():
    """Hermes 不可用时应抛出 RuntimeError。"""

    async def _run():
        with patch("src.core.hermes_runner.find_hermes_bin", return_value=None):
            await run_hermes_chat(query="测试")

    with pytest.raises(RuntimeError, match="未找到 Hermes"):
        asyncio.run(_run())


def test_ensure_hermes_profile_skill_symlink(tmp_path, monkeypatch):
    """profile 缺 skill 时应从 ~/.claude/skills symlink。"""
    profile = "test-profile"
    skill = "lmd-finance-perspective"
    source_root = tmp_path / "claude-skills"
    source = source_root / skill
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# test", encoding="utf-8")

    profile_skills = tmp_path / "hermes" / "profiles" / profile / "skills"
    monkeypatch.setattr(
        "src.core.hermes_runner._profile_skills_dir",
        lambda p: profile_skills,
    )

    ensure_hermes_profile_skill(profile, skill, skill_source_dir=str(source_root))
    link = profile_skills / skill
    assert link.is_symlink()
    assert link.resolve() == source.resolve()


def test_is_hermes_available_with_which():
    """PATH 中有 hermes 时 is_hermes_available 为 True。"""
    with patch("src.core.hermes_runner.shutil.which", return_value="/x/hermes"):
        assert is_hermes_available() is True
