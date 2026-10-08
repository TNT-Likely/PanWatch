#!/usr/bin/env python3
"""Evaluate the real HTTP/task/runtime/approval/DB path in a managed QA run.

No imports of application modules or default database. Run after configuring a
QA model through normal settings APIs. Fixed responses must use --mode replay;
missing or skipped model coverage returns INCOMPLETE (exit 2), never green.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx


class RuntimeEvaluation:
    def __init__(self, run: Path, mode: str):
        self.run = run.resolve(strict=True)
        self.manifest = json.loads((self.run / "manifest.json").read_text())
        runtime = json.loads((self.run / "private/runtime.json").read_text())
        assert runtime["run_id"] == self.manifest["run_id"], "QA runtime identity differs"
        self.origin = self.manifest["origin"]
        assert urlparse(self.origin).hostname == "127.0.0.1", "Only a QA loopback origin is permitted"
        self.db = self.run / "source/data/panwatch.db"
        assert self.db.resolve() == Path(runtime["database"]).resolve(), "QA database identity differs"
        assert self.db.resolve().is_relative_to(self.run), "QA DB escapes run directory"
        self.client = httpx.Client(base_url=self.origin, timeout=240, trust_env=False)
        credentials = json.loads((self.run / "private/credentials.json").read_text())
        response = self.client.post("/api/auth/login", json={"username": credentials["username"], "password": credentials["password"]})
        response.raise_for_status()
        self.client.headers["Authorization"] = "Bearer " + response.json()["data"]["token"]
        self.mode = mode
        self.results = []
        self.task_ids = []

    def sql(self, query, args=()):
        with sqlite3.connect(f"file:{self.db}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(query, args)]

    def api(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        assert response.status_code < 400, f"{method} {path}: HTTP {response.status_code}"
        envelope = response.json()
        assert envelope.get("success") is True, f"{method} {path}: API envelope failed"
        return envelope["data"]

    def task(self, prompt):
        conversation = self.api("POST", "/api/assistant/conversations", json={})
        task = self.api("POST", f"/api/assistant/conversations/{conversation['id']}/tasks", json={"content": prompt})
        self.task_ids.append(task["task_id"])
        return task["task_id"]

    def wait(self, task_id):
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            task = self.api("GET", f"/api/assistant/tasks/{task_id}")
            if task["status"] not in {"queued", "running"}:
                return task
            time.sleep(.35)
        raise AssertionError("Task exceeded evaluation deadline")

    def decide(self, approval, decision):
        with self.client.stream("POST", f"/api/assistant/approvals/{approval['id']}/decision/stream", json={"decision": decision}) as response:
            assert response.status_code == 200, f"Approval HTTP {response.status_code}"
            for _ in response.iter_lines():
                pass

    def require_completed(self, task):
        assert task["status"] == "completed", f"Task status {task['status']}, error {task.get('error_code')}"
        assert task.get("model") or task.get("usage", {}).get("model"), "No model identity in actual task receipt"

    def invocations(self, task_id):
        return self.sql("select call_id,tool_name,status,arguments,result_data from assistant_tool_invocations where task_run_id=? order by id", (task_id,))

    def quote_disconnect(self):
        task_id = self.task("查询 CN:600519 当前报价，明确来源时间、市场状态及是否实时，不要把获取时间当成数据时间。")
        cursor = 0
        with self.client.stream("GET", f"/api/assistant/tasks/{task_id}/events") as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("id:"):
                    cursor = int(line.split(":", 1)[1])
                    break  # A real SSE client disconnect, not a fake network outage.
        task = self.wait(task_id)
        self.require_completed(task)
        calls = self.invocations(task_id)
        assert any(c["tool_name"] == "get_stock_quote" and c["status"] == "completed" for c in calls), "Actual quote tool did not complete"
        evidence = task["result"]["evidence"]
        quote = next(e for e in evidence if e["tool_name"] == "get_stock_quote")
        assert quote["freshness_basis"] != "observed_at", "Fetch time is incorrectly used as source time"
        ids = []
        with self.client.stream("GET", f"/api/assistant/tasks/{task_id}/events", params={"last_event_id": cursor}) as response:
            for line in response.iter_lines():
                if line.startswith("id:"):
                    ids.append(int(line.split(":", 1)[1]))
        assert ids and ids == sorted(set(ids)) and min(ids) > cursor, "Replay duplicates or loses sequence ordering"
        return {"task_id": task_id, "model": task["model"], "quote_evidence": quote, "disconnect_cursor": cursor, "replayed_event_ids": ids, "invocations": calls}

    def discovery_watchlist(self):
        task_id = self.task("通过可用工具读取真实自选库，列出自选股票代码和市场，不要用持仓列表代替自选；缺少工具请先搜索加载。")
        task = self.wait(task_id)
        self.require_completed(task)
        calls = self.invocations(task_id)
        assert any(c["tool_name"] == "tool_search" and c["status"] == "completed" for c in calls), "Deferred discovery was not exercised"
        reads = [c for c in calls if c["tool_name"] == "get_watchlist" and c["status"] == "completed"]
        assert reads, "Actual watchlist read did not complete"
        stocks = self.sql("select symbol,market from stocks")
        collected = set()
        results = []
        for call in reads:
            result = json.loads(call["result_data"])
            arguments = json.loads(call["arguments"])
            scope = [s for s in stocks if not arguments.get("market") or s["market"] == arguments["market"]]
            assert result["total"] == len(scope)
            identities = {(s["symbol"],s["market"]) for s in result["items"]}
            assert identities <= {(s["symbol"],s["market"]) for s in scope}
            collected.update(identities)
            results.append(result)
        assert collected == {(s["symbol"],s["market"]) for s in stocks}, "The complete requested watchlist was not returned"
        return {"task_id":task_id,"model":task["model"],"watchlist_reads":results,"invocations":calls}

    def combo_approval(self):
        channels = self.sql("select id,name,type from notify_channels where enabled=1 and type='feishu'")
        assert channels, "Create an enabled synthetic QA Feishu channel before evaluating"
        label = "Runtime QA " + uuid4().hex[:8]
        before = self.sql("select id from price_alert_rules")
        task_id = self.task(f"为 CN:600519 创建提醒，名字为 {label}。价格大于999999且量比大于2，两周有效，发飞书，冷却0分钟，每日最多2次，仅交易时段，重复提醒。先检查能力和通知渠道，再提交审批。")
        paused = self.wait(task_id)
        assert paused["status"] == "awaiting_approval", f"Expected approval, received {paused['status']}"
        assert self.sql("select id from price_alert_rules") == before, "A rule was written before approval"
        approval = next(a for a in paused["pending_approvals"] if a["tool_name"] == "create_price_alert")
        self.decide(approval, "approved")
        completed = self.wait(task_id)
        self.require_completed(completed)
        added = self.sql("select * from price_alert_rules where name=?", (label,))
        assert len(added) == 1, "Expected exactly one new rule"
        row = added[0]
        expected_group = {"op": "and", "items": [{"type": "price", "op": ">", "value": 999999}, {"type": "volume_ratio", "op": ">", "value": 2}]}
        group = json.loads(row["condition_group"])
        assert group["op"] == "and" and sorted(group["items"], key=lambda i:i['type']) == expected_group["items"], "Full combination differs"
        assert row["cooldown_minutes"] == 0 and row["max_triggers_per_day"] == 2
        assert row["market_hours_mode"] == "trading_only" and row["repeat_mode"] == "repeat"
        assert set(json.loads(row["notify_channel_ids"])) <= {c["id"] for c in channels} and json.loads(row["notify_channel_ids"])
        expiry = datetime.fromisoformat(row["expire_at"]).replace(tzinfo=UTC)
        assert 13.9 < (expiry - datetime.now(UTC)).total_seconds()/86400 < 14.1
        duplicate = self.client.post(f"/api/assistant/approvals/{approval['id']}/decision/stream", json={"decision":"approved"})
        assert duplicate.status_code == 409
        assert len(self.sql("select * from price_alert_rules where name=?", (label,))) == 1
        calls = self.invocations(task_id)
        readback = json.loads(next(c["result_data"] for c in calls if c["tool_name"] == "create_price_alert"))
        assert readback["condition_group"] == group and readback["notify_channel_ids"] == json.loads(row["notify_channel_ids"])
        return {"task_id":task_id,"model":completed["model"],"approval":approval,"rule":row,"duplicate_http":duplicate.status_code,"invocations":calls}

    def rejection(self):
        before = self.sql("select id from price_alert_rules")
        task_id = self.task("为 CN:600519 创建价格高于999998的盘中提醒，发飞书，冷却0分钟，每日最多2次，重复提醒，两周有效。999998是验收阈值，刻意避免触发，无需纠正。读取已配置渠道后直接调用创建工具提交系统审批，我会在审批面板确认，无需另行聊天确认。")
        paused = self.wait(task_id)
        assert paused["status"] == "awaiting_approval", f"Expected approval, received {paused['status']}"
        self.decide(paused["pending_approvals"][0], "rejected")
        task = self.wait(task_id)
        self.require_completed(task)
        assert self.sql("select id from price_alert_rules") == before, "Rejected proposal changed rules"
        assert not any(c["tool_name"] == "create_price_alert" and c["status"] == "completed" for c in self.invocations(task_id))
        return {"task_id":task_id,"model":task["model"],"rules_unchanged":True}

    def unsupported(self):
        before = self.sql("select id from price_alert_rules")
        task_id = self.task("帮我设置 CN:600519 收盘站上20日均线且连续3天满足时提醒，不接受替换成盘中价格提醒。先检查哪些条件不支持。")
        task = self.wait(task_id)
        self.require_completed(task)
        assert not task["pending_approvals"] and self.sql("select id from price_alert_rules") == before
        checks = [c for c in self.invocations(task_id) if c["tool_name"] == "check_watch_request" and c["status"] == "completed"]
        assert checks and set(json.loads(checks[0]["result_data"])["unsupported_conditions"]) >= {"bar_close_confirmation", "moving_average_trigger", "consecutive_sessions"}
        return {"task_id":task_id,"model":task["model"],"rules_unchanged":True,"check":checks[0]}

    def evaluate(self, cases):
        providers = self.sql("select base_url from ai_services")
        model_ready = bool(self.sql("select id from ai_models"))
        if self.mode == "live-model":
            assert all(urlparse(p["base_url"]).hostname not in {"localhost", "127.0.0.1", "::1"} for p in providers), "A loopback replay provider cannot count as live-model coverage"
        if not model_ready:
            return {"overall":"INCOMPLETE","mode":self.mode,"reason":"QA model is not configured","cases":[]}
        for name in cases:
            try:
                evidence = getattr(self, name)()
                self.results.append({"id":name,"status":"PASS","layer":"live-model" if self.mode == "live-model" else "runtime-replay","evidence":evidence})
            except Exception as exc:
                # Never echo credentials, provider payloads or unredacted logs.
                self.results.append({"id":name,"status":"FAIL","reason":str(exc)[:500],"exception_type":type(exc).__name__})
            print(f"{name}: {self.results[-1]['status']}", flush=True)
        overall = "FAIL" if any(c["status"] == "FAIL" for c in self.results) else "PASS" if self.mode == "live-model" and set(cases) == {"quote_disconnect","discovery_watchlist","combo_approval","rejection","unsupported"} else "INCOMPLETE"
        return {"overall":overall,"mode":self.mode,"run_id":self.manifest["run_id"],"source_sha":self.manifest["base_sha"],"source_hash":self.manifest["source_hash"],"frontend_hash":self.manifest.get("frontend_hash"),"evaluated_at":datetime.now(UTC).isoformat(),"production_path":"HTTP → task worker → AssistantService.build_runtime → registry/policy → approval → DB","cases":self.results,"task_ids":self.task_ids,"boundaries":["C01 stale replay is a separate contract test", "Notification delivery is not exercised", "This suite does not cover every model or arbitrary natural-language request", "Client SSE disconnection does not simulate a server network outage"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--mode", choices=["live-model","replay"], default="live-model")
    parser.add_argument("--case", action="append", choices=["quote_disconnect","discovery_watchlist","combo_approval","rejection","unsupported"])
    args = parser.parse_args()
    evaluator = RuntimeEvaluation(args.run, args.mode)
    try:
        report = evaluator.evaluate(args.case or ["quote_disconnect","discovery_watchlist","combo_approval","rejection","unsupported"])
    finally:
        evaluator.client.close()
    output = evaluator.run / "evidence" / f"runtime-eval-{uuid4().hex[:8]}.json"
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(f"{report['overall']}: {output}")
    return 0 if report["overall"] == "PASS" else 1 if report["overall"] == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
