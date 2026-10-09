#!/usr/bin/env python3
"""Evaluate the real HTTP/task/runtime/approval/DB path in a managed QA run.

No imports of application modules or default database. Run after configuring a
QA model through normal settings APIs. Fixed responses must use --mode replay;
missing or skipped model coverage returns INCOMPLETE (exit 2), never green.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

REQUIRED_CASES = ("quote_disconnect", "discovery_watchlist", "combo_approval", "rejection", "unsupported", "monitoring_health", "or_combo", "research_history", "event_details", "portfolio_diagnosis")


def case_outcome(name):
    if name == "unsupported":
        return {"kind": "guardrail", "user_goal": "not_fulfilled", "reason": "Unsupported conditions were rejected; no requested alert exists"}
    if name == "rejection":
        return {"kind": "guardrail", "user_goal": "cancelled_by_user", "reason": "Approval was rejected; no alert was created"}
    return {"kind": "functional", "user_goal": "fulfilled_for_case"}


def report_status(cases, results, mode):
    if any(c["status"] == "FAIL" for c in results):
        return "FAIL"
    if mode != "live-model" or set(cases) != set(REQUIRED_CASES) or {c["id"] for c in results if c["status"] == "PASS"} != set(REQUIRED_CASES):
        return "INCOMPLETE"
    return "PASS"


def unsupported_closing_claim(answer):
    return bool(re.search(r"(?:可视为|相当于|就是)[^。；\n]{0,15}(?:收盘价|最后成交价)|该报价是[^。；\n]{0,25}(?:最后[^。；\n]*(?:盘面价格|成交)|收盘价)", answer))


def utc_instant(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


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

    def rules_named(self, label):
        return self.sql("select r.*,s.symbol,s.market from price_alert_rules r join stocks s on s.id=r.stock_id where r.name=?", (label,))

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
        quote_data = json.loads(next(c["result_data"] for c in calls if c["tool_name"] == "get_stock_quote" and c["status"] == "completed"))
        assert quote_data.get("current_price", 0) > 0, "The positive quote scenario has no price"
        assert quote_data.get("source_timestamp") or quote_data.get("quote_date"), "The positive quote scenario has no actual source time"
        assert quote_data["is_realtime"] == (quote_data["freshness"] == "fresh")
        assert quote_data["quote_semantics"] == "provider_snapshot" and quote_data["bar_close_confirmed"] is False
        conversation = self.api("GET", f"/api/assistant/conversations/{task['conversation_id']}")
        answer = next(m["content"] for m in reversed(conversation["messages"]) if m["role"] == "assistant")
        assert not unsupported_closing_claim(answer), "The answer infers a closing price from an unverified quote snapshot"
        assert quote["freshness"] == quote_data["freshness"] and quote["market_status"] == quote_data["market_status"]
        assert all(e["evidence_kind"] == "local_snapshot" for e in evidence if e["tool_name"] == "get_market_status"), "Market calendar is incorrectly treated as external source data"
        if not any(e["evidence_kind"] == "source_data" and e["freshness"] in {"unknown", "stale"} for e in evidence):
            assert "部分依据缺少可验证的数据时点或已经过期，请结合最新数据复核。" not in task["result"]["risks"], "Unsupported source-time warning remains"
        ids = []
        with self.client.stream("GET", f"/api/assistant/tasks/{task_id}/events", params={"last_event_id": cursor}) as response:
            for line in response.iter_lines():
                if line.startswith("id:"):
                    ids.append(int(line.split(":", 1)[1]))
        assert ids and ids == sorted(set(ids)) and min(ids) > cursor, "Replay duplicates or loses sequence ordering"
        return {"task_id": task_id, "model": task["model"], "quote_evidence": quote, "answer": answer, "disconnect_cursor": cursor, "replayed_event_ids": ids, "invocations": calls}

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
        added = self.rules_named(label)
        assert len(added) == 1, "Expected exactly one new rule"
        row = added[0]
        assert row["symbol"] == "600519" and row["market"] == "CN"
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
        retried = self.api("POST", f"/api/assistant/conversations/{task['conversation_id']}/tasks", json={"content":"再试一下"})
        retry_id = retried["task_id"]
        self.task_ids.append(retry_id)
        retry = self.wait(retry_id)
        self.require_completed(retry)
        assert not retry["pending_approvals"] and self.sql("select id from price_alert_rules") == before
        retry_checks = [c for c in self.invocations(retry_id) if c["tool_name"] == "check_watch_request" and c["status"] == "completed"]
        assert retry_checks, "Explicit retry did not check the original watch request"
        retry_plan = json.loads(retry_checks[0]["result_data"])
        assert retry_plan["original_text"] == json.loads(checks[0]["result_data"])["original_text"]
        assert set(retry_plan["unsupported_conditions"]) >= {"bar_close_confirmation", "moving_average_trigger", "consecutive_sessions"}
        variant_id = self.task("帮我给 CN:600519 设置提醒，只有连续三个交易日收盘高于999999才通知，不要改成盘中提醒。")
        variant = self.wait(variant_id)
        self.require_completed(variant)
        assert not variant["pending_approvals"] and self.sql("select id from price_alert_rules") == before
        variant_checks = [c for c in self.invocations(variant_id) if c["tool_name"] == "check_watch_request" and c["status"] == "completed"]
        assert variant_checks and set(json.loads(variant_checks[0]["result_data"])["unsupported_conditions"]) >= {"bar_close_confirmation", "consecutive_sessions"}
        assert any("尚未实现" in item for item in variant["result"]["missing_data"]), "Unmet conditions are absent from the structured result"
        return {"task_id":task_id,"model":task["model"],"rules_unchanged":True,"check":checks[0],"retry_task_id":retry_id,"retry_check":retry_checks[0],"ordinary_wording_task_id":variant_id,"ordinary_wording_check":variant_checks[0]}

    def monitoring_health(self):
        before = self.sql("select id,status,attempts from price_alert_deliveries order by id")
        task_id = self.task("查询我的价格提醒监控健康：最近成功检查、检查失败、每日触发额度和通知渠道投递状态。读取实际持久记录，不要改规则或补发；缺少 AI 金额预算时明确说明。")
        task = self.wait(task_id)
        self.require_completed(task)
        calls = self.invocations(task_id)
        reads = [c for c in calls if c["tool_name"] == "get_monitoring_health" and c["status"] == "completed"]
        assert reads, "Actual monitoring health tool did not complete"
        data = json.loads(reads[-1]["result_data"])
        assert data["scope"] == "price_alerts" and data["delivery_semantics"] == "at_least_once"
        assert data["ai_spend_budget"] == "not_enforced_by_price_alerts"
        assert data["total_rules"] == self.sql("select count(*) as n from price_alert_rules")[0]["n"]
        assert not any(c["tool_name"] in {"create_price_alert", "update_price_alert", "delete_price_alert"} for c in calls), "Read-only health query proposed a write"
        # The worker can naturally progress while reading. This query must not
        # create deliveries or approvals; it never uses a retry/write tool.
        after = self.sql("select id from price_alert_deliveries order by id")
        assert {r["id"] for r in before} <= {r["id"] for r in after}
        assert not task["pending_approvals"], "Read-only health query requested approval"
        return {"task_id": task_id, "model": task["model"], "health": data, "invocations": calls}

    def readonly_completed(self, task_id):
        task = self.wait(task_id)
        self.require_completed(task)
        assert not task["pending_approvals"], "Read-only request generated an approval"
        assert not self.sql("select id from assistant_tool_approvals where task_run_id=?", (task_id,)), "Read-only request has a historical approval"
        calls = self.invocations(task_id)
        assert not any(c["tool_name"] in {"create_price_alert", "update_price_alert", "delete_price_alert", "update_stock", "delete_stock"} for c in calls), "Read-only request proposed a write"
        return task, calls

    def or_combo(self):
        label = "Runtime OR " + uuid4().hex[:8]
        before = self.sql("select id from price_alert_rules")
        task_id = self.task(f"给 CN:600519 设个提醒，叫 {label}：价格高于999999或者量比大于999999，有效一周，通知发飞书，交易时段检查，冷却0分钟，每天不限次数，只提醒一次。")
        paused = self.wait(task_id)
        assert paused["status"] == "awaiting_approval", f"Expected approval, received {paused['status']}"
        assert self.sql("select id from price_alert_rules") == before
        approval = next(a for a in paused["pending_approvals"] if a["tool_name"] == "create_price_alert")
        self.decide(approval, "approved")
        task = self.wait(task_id)
        self.require_completed(task)
        rows = self.rules_named(label)
        assert len(rows) == 1
        rule = rows[0]
        group = json.loads(rule["condition_group"])
        assert group["op"] == "or" and sorted(group["items"], key=lambda i:i["type"]) == [{"type":"price","op":">","value":999999},{"type":"volume_ratio","op":">","value":999999}]
        assert rule["symbol"] == "600519" and rule["market"] == "CN"
        assert rule["repeat_mode"] == "once" and rule["market_hours_mode"] == "trading_only"
        assert rule["cooldown_minutes"] == 0 and rule["max_triggers_per_day"] == 0
        expiry = datetime.fromisoformat(rule["expire_at"]).replace(tzinfo=UTC)
        assert 6.9 < (expiry - datetime.now(UTC)).total_seconds()/86400 < 7.1
        channels = self.sql("select id from notify_channels where type='feishu' and enabled=1")
        assert set(json.loads(rule["notify_channel_ids"])) <= {c["id"] for c in channels} and json.loads(rule["notify_channel_ids"])
        calls = self.invocations(task_id)
        readback = json.loads(next(c["result_data"] for c in calls if c["tool_name"] == "create_price_alert" and c["status"] == "completed"))
        assert readback["condition_group"] == group and utc_instant(readback["expire_at"]) == utc_instant(rule["expire_at"]), "Rule readback expiry differs from the stored UTC instant"
        return {"task_id":task_id,"model":task["model"],"rule":rule,"invocations":calls}

    def research_history(self):
        # The expired opinion is synthetic, seeded before starting this QA run.
        seeds = self.sql("select id,stock_symbol,stock_market,reason from stock_suggestions where expires_at < datetime('now') order by id desc")
        assert seeds, "Seed a scoped expired synthetic opinion before starting QA"
        seed = seeds[0]
        task_id = self.task(f"上次对 {seed['stock_market']}:{seed['stock_symbol']} 是怎么看的？请找保存的历史研究，带上日期、原观点和是否已经过期，不要给我编一个新观点。")
        task, calls = self.readonly_completed(task_id)
        reads = [json.loads(c["result_data"]) for c in calls if c["tool_name"] == "get_research_history" and c["status"] == "completed"]
        assert reads, "Historical opinion tool was not used"
        items = [i for data in reads for i in data["items"]]
        item = next(i for i in items if i["kind"] == "suggestion" and i["id"] == seed["id"])
        assert item["symbol"] == seed["stock_symbol"] and item["market"] == seed["stock_market"]
        assert item["expired"] is True and item["reason"] == seed["reason"] and item["created_at"]
        assert all(i.get("market") == seed["stock_market"] for i in items)
        assert task["result"]["evidence"]
        return {"task_id":task_id,"model":task["model"],"synthetic_seed_id":seed["id"],"history":reads,"invocations":calls}

    def event_details(self):
        task_id = self.task("帮我读 CN:600519 过去365天最近一条有正文的公告，说明日期、来源链接和公告讲了什么。先找公告再读全文，不要只看标题；没有可用正文就明确说没有。")
        task, calls = self.readonly_completed(task_id)
        assert any(c["tool_name"] == "get_stock_events" and c["status"] == "completed" for c in calls)
        details = [json.loads(c["result_data"]) for c in calls if c["tool_name"] == "get_event_details" and c["status"] == "completed"]
        assert details, "No actual announcement details were read"
        data = next(d for d in details if d.get("content_available"))
        assert data["symbol"] == "600519" and data["market"] == "CN"
        assert data["content"] and data["published_at"] and data["url"].startswith("https://")
        assert any(e["tool_name"] == "get_event_details" and e["data_at"] and e["source_url"] for e in task["result"]["evidence"])
        return {"task_id":task_id,"model":task["model"],"announcement":data,"invocations":calls}

    def portfolio_diagnosis(self):
        task_id = self.task("诊断一下我的持仓，主要判断分别用了哪些数据、数据是什么时间的？缺少数据的地方请说清楚。")
        task, calls = self.readonly_completed(task_id)
        diagnosis = next(c for c in calls if c["tool_name"] == "portfolio_diagnosis" and c["status"] == "completed")
        data = json.loads(diagnosis["result_data"])
        assert data["nested_tools"] and data["diagnosis_steps"]
        result = task["result"]
        assert result["judgments"], "No source-linked portfolio judgments"
        evidence = {e["id"]: e for e in result["evidence"]}
        for judgment in result["judgments"]:
            if judgment["status"] == "completed":
                assert judgment["evidence_ids"], "A completed judgment has no evidence"
                assert all(key in evidence for key in judgment["evidence_ids"])
        assert any(e["tool_name"] != "portfolio_diagnosis" for e in evidence.values())
        return {"task_id":task_id,"model":task["model"],"judgments":result["judgments"],"evidence":result["evidence"],"invocations":calls}

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
                self.results.append({"id":name,"status":"PASS","layer":"live-model" if self.mode == "live-model" else "runtime-replay","business_outcome":case_outcome(name),"evidence":evidence})
            except Exception as exc:
                # Never echo credentials, provider payloads or unredacted logs.
                self.results.append({"id":name,"status":"FAIL","reason":str(exc)[:500],"exception_type":type(exc).__name__})
            print(f"{name}: {self.results[-1]['status']}", flush=True)
        overall = report_status(cases, self.results, self.mode)
        return {"overall":overall,"acceptance_scope":"Assertions for the listed runtime cases only; guardrail PASS does not fulfill an unsupported user goal","all_product_capabilities_accepted":False,"mode":self.mode,"run_id":self.manifest["run_id"],"source_sha":self.manifest["base_sha"],"source_hash":self.manifest["source_hash"],"frontend_hash":self.manifest.get("frontend_hash"),"evaluated_at":datetime.now(UTC).isoformat(),"production_path":"HTTP → task worker → AssistantService.build_runtime → registry/policy → approval → DB","cases":self.results,"task_ids":self.task_ids,"boundaries":["C01 stale replay is a separate contract test", "Notification delivery is not exercised", "This suite does not cover every model or arbitrary natural-language request", "Client SSE disconnection does not simulate a server network outage", "Bar-close, moving-average and consecutive-session alerts are not implemented", "Expired research records are synthetic QA fixtures, not fresh investment opinions"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--mode", choices=["live-model","replay"], default="live-model")
    parser.add_argument("--case", action="append", choices=REQUIRED_CASES)
    args = parser.parse_args()
    evaluator = RuntimeEvaluation(args.run, args.mode)
    try:
        report = evaluator.evaluate(args.case or list(REQUIRED_CASES))
    finally:
        evaluator.client.close()
    output = evaluator.run / "evidence" / f"runtime-eval-{uuid4().hex[:8]}.json"
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(f"{report['overall']} (listed runtime assertions only): {output}")
    return 0 if report["overall"] == "PASS" else 1 if report["overall"] == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
