import httpx

import marketdata.http as mh


class _FakeResp:
    def __init__(self, text="", content=b"", status=200):
        self.text, self.content, self._status = text, content, status

    def raise_for_status(self):
        if self._status >= 400:
            raise httpx.HTTPError("bad status")


class _OkClient:
    def __init__(self, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        return _FakeResp(text="hello")


class _FailClient(_OkClient):
    def get(self, url, params=None):
        raise httpx.ConnectError("boom")


def test_market_get_returns_text(monkeypatch):
    monkeypatch.setattr(mh.httpx, "Client", _OkClient)
    assert mh.market_get("http://x", host_key="t4a", retries=0) == "hello"


def test_market_get_returns_none_on_failure(monkeypatch):
    monkeypatch.setattr(mh.httpx, "Client", _FailClient)
    monkeypatch.setattr(mh.time, "sleep", lambda *_: None)
    assert mh.market_get("http://x", host_key="t4b", retries=1) is None


def test_throttle_sleeps_on_second_call(monkeypatch):
    slept = []
    monkeypatch.setattr(mh.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(mh.time, "time", lambda: 100.0)
    mh.throttle("t4c", 0.15)   # 首次:last_call 默认 0,wait 为负,不睡
    mh.throttle("t4c", 0.15)   # 二次:同一时刻,wait=0.15,应 sleep
    assert slept and abs(slept[-1] - 0.15) < 1e-9
