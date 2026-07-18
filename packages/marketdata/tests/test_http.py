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


class _CapturingClient(_OkClient):
    captured_kwargs: dict = {}

    def __init__(self, **kw):
        _CapturingClient.captured_kwargs = kw


def test_market_get_returns_text(monkeypatch):
    monkeypatch.setattr(mh.httpx, "Client", _OkClient)
    assert mh.market_get("http://x", host_key="t4a", retries=0) == "hello"


def test_market_get_returns_none_on_failure(monkeypatch):
    monkeypatch.setattr(mh.httpx, "Client", _FailClient)
    monkeypatch.setattr(mh.time, "sleep", lambda *_: None)
    assert mh.market_get("http://x", host_key="t4b", retries=1) is None


def test_capture_errors_collects_failure_reason(monkeypatch):
    monkeypatch.setattr(mh.httpx, "Client", _FailClient)
    monkeypatch.setattr(mh.time, "sleep", lambda *_: None)
    with mh.capture_errors() as errs:
        mh.market_get("http://x", host_key="t4i", retries=0, log_label="测试源", symbol="600519")
    assert len(errs) == 1
    assert "测试源" in errs[0] and "600519" in errs[0] and "ConnectError" in errs[0]


def test_capture_errors_empty_on_success(monkeypatch):
    monkeypatch.setattr(mh.httpx, "Client", _OkClient)
    with mh.capture_errors() as errs:
        mh.market_get("http://x", host_key="t4j", retries=0)
    assert errs == []


def test_record_error_no_sink_is_noop():
    mh.record_error("孤立调用不应抛错")  # 无 capture_errors 上下文


def test_market_get_passes_proxy_when_set(monkeypatch):
    monkeypatch.setattr(mh.httpx, "Client", _CapturingClient)
    mh.market_get("http://x", host_key="t4d", retries=0, proxy="http://p:1")
    assert _CapturingClient.captured_kwargs.get("proxy") == "http://p:1"


def test_market_get_omits_proxy_when_not_set(monkeypatch):
    monkeypatch.setattr(mh.httpx, "Client", _CapturingClient)
    mh.market_get("http://x", host_key="t4e", retries=0)
    assert "proxy" not in _CapturingClient.captured_kwargs


def test_throttle_sleeps_on_second_call(monkeypatch):
    slept = []
    monkeypatch.setattr(mh.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(mh.time, "time", lambda: 100.0)
    mh.throttle("t4c", 0.15)   # 首次:last_call 默认 0,wait 为负,不睡
    mh.throttle("t4c", 0.15)   # 二次:同一时刻,wait=0.15,应 sleep
    assert slept and abs(slept[-1] - 0.15) < 1e-9
