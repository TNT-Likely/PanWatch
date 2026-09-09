"""tests for src/core/timezone.py"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.platform.scheduling.timezone import (
    format_beijing,
    to_beijing,
    to_iso_utc,
    to_iso_with_tz,
    to_utc,
)


class TestToUtc:
    def test_aware_datetime(self):
        """转 UTC — 带时区的 datetime 正确转换"""
        dt = datetime(2024, 1, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        result = to_utc(dt)
        assert result.tzinfo == timezone.utc
        assert result.hour == 2  # Shanghai is UTC+8

    def test_naive_datetime_treated_as_app_tz(self, monkeypatch):
        """转 UTC — 无时区 datetime 视为应用时区"""
        monkeypatch.setenv("TZ", "Asia/Shanghai")
        dt = datetime(2024, 1, 15, 10, 0)
        result = to_utc(dt)
        assert result.tzinfo == timezone.utc
        assert result.hour == 2


class TestToBeijing:
    def test_utc_to_beijing(self, monkeypatch):
        """转北京时间 — UTC 02:00 → 10:00"""
        monkeypatch.setenv("TZ", "Asia/Shanghai")
        dt = datetime(2024, 1, 15, 2, 0, tzinfo=timezone.utc)
        result = to_beijing(dt)
        assert result.hour == 10

    def test_naive_treated_as_utc(self, monkeypatch):
        """转北京时间 — 无时区 datetime 视为 UTC"""
        monkeypatch.setenv("TZ", "Asia/Shanghai")
        dt = datetime(2024, 1, 15, 2, 0)
        result = to_beijing(dt)
        assert result.hour == 10


class TestFormatBeijing:
    def test_default_format(self, monkeypatch):
        """格式化 — 默认格式 YYYY-MM-DD HH:MM:SS"""
        monkeypatch.setenv("TZ", "Asia/Shanghai")
        dt = datetime(2024, 1, 15, 2, 30, 0, tzinfo=timezone.utc)
        result = format_beijing(dt)
        assert result == "2024-01-15 10:30:00"

    def test_custom_format(self, monkeypatch):
        """格式化 — 自定义格式 HH:MM"""
        monkeypatch.setenv("TZ", "Asia/Shanghai")
        dt = datetime(2024, 1, 15, 2, 0, 0, tzinfo=timezone.utc)
        result = format_beijing(dt, fmt="%H:%M")
        assert result == "10:00"


class TestToIsoUtc:
    def test_utc_input(self):
        """ISO UTC — UTC 输入带 Z 后缀"""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert to_iso_utc(dt) == "2024-01-15T10:30:00Z"

    def test_non_utc_input(self):
        """ISO UTC — 非 UTC 输入自动转换"""
        dt = datetime(2024, 1, 15, 18, 30, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert to_iso_utc(dt) == "2024-01-15T10:30:00Z"


class TestToIsoWithTz:
    def test_aware(self):
        """ISO 带时区 — 保留原始时区偏移"""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = to_iso_with_tz(dt)
        assert "10:30:00" in result
        assert "+00:00" in result

    def test_naive_gets_utc(self):
        """ISO 带时区 — 无时区 datetime 默认 UTC"""
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = to_iso_with_tz(dt)
        assert "+00:00" in result
