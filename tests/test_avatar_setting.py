"""设置页头像:图片落 data/avatars 文件,DB 仅存文件名;data URL 读写,清空删文件。"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.platform.persistence.models as M  # noqa: F401 (确保模型注册到 Base)
from src.web.api import settings as settings_api
from src.platform.persistence.database import Base, get_db

# 任意有效 base64;后端按字节落文件,GET 再读回同样的 data URL
_IMG = "data:image/jpeg;base64,AAAA"


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(settings_api.router, prefix="/settings")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_avatar_default_empty(tmp_path, monkeypatch):
    """未设置时头像为空字符串。"""
    c = _client(tmp_path, monkeypatch)
    assert c.get("/settings/avatar").json()["value"] == ""


def test_avatar_saved_as_file_db_stores_filename(tmp_path, monkeypatch):
    """上传后:图片落 data/avatars 文件,DB 仅记文件名,GET 以 data URL 读回。"""
    c = _client(tmp_path, monkeypatch)
    r = c.put("/settings/avatar", json={"value": _IMG})
    assert r.status_code == 200, r.text
    # 文件已落盘到 data/avatars
    assert os.listdir(os.path.join(str(tmp_path), "avatars")) == ["avatar.jpg"]
    # DB 仅存文件名(短,非 base64)
    assert r.json()["value"] == "avatar.jpg"
    # GET 读回 data URL
    assert c.get("/settings/avatar").json()["value"] == _IMG


def test_avatar_clear_deletes_file(tmp_path, monkeypatch):
    """传空字符串清空:删除文件 + GET 返回空。"""
    c = _client(tmp_path, monkeypatch)
    c.put("/settings/avatar", json={"value": _IMG})
    c.put("/settings/avatar", json={"value": ""})
    assert c.get("/settings/avatar").json()["value"] == ""
    assert os.listdir(os.path.join(str(tmp_path), "avatars")) == []


def test_avatar_rejects_non_dataurl(tmp_path, monkeypatch):
    """非 data URL 的头像值应被拒绝(400)。"""
    c = _client(tmp_path, monkeypatch)
    assert c.put("/settings/avatar", json={"value": "http://x/a.png"}).status_code == 400


def test_avatar_key_not_in_generic_list(tmp_path, monkeypatch):
    """头像键不混进通用设置列表。"""
    c = _client(tmp_path, monkeypatch)
    c.put("/settings/avatar", json={"value": _IMG})
    keys = [s["key"] for s in c.get("/settings").json()]
    assert "ui_avatar" not in keys
