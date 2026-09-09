"""Planning 试点 —— "全面诊断我的持仓"计划驱动编排。

范围刻意小:只覆盖单一场景(全面诊断持仓)。识别到该意图后走计划驱动:
LLM 生成结构化计划(逐持仓股分析 → 组合风险 → 汇总建议)→ 计划经 SSE `plan` 事件
推给前端 → 逐步执行(每步复用现有工具/LLM)→ 步骤失败重规划(上限 1 次,超限带失败
信息直接汇总)。

这是**试点**:验证"计划驱动"相对固定流程的价值,不做过度泛化。编排函数把工具执行器
(execute_tool)与 SSE 流(stream)作为依赖注入,便于单测全 mock。
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# 触发词:显式命中即走计划驱动(简单启发式,试点足够)
_PLANNING_TRIGGERS = (
    "全面诊断",
    "诊断我的持仓",
    "诊断一下我的持仓",
    "持仓诊断",
    "组合诊断",
    "全面体检",
    "持仓体检",
    "全面分析我的持仓",
)


def should_use_planning(content: str) -> bool:
    """判断用户输入是否命中"全面诊断持仓"场景。"""
    if not content:
        return False
    text = content.replace(" ", "")
    return any(t in text for t in _PLANNING_TRIGGERS)


_PLAN_SYSTEM = (
    "你是投资组合诊断规划助手。根据用户持仓,产出一个结构化诊断计划。"
    "只输出 JSON,形如:"
    '{"steps":[{"title":"分析 贵州茅台(600519)","action":"analyze_stock",'
    '"params":{"symbol":"600519","market":"CN"}},'
    '{"title":"组合整体风险","action":"portfolio_risk"}]}。'
    "action 取值:analyze_stock(逐只持仓,params 带 symbol/market)、portfolio_risk(组合风险)。"
    "不要包含汇总步骤,汇总由系统自动追加。"
)


def _plan_messages(portfolio_text: str) -> list[dict]:
    return [
        {"role": "system", "content": _PLAN_SYSTEM},
        {"role": "user", "content": f"我的持仓如下,请产出诊断计划:\n{portfolio_text}"},
    ]


def _replan_messages(
    portfolio_text: str, failed_title: str, error: str
) -> list[dict]:
    return [
        {"role": "system", "content": _PLAN_SYSTEM},
        {
            "role": "user",
            "content": (
                f"我的持仓:\n{portfolio_text}\n\n"
                f'上一版计划里的步骤「{failed_title}」执行失败({error}),'
                "请重新产出一份可执行的诊断计划(跳过或替换失败步骤)。"
            ),
        },
    ]


def parse_plan(text: str) -> list[dict] | None:
    """从 LLM 文本里容错解析计划步骤列表。

    支持:纯 JSON、```json 围栏包裹、前后有解释文字、尾部截断等常见脏输出。
    解析失败返回 None(交由调用方回退默认计划)。
    """
    if not text:
        return None

    blob = None
    m = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", text, re.S)
    if m:
        blob = m.group(1)
    else:
        candidates = [i for i in (text.find("{"), text.find("[")) if i >= 0]
        if candidates:
            blob = text[min(candidates):]

    if not blob:
        return None

    data = None
    for attempt in (blob, blob[: max(blob.rfind("]"), blob.rfind("}")) + 1]):
        try:
            data = json.loads(attempt)
            break
        except Exception:
            continue
    if data is None:
        return None

    if isinstance(data, dict):
        data = data.get("steps") or data.get("plan")
    if not isinstance(data, list) or not data:
        return None
    return data


def build_default_plan(portfolio_text: str) -> list[dict]:
    """LLM 计划不可用时的降级默认计划(仅做组合风险,汇总由系统追加)。"""
    return [{"title": "组合整体风险评估", "action": "portfolio_risk"}]


def normalize_steps(steps: list[dict], start_id: int = 1) -> list[dict]:
    """规范化步骤:补 id/title/action/params/status。过滤 summarize(汇总系统自动做)。"""
    out = []
    sid = start_id
    for s in steps:
        if not isinstance(s, dict):
            continue
        action = s.get("action") or "portfolio_risk"
        if action == "summarize":
            continue
        out.append(
            {
                "id": sid,
                "title": s.get("title") or f"步骤 {sid}",
                "action": action,
                "params": s.get("params") or {},
                "status": "pending",
            }
        )
        sid += 1
    return out


def _steps_public(steps: list[dict]) -> list[dict]:
    return [{"id": s["id"], "title": s["title"], "status": s["status"]} for s in steps]


async def _publish_plan(stream, steps: list[dict], status: str, current=None) -> None:
    data = {"status": status, "steps": _steps_public(steps)}
    if current is not None:
        data["current"] = current
    await stream.publish("plan", data)


_STEP_SYSTEM = "你是资深投研分析师。基于给定数据,给出精炼、有据的分析(150 字内)。"
_SUMMARY_SYSTEM = (
    "你是资深投资顾问。基于各步骤的分析结果,给出全面的持仓诊断结论:"
    "整体健康度、主要风险、可执行的调仓建议。分点、精炼、有据。"
)


async def _execute_step(db, ai_client, execute_tool, step: dict, portfolio_text: str) -> str:
    """执行单个计划步骤,返回该步的分析文本。"""
    action = step["action"]
    if action == "analyze_stock":
        p = step.get("params") or {}
        symbol = p.get("symbol", "")
        market = p.get("market", "CN")
        tech = await execute_tool(db, "get_technical_analysis", {"symbol": symbol, "market": market})
        sug = await execute_tool(db, "get_stock_suggestions", {"symbol": symbol, "market": market})
        msgs = [
            {"role": "system", "content": _STEP_SYSTEM},
            {
                "role": "user",
                "content": f"分析持仓「{step['title']}」。\n技术面:\n{tech}\n\nAI 建议:\n{sug}",
            },
        ]
        return await ai_client.chat_multi(msgs, temperature=0.4)

    # portfolio_risk 及其它未知 action:统一按组合风险处理
    msgs = [
        {"role": "system", "content": _STEP_SYSTEM},
        {"role": "user", "content": f"评估以下持仓组合的整体风险:\n{portfolio_text}"},
    ]
    return await ai_client.chat_multi(msgs, temperature=0.4)


def _summary_messages(results: list[tuple[str, str]]) -> list[dict]:
    body = "\n\n".join(f"【{title}】\n{res}" for title, res in results)
    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": f"以下是各步骤的诊断结果,请汇总:\n\n{body}"},
    ]


async def run_portfolio_diagnosis(db, stream, ai_client, execute_tool) -> str:
    """计划驱动的"全面诊断持仓"编排,返回最终汇总文本(已通过 SSE 流式推送)。

    Args:
        db: DB session。
        stream: SSEStream(需支持 async publish(event, data))。
        ai_client: AI 客户端(chat_multi / chat_stream)。
        execute_tool: async (db, name, args) -> str 工具执行器。
    """
    await stream.publish("plan", {"status": "planning", "steps": []})

    portfolio_text = await execute_tool(db, "get_portfolio", {})

    # 1) 生成计划(失败/解析不了则回退默认计划)
    steps = None
    try:
        raw = await ai_client.chat_multi(_plan_messages(portfolio_text), temperature=0.3)
        steps = parse_plan(raw)
    except Exception:
        logger.warning("生成诊断计划失败,回退默认计划", exc_info=True)
    if not steps:
        steps = build_default_plan(portfolio_text)
    steps = normalize_steps(steps)
    if not steps:
        steps = normalize_steps(build_default_plan(portfolio_text))

    await _publish_plan(stream, steps, status="running")

    # 2) 逐步执行,失败重规划(上限 1 次)
    results: list[tuple[str, str]] = []
    replanned = False
    i = 0
    while i < len(steps):
        step = steps[i]
        step["status"] = "running"
        await _publish_plan(stream, steps, status="running", current=step["id"])
        try:
            res = await _execute_step(db, ai_client, execute_tool, step, portfolio_text)
            step["status"] = "done"
            results.append((step["title"], res))
        except Exception as e:  # noqa: BLE001
            if not replanned:
                replanned = True
                logger.info("步骤「%s」失败,触发重规划: %s", step["title"], e)
                try:
                    raw = await ai_client.chat_multi(
                        _replan_messages(portfolio_text, step["title"], str(e)),
                        temperature=0.3,
                    )
                    new_steps = parse_plan(raw)
                except Exception:
                    new_steps = None
                if new_steps:
                    steps = steps[:i] + normalize_steps(new_steps, start_id=step["id"])
                    await _publish_plan(stream, steps, status="running")
                    continue  # 从当前位置用新计划重试
            # 已重规划过或重规划失败:标记失败,带失败信息继续汇总
            step["status"] = "failed"
            results.append((step["title"], f"(该步执行失败:{e})"))
        await _publish_plan(stream, steps, status="running")
        i += 1

    # 3) 汇总(流式推 token)
    summary = ""
    try:
        parts: list[str] = []
        async for kind, payload in ai_client.chat_stream(
            _summary_messages(results), temperature=0.4
        ):
            if kind == "token":
                parts.append(payload)
                await stream.publish("token", {"text": payload})
        summary = "".join(parts)
    except Exception as e:  # noqa: BLE001 — 流式汇总失败降级为非流式
        logger.warning("流式汇总失败,降级非流式: %s", e)
        try:
            summary = await ai_client.chat_multi(_summary_messages(results), temperature=0.4)
            await stream.publish("token", {"text": summary})
        except Exception:
            summary = "抱歉,诊断汇总失败。"
            await stream.publish("token", {"text": summary})

    await _publish_plan(stream, steps, status="done")
    return summary
