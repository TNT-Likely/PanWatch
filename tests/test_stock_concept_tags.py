"""概念标签采集与合并逻辑测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.collectors import concept_collector
from src.core.stock_concept_tags import merge_concept_tags, normalize_manual_tags


def test_normalize_manual_tags_dedupes_and_limits():
    """手动标签应去重并限制数量。"""
    tags = normalize_manual_tags([" 人工智能 ", "人工智能", "芯片", ""])
    assert tags == ["人工智能", "芯片"]


def test_merge_concept_tags_manual_first(monkeypatch):
    """合并标签时手动标签优先展示。"""
    stock = SimpleNamespace(
        concept_tags_auto=["人工智能", "芯片"],
        concept_tags_manual=["龙头", "人工智能"],
    )
    merged = merge_concept_tags(stock)
    assert merged == [
        {"name": "龙头", "source": "manual"},
        {"name": "人工智能", "source": "manual"},
        {"name": "芯片", "source": "auto"},
    ]


def test_fetch_cn_concept_tags_parses_slist(monkeypatch):
    """东财 slist 响应应解析为概念标签列表。"""
    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            if "stock/get" in url:
                return FakeResp({"data": {"f127": "白酒"}})
            return FakeResp(
                {
                    "data": {
                        "diff": [
                            {"f14": "白酒"},
                            {"f14": "人工智能"},
                            {"f14": "人工智能"},
                        ]
                    }
                }
            )

    monkeypatch.setattr(concept_collector.httpx, "Client", FakeClient)
    concept_collector._CONCEPT_CACHE.clear()

    tags = concept_collector.fetch_cn_concept_tags("600519")
    assert tags == ["人工智能"]
