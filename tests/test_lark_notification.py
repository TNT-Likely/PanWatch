"""飞书(lark)通知渠道自定义发送器。

背景：apprise 的 NotifyLark 硬编码 open.larksuite.com(Lark 海外版)，国内飞书
(open.feishu.cn)永远收不到 → lark 移出 apprise，走 _send_lark 自定义实现。
"""

import base64
import hashlib
import hmac

import pytest

from src.platform.notifications.notifier import NotifierManager

_captured: dict = {}


class _FakeResp:
    status_code = 200

    def __init__(self, body: dict):
        self._body = body

    def json(self) -> dict:
        return self._body


class _FakeClient:
    body: dict = {}

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, timeout=None):
        _captured["url"] = url
        _captured["payload"] = json
        return _FakeResp(_FakeClient.body)


@pytest.fixture(autouse=True)
def _fake_http(monkeypatch):
    _captured.clear()
    _FakeClient.body = {"code": 0, "msg": "success"}
    monkeypatch.setattr(
        "src.platform.notifications.notifier.httpx.AsyncClient", _FakeClient
    )


def test_lark_posts_to_feishu_cn_with_text_payload():
    import asyncio

    mgr = NotifierManager()
    asyncio.run(
        mgr._send_lark(
            {"webhook_token": "abcd1234-5678-90ab-cdef"},
            "测试通知",
            "内容",
        )
    )
    assert _captured["url"] == "https://open.feishu.cn/open-apis/bot/v2/hook/abcd1234-5678-90ab-cdef"
    assert _captured["payload"]["msg_type"] == "text"
    assert "测试通知" in _captured["payload"]["content"]["text"]
    assert "timestamp" not in _captured["payload"]  # 无签名密钥时不带签名字段


def test_lark_signature_when_secret_present():
    import asyncio

    mgr = NotifierManager()
    secret = "my-sign-secret"
    asyncio.run(
        mgr._send_lark(
            {"webhook_token": "tok", "secret": secret},
            "测试通知",
            "内容",
        )
    )
    payload = _captured["payload"]
    assert payload["timestamp"]
    # 飞书签名算法：key="{timestamp}\n{secret}"，对空串 HMAC-SHA256 后 base64
    expected = base64.b64encode(
        hmac.new(
            f"{payload['timestamp']}\n{secret}".encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    assert payload["sign"] == expected


def test_lark_nonzero_code_raises_with_guidance():
    import asyncio

    _FakeClient.body = {"code": 19021, "msg": "sign match fail"}
    mgr = NotifierManager()
    with pytest.raises(RuntimeError, match="19021"):
        asyncio.run(mgr._send_lark({"webhook_token": "tok"}, "t", "c"))


def test_lark_missing_token_raises():
    import asyncio

    mgr = NotifierManager()
    with pytest.raises(ValueError, match="webhook_token"):
        asyncio.run(mgr._send_lark({}, "t", "c"))
