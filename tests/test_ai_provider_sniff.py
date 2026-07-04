"""AI 服务商模型嗅探 + 测试 temperature 降级 的单元测试。"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.ai_client import AIClient
from src.web import models as _models  # noqa: F401  注册 ORM
from src.web.database import Base
from src.web.models import AIService, AIModel


@pytest.fixture
def db():
    """独立内存 SQLite 会话,建全表。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _seed_service_with_model(db) -> AIModel:
    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)
    m = AIModel(name="m", service_id=svc.id, model="m", is_default=False)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _make_client() -> AIClient:
    return AIClient(base_url="http://example.test", api_key="k", model="m")


def test_list_models_returns_sorted_ids():
    """list_models 调用 models.list() 并返回排序后的模型 id 列表。"""
    client = _make_client()

    class _FakeModels:
        async def list(self):
            data = [type("M", (), {"id": "gpt-4o"})(), type("M", (), {"id": "aaa"})()]
            return type("R", (), {"data": data})()

    client.client.models = _FakeModels()
    assert asyncio.run(client.list_models()) == ["aaa", "gpt-4o"]


def test_chat_omits_temperature_when_none():
    """temperature=None 时不向 create 下发该字段。"""
    client = _make_client()
    seen: dict = {}

    async def _fake_create(**kwargs):
        seen.update(kwargs)
        msg = type("Msg", (), {"content": "ok"})()
        choice = type("C", (), {"message": msg})()
        return type("Resp", (), {"usage": None, "choices": [choice]})()

    client.client.chat.completions.create = _fake_create
    asyncio.run(client.chat("s", "u", temperature=None))
    assert "temperature" not in seen


def test_chat_sends_temperature_when_float():
    """temperature 为数值时正常下发。"""
    client = _make_client()
    seen: dict = {}

    async def _fake_create(**kwargs):
        seen.update(kwargs)
        msg = type("Msg", (), {"content": "ok"})()
        choice = type("C", (), {"message": msg})()
        return type("Resp", (), {"usage": None, "choices": [choice]})()

    client.client.chat.completions.create = _fake_create
    asyncio.run(client.chat("s", "u", temperature=0))
    assert seen["temperature"] == 0


def test_discover_models_returns_list(db, monkeypatch):
    """discover-models 用服务商凭证嗅探并返回模型 id 列表。"""
    from src.web.api import providers

    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def list_models(self):
            return ["gpt-4o", "o1"]

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    res = asyncio.run(providers.discover_models(svc.id, db))
    assert res["models"] == ["gpt-4o", "o1"]


def test_discover_models_error_maps_to_400(db, monkeypatch):
    """嗅探失败(服务商不支持/网络错误)返回 400。"""
    from fastapi import HTTPException
    from src.web.api import providers

    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def list_models(self):
            raise RuntimeError("404 not found")

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    try:
        asyncio.run(providers.discover_models(svc.id, db))
        assert False, "应抛 HTTPException"
    except HTTPException as e:
        assert e.status_code == 400


def test_batch_add_skips_duplicates_and_sets_default(db, monkeypatch):
    """批量新增:跳过已存在的 model 标识,设默认时清零其余。"""
    from src.web.api import providers

    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)
    db.add(AIModel(name="exists", service_id=svc.id, model="dup", is_default=True))
    db.commit()

    body = providers.BatchModelCreate(models=[
        providers.BatchModelItem(name="", model="dup", is_default=False),   # 重复,跳过
        providers.BatchModelItem(name="新A", model="new-a", is_default=True),
        providers.BatchModelItem(name="", model="new-b", is_default=False),
    ])
    res = providers.batch_add_models(svc.id, body, db)  # 同步端点(threadpool),不阻塞事件循环
    assert res["added"] == 2

    all_models = db.query(AIModel).filter(AIModel.service_id == svc.id).all()
    names = {m.model for m in all_models}
    assert names == {"dup", "new-a", "new-b"}
    # new-a 设为默认后,其余(含原 dup)应被清零
    defaults = [m.model for m in all_models if m.is_default]
    assert defaults == ["new-a"]
    # 显示名为空的回退为 model 标识
    assert next(m for m in all_models if m.model == "new-b").name == "new-b"


def test_test_model_omits_temperature(db, monkeypatch):
    """测试连通性时不下发 temperature(对不支持该参数的模型也安全)。"""
    from src.web.api import providers

    m = _seed_service_with_model(db)
    seen: dict = {}

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def chat(self, system_prompt, user_content, temperature=0.4):
            seen["temperature"] = temperature
            return "OK"

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    res = asyncio.run(providers.test_model(m.id, db))
    assert res["ok"] is True
    assert seen["temperature"] is None  # 未带 temperature


def test_test_model_error_maps_to_400(db, monkeypatch):
    """测试调用报错时映射为 400。"""
    from fastapi import HTTPException
    from src.web.api import providers

    m = _seed_service_with_model(db)

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def chat(self, system_prompt, user_content, temperature=0.4):
            raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    try:
        asyncio.run(providers.test_model(m.id, db))
        assert False, "应抛 HTTPException"
    except HTTPException as e:
        assert e.status_code == 400


def test_discover_models_empty_list(db, monkeypatch):
    """嗅探返回空列表时,接口正常返回空 models。"""
    from src.web.api import providers

    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def list_models(self):
            return []

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    res = asyncio.run(providers.discover_models(svc.id, db))
    assert res["models"] == []
