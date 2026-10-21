"""Reader-facing reports from captured facts and approved plans, without model calls."""
from datetime import datetime
import math
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from src.modules.research.signals.actions import ACTION_LABELS, ACTION_LABELS_EN, normalize_suggestion


def build_report(agent_name, research, *, securities=(), evidence=(), scope=(), as_of="", language="zh-CN", timezone="Asia/Shanghai", elapsed_seconds=None):
    en = language == "en-US"
    def tr(zh, english):
        return english if en else zh
    def text(value):
        return str(value or "").replace("\n", " ").replace("[", "\\[").replace("]", "\\]")
    def number(value, *, signed=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return "—"
        return f"{value:+,.2f}" if signed else f"{value:,.2f}"
    def stamp(value):
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo:
                dt = dt.astimezone(ZoneInfo(timezone))
            return dt.strftime("%Y-%m-%d %H:%M %Z").strip()
        except (ValueError, TypeError):
            return text(value)

    artifacts = research.get("artifacts", [])
    sources = {e["id"]: e for e in [*evidence, *(e for a in artifacts for e in a.get("evidence", []))]}
    keys = list(dict.fromkeys([s["key"] for s in securities] or scope or [r["stock_key"] for r in research.get("recommendations", [])]))
    sources = {key: e for key, e in sources.items() if e["stock_key"] in [*keys, "*"]}
    names = {s["key"]: s.get("name") or s["key"] for s in securities}
    latest = {}
    for a in artifacts:
        identity = (a["id"].split("/")[0], a["role"])
        if identity not in latest or a["epoch"] > latest[identity]["epoch"]:
            latest[identity] = a
    epochs = {}
    for (group, role), a in latest.items():
        if role in {"technical", "flow", "events"}:
            epochs[group] = max(epochs.get(group, 0), a["epoch"])
    latest = {identity: a for identity, a in latest.items() if a["role"] not in {"manager", "bull", "bear"}
              or a["epoch"] >= epochs.get(identity[0], 0)}
    fast = research.get("graph") == "intraday_fast"
    accepted = {c for a in latest.values() if a["role"] == "manager" for c in a["report"].get("accepted_claim_ids", [])}
    if fast:
        accepted = {c["id"] for a in latest.values() if a["role"] in {"technical", "events"} for c in a["report"].get("claims", [])}
    facts = {}
    for e in sorted(sources.values(), key=lambda e: (e["available_at"], e["id"].endswith("/detail"))):
        facts[(e["stock_key"], e["domain"])] = e["content"]
        quote = e["content"].get("quote") or {}
        if quote.get("name"):
            names[e["stock_key"]] = quote["name"]
    def name(key):
        return f"{text(names.get(key, key))}（{key}）" if names.get(key, key) != key else key
    def claims(key, domain):
        return list(dict.fromkeys(c["statement"] for a in latest.values() if a["role"] == domain
            for c in a["report"].get("claims", []) if c["stock_key"] == key and c["id"] in accepted))

    notes = {n["stock_key"]: n for a in latest.values() if a["role"] == "strategy" for n in a["report"].get("session_notes", [])}
    completion = research.get("completion", "complete")
    states = {"complete": tr("研究完成", "Complete"), "skipped": tr("无实质变化，已跳过研究", "No material change; research skipped"), "timeout": tr("研究超时", "Timed out"),
        "budget_exhausted": tr("研究预算耗尽", "Budget exhausted"), "validation_failed": tr("结论校验未通过", "Validation failed"),
        "risk_rejected": tr("风控未通过", "Risk rejected")}
    lines = [f"{states.get(completion, completion)} · {tr('数据截至', 'Data as of')} {stamp(as_of)}"]
    if completion not in {"complete", "skipped"}:
        lines += [tr("**研究未完成：以下仅为草案，未发布正式建议或预测。**", "**Incomplete research: drafts only; no formal recommendations or predictions published.**")]
        if research.get("reason"):
            lines.append(tr("原因：", "Reason: ") + research["reason"])
    lines.append("## " + tr("隔夜市场" if agent_name == "premarket_outlook" else "大盘概览", "Overnight markets" if agent_name == "premarket_outlook" else "Market overview"))
    indices = {}
    for e in sources.values():
        for idx in e["content"].get("indices") or []:
            identity = (idx.get("market"), idx.get("symbol"), idx.get("name"))
            indices[identity] = idx
    for idx in indices.values():
        line = f"- **{text(idx.get('name') or idx.get('symbol'))}** {number(idx.get('current_price', idx.get('current')))} · {number(idx.get('change_pct'), signed=True)}%"
        if idx.get("turnover") is not None:
            amount = idx["turnover"] / (1e9 if en else 1e8)
            units = {"CN": tr(" 亿元", " bn CNY"), "HK": tr(" 亿港元", " bn HKD"), "US": tr(" 亿美元", " bn USD")}
            line += tr(" · 成交额 ", " · Turnover ") + number(amount) + units.get(idx.get("market"), tr(" 亿（当地货币）", " bn (local currency)"))
        lines.append(line)
    if not indices:
        lines.append(tr("本次未采集大盘指数，暂不判断整体市场。", "No index snapshot was captured; no broad-market assessment is available."))

    plans = {r["stock_key"]: normalize_suggestion(r) for r in research.get("recommendations", []) if r["stock_key"] in keys}
    constraints = {"buying_power_unverified": tr("币种与结算购买力未核实", "Currency and settled buying power unverified"),
        "account_missing": tr("未配置交易账户", "No trading account configured"), "insufficient_funds": tr("账户可用资金不足", "Insufficient available funds"),
        "sellable_quantity_unverified": tr("可卖数量未核实", "Sellable quantity unverified"),
        "quote_unavailable_or_expired": tr("报价缺失或已过期", "Quote unavailable or expired"),
        "review_required": tr("需人工复核", "Human review required"), "research_incomplete": tr("研究尚未完成", "Research incomplete")}
    outlooks = {"bullish": tr("看多", "Bullish"), "neutral": tr("中性", "Neutral"), "bearish": tr("看空", "Bearish")}
    executions = {"conditional": tr("待条件确认", "Conditional"), "blocked": tr("执行受限", "Blocked"), "not_applicable": tr("无需下单", "No order needed")}
    lines.append("## " + tr("个股分析", "Stock analysis"))
    for key in keys:
        lines.append("### " + name(key))
        technical = facts.get((key, "technical"), {})
        quote, indicators = technical.get("quote") or {}, technical.get("indicators") or {}
        if quote:
            lines.append(tr("行情：", "Quote: ") + f"{number(quote.get('current_price'))} · {number(quote.get('change_pct'), signed=True)}%" + (f" · {stamp(quote['timestamp'])}" if quote.get("timestamp") else ""))
        for domain, label in (("technical", tr("技术面", "Technicals")), ("flow", tr("资金面", "Capital flow")), ("events", tr("消息面", "News and events"))):
            statements = claims(key, domain)
            if statements:
                lines.append(f"**{label}：** " + "；".join(statements))
            elif domain == "technical" and indicators:
                values = [str(indicators[k]) for k in ("trend", "macd_status", "rsi_status", "volume_trend") if indicators.get(k)]
                lines.append(f"**{label}：** " + (" · ".join(values) or tr("仅有指标快照，未形成可采纳的分析。", "Indicators only; no accepted assessment.")))
            else:
                candidates = [a for a in latest.values() if a["role"] == domain and
                    {e["stock_key"] for e in a.get("evidence", []) if e["stock_key"] != "*"} == {key}]
                missing = (candidates[-1]["report"]["summary"] if fast and candidates else
                    tr("本轻图未运行该领域专家。", "This fast run did not include this domain specialist.") if fast and not any(a["role"] == domain for a in latest.values()) else
                    tr("暂无可采纳的研究结论。", "No accepted assessment available."))
                lines.append(f"**{label}：** " + missing)
        if note := notes.get(key):
            if agent_name == "premarket_outlook":
                lines += ["**" + tr("开盘情景", "Opening scenarios") + "：** " + note["summary"],
                    "- " + tr("向上情景：", "Upside scenario: ") + note["upside"],
                    "- " + tr("向下情景：", "Downside scenario: ") + note["downside"]]
            elif agent_name == "daily_report":
                verdicts = {"confirmed": tr("已印证", "Supported"), "refuted": tr("被推翻", "Refuted"), "mixed": tr("部分印证", "Mixed"),
                    "unresolved": tr("尚待验证", "Unresolved"), "no_prior": tr("未提供同日盘前计划，不作兑现评价", "No same-day premarket plan supplied; no fulfilment assessment")}
                lines += ["**" + tr("收盘归因", "Session attribution") + "：** " + note["summary"],
                    "- " + tr("盘前计划复核：", "Premarket plan review: ") + verdicts[note["prior_verdict"]]]
        rec = plans.get(key)
        if not rec:
            lines.append(tr("**最终结论：** 尚无有效投研结论。", "**Conclusion:** No validated recommendation available."))
            continue
        action = (ACTION_LABELS_EN if en else ACTION_LABELS)[rec["action"]]
        prefix = tr("草案", "Draft") if completion != "complete" else tr("最终结论", "Conclusion")
        lines += [f"**{prefix}：** {outlooks[rec['outlook']]} · {tr('置信度', 'Confidence')} {rec['confidence']:.0%} · {tr('条件性建议', 'Conditional action')}：{action}", rec["reason"]]
        lines.append(f"**{tr('执行状态', 'Execution')}：** {executions[rec['execution_status']]}")
        for label, values in ((tr("执行限制", "Execution constraints"), [constraints.get(c, c) for c in rec.get("execution_constraints", [])]),
                              (tr("失效条件", "Invalidations"), rec.get("invalidations", []))):
            if values:
                lines.append(f"- {label}：" + "；".join(values))

    lines.append("## " + tr("风险提示", "Risks"))
    risks = [f"- **{name(key)}**：" + "；".join(rec.get("risks", [])) for key, rec in plans.items() if rec.get("risks")]
    lines += risks or [tr("本次未形成独立风险清单，请结合各股失效条件复核。", "No separate risk list; review each stock's invalidation conditions.")]
    account_limits = list(dict.fromkeys(constraints.get(c, c) for rec in plans.values() for c in rec.get("execution_constraints", [])))
    if account_limits:
        lines.append(tr("- **账户与执行**：", "- **Account and execution**: ") + "；".join(account_limits))
    focus = {"daily_report": tr("明日关注", "Next-session watchlist"), "premarket_outlook": tr("今日关注", "Today's watchlist"), "intraday_monitor": tr("后续关注", "What to monitor next")}[agent_name]
    lines.append("## " + focus)
    if completion not in {"complete", "skipped"}:
        lines.append(tr("研究未完成，以下仅为草案中的待核实条件。", "Research is incomplete; these are draft conditions to verify."))
    for key, rec in plans.items():
        lines.append(f"- **{name(key)}**：" + "；".join(rec.get("triggers") or [tr("暂无明确触发条件，等待补充证据。", "No explicit trigger; await further evidence.")]))
    if not plans:
        lines.append(tr("暂无有效关注条件，需补充证据并完成研究。", "No validated watch conditions; further evidence and completed research are required."))

    actions, summary = [], ["## " + tr("操作建议", "Action summary")]
    if completion not in {"complete", "skipped"}:
        summary.append(tr("**研究未完成，暂无正式操作建议；草案不作为操作指令。**", "**Research incomplete: no formal action; drafts are not instructions.**"))
    for key, rec in plans.items() if completion == "complete" else []:
        actions.append({**rec, "stock_name": names.get(key, key)})
        action = (ACTION_LABELS_EN if en else ACTION_LABELS)[rec["action"]]
        summary += [f"### {name(key)} · {tr('操作建议', 'Action')}：**{action}**",
            f"{outlooks[rec['outlook']]} · {tr('置信度', 'Confidence')} {rec['confidence']:.0%}",
            tr("**关注 / 触发条件：** ", "**Watch / trigger conditions:** ") + "；".join(rec.get("triggers") or [tr("暂无明确条件，等待补充证据。", "No explicit conditions; await evidence.")])]
        if rec["execution_status"] == "blocked":
            summary.append(tr("**下单限制：** ", "**Order restrictions:** ") + "；".join(constraints.get(c, c) for c in rec.get("execution_constraints", [])))
        else:
            summary.append(tr("无需下单" if rec["execution_status"] == "not_applicable" else "仅在条件满足并核实下单条件后执行", "No order needed" if rec["execution_status"] == "not_applicable" else "Act only after confirming triggers and order eligibility"))
    if completion == "complete" and not actions:
        summary.append(tr("暂无有效操作建议。", "No validated action available."))
    action_summary = "\n\n".join(summary)

    roles = {"execution": tr("执行校验", "Execution checks"), "gate": tr("异动门控", "Change gate"), "technical": tr("技术分析", "Technical analysis"), "flow": tr("资金分析", "Capital-flow analysis"), "events": tr("事件分析", "Event analysis"),
        "bull": tr("多头论证", "Bull case"), "bear": tr("空头复核", "Bear case"), "manager": tr("研究经理裁定", "Research manager"),
        "strategy": tr("组合策略", "Portfolio strategy"), "risk": tr("风险审查", "Risk review"), "decision": tr("最终决策", "Final decision")}
    if fast:
        roles["strategy"] = tr("盘中决策", "Intraday decision")
        roles["risk"] = tr("规则校验", "Rule checks")
    def group_label(a):
        group = a["id"].split("/")[0]
        return tr("跨股组合", "Portfolio") if group == "portfolio" else tr("研究组 ", "Research group ") + str(int(group) + 1)
    active_ids = {v["id"] for v in latest.values()}
    process = [{"id": a["id"], "role": a["role"], "group": a["id"].split("/")[0], "epoch": a["epoch"],
                "verdict": a["report"].get("verdict", "ready"), "kind": a.get("kind", "model"), "active": a["id"] in active_ids,
                "feedback_roles": list(dict.fromkeys(i.get("respondent") or i["domain"] for i in a["report"].get("issues", []))),
                "title": f"{roles[a['role']]} · {group_label(a)} · {tr('轮次', 'Round')} {a['epoch'] + 1}", "content": a["report"]["summary"]}
               for a in sorted(artifacts, key=lambda a: (a["epoch"], list(roles).index(a["role"]), a["id"]))]
    evidence_rows = []
    snapshots = {}
    for e in sources.values():
        identity = e["id"].removesuffix("/summary").removesuffix("/detail")
        if identity not in snapshots or e["id"].endswith("/detail"):
            snapshots[identity] = e
    for e in sorted(snapshots.values(), key=lambda e: (e["stock_key"], e["domain"], e["available_at"])):
        body = e["content"]
        domains = {"technical": tr("行情与技术指标", "Quotes and indicators"), "flow": tr("资金与指数", "Capital flow and indices"), "events": tr("新闻与公告", "News and announcements"), "market": tr("市场资料", "Market data"), "portfolio": tr("账户快照", "Account snapshot")}
        source_keys = {"technical": ("quote", "kline"), "flow": ("capital_flow", "quote"), "events": ("news", "events")}.get(e["domain"], ())
        providers = list(dict.fromkeys(body.get("sources", {}).get(k) for k in source_keys if body.get("sources", {}).get(k)))
        provider_names = {"tencent": tr("腾讯行情", "Tencent quotes"), "eastmoney": tr("东方财富", "Eastmoney"), "db": tr("新闻库", "News archive")}
        providers = [provider_names.get(p, p) for p in providers]
        content = [tr("采集时间：", "Captured: ") + stamp(e["available_at"])]
        quote, indicators, flow = body.get("quote") or {}, body.get("indicators") or {}, body.get("capital_flow") or {}
        if quote:
            content.append(tr("行情：", "Quote: ") + f"{number(quote.get('current_price'))} · {number(quote.get('change_pct'), signed=True)}%")
        if indicators:
            content.append(" · ".join(f"{k.upper()} {number(indicators[k])}" for k in ("ma5", "ma10", "ma20", "rsi6", "support", "resistance") if indicators.get(k) is not None))
        if flow.get("main_net_inflow") is not None:
            content.append(tr("主力净流入：", "Main net inflow: ") + number(flow["main_net_inflow"]) + tr("（当地货币）", " (local currency)"))
        for item in body.get("items") or []:
            url = item.get("url") or ""
            title = text(item.get("title"))
            link = f"[{title}]({url.replace(')', '%29')})" if urlsplit(url).scheme in {"https", "http"} else title
            content.append(f"- {link} · {text(item.get('source'))} · {text(item.get('time'))}")
        if not body.get("items"):
            content += ["- " + c for c in claims(e["stock_key"], e["domain"])]
        preview = "\n\n".join(content[1:]) if not body.get("items") else tr(f"已采集 {len(body['items'])} 条新闻与公告", f"{len(body['items'])} news and announcement items captured")
        content += [f"`{e['id']}`"]
        evidence_rows.append({"id": e["id"], "stock_key": e["stock_key"], "stock_name": names.get(e["stock_key"], e["stock_key"]),
            "domain": e["domain"], "domain_label": domains[e["domain"]], "providers": providers or [e["source"]],
            "captured_at": stamp(e["available_at"]), "summary": preview,
            "items": [{"title": str(item.get("title") or ""), "source": str(item.get("source") or ""), "time": str(item.get("time") or ""),
                       "url": item.get("url") if urlsplit(item.get("url") or "").scheme in {"https", "http"} else ""} for item in body.get("items") or []],
            "title": f"{name(e['stock_key'])} · {domains[e['domain']]} · {text(', '.join(providers) or e['source'])}", "content": "\n\n".join(content)})
    return {"version": 3, "body": "\n\n".join(lines), "action_summary": action_summary, "actions": actions,
            "content": "\n\n".join([lines[0], action_summary, *lines[1:]]), "process": process, "evidence": evidence_rows,
            "runtime": {"completion": completion, "graph": research.get("graph", "research"), "as_of": stamp(as_of), "stock_count": len(keys), "elapsed_seconds": elapsed_seconds},
            "models": sorted({a["model"] for a in artifacts if a.get("model")}), "usage": research.get("usage", {})}
