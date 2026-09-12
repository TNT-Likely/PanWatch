"""tests for src/core/json_safe.py"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum

from src.platform.persistence.json_safe import to_jsonable


class TestPrimitives:
    def test_none(self):
        """基础类型 — None 原样返回"""
        assert to_jsonable(None) is None

    def test_str(self):
        """基础类型 — 字符串原样返回"""
        assert to_jsonable("hello") == "hello"

    def test_int(self):
        """基础类型 — 整数原样返回"""
        assert to_jsonable(42) == 42

    def test_float(self):
        """基础类型 — 浮点数原样返回"""
        assert to_jsonable(3.14) == 3.14

    def test_bool(self):
        """基础类型 — 布尔值原样返回"""
        assert to_jsonable(True) is True


class TestDatetime:
    def test_datetime(self):
        """日期时间 — datetime 转 ISO 格式"""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert to_jsonable(dt) == "2024-01-15T10:30:00+00:00"

    def test_date(self):
        """日期时间 — date 转 ISO 格式"""
        d = date(2024, 1, 15)
        assert to_jsonable(d) == "2024-01-15"


class TestEnum:
    def test_enum_value(self):
        """枚举 — 字符串枚举取 value"""
        class Color(Enum):
            RED = "red"
            BLUE = "blue"

        assert to_jsonable(Color.RED) == "red"

    def test_enum_int_value(self):
        """枚举 — 整数枚举取 value"""
        class Priority(Enum):
            HIGH = 1
            LOW = 2

        assert to_jsonable(Priority.HIGH) == 1


class TestCollections:
    def test_dict(self):
        """集合 — dict 递归转换"""
        assert to_jsonable({"a": 1, "b": "x"}) == {"a": 1, "b": "x"}

    def test_list(self):
        """集合 — list 递归转换"""
        assert to_jsonable([1, "a", None]) == [1, "a", None]

    def test_set(self):
        """集合 — set 转为 list"""
        result = to_jsonable({1})
        assert isinstance(result, list)
        assert result == [1]

    def test_nested(self):
        """集合 — 嵌套结构递归转换"""
        data = {"items": [{"name": "test", "date": date(2024, 1, 1)}]}
        result = to_jsonable(data)
        assert result == {"items": [{"name": "test", "date": "2024-01-01"}]}


class TestDataclass:
    def test_dataclass(self):
        """dataclass — 转为 dict"""
        @dataclass
        class Point:
            x: int
            y: int

        assert to_jsonable(Point(1, 2)) == {"x": 1, "y": 2}


class TestCircularReference:
    def test_circular_dict(self):
        """循环引用 — 检测并返回 <circular>"""
        d: dict = {}
        d["self"] = d
        result = to_jsonable(d)
        assert result["self"] == "<circular>"
