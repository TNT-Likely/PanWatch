"""短线归因 Agent:判断一只股票短线上涨/涨停/异动的核心原因。

方法论移植自 wudao-stock-attribution skill(5W 证据收集 + 触发/主因/载体三拆解 +
三一致性评分)。数据源全部走 PanWatch 原生 MarketData(东财/腾讯,免 key):
- dragon_tiger:龙虎榜(资金证据)
- events:公告/事件(信息证据)
- capital_flow:资金流(资金证据)
- news:新闻(信息证据)
- limit_up_pool:涨停池(市场证据)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from src.agents.base import AgentContext, BaseAgent

logger = logging.getLogger(__name__)


def md_quote_rows_wrapper(symbols: list[str], market: str) -> list:
    """行情快照 wrapper(同步函数,供 to_thread 调用)。"""
    from src.core.marketdata_client import md_quote_rows

    return md_quote_rows(symbols, market)


class StockAttributionAgent(BaseAgent):
    """短线归因:为什么涨/为什么涨停/异动核心原因。"""

    name = "stock_attribution"
    display_name = "短线归因"
    description = "分析个股短线上涨/涨停/异动的核心原因(触发/主因/载体三拆解)"

    async def collect(self, context: AgentContext) -> dict:
        """采集归因数据:龙虎榜 + 公告 + 资金流 + 涨停池。"""
        trace_id = datetime.now().strftime("%m%d%H%M%S%f")[-10:]
        start_ts = __import__("time").monotonic()

        symbols = [s.symbol for s in context.watchlist]
        if not symbols:
            logger.warning("[%s] 无自选股,归因 Agent 需要至少 1 只股票", trace_id)
            return {"error": "无自选股", "timestamp": datetime.now().isoformat()}

        today = datetime.now().strftime("%Y-%m-%d")
        data = {"symbols": symbols, "today": today}

        try:
            from src.core.marketdata_client import get_market_data

            md = get_market_data()

            # 1. 龙虎榜(今日上榜的归因目标)
            try:
                dt = md.dragon_tiger(date=today, market="CN")
                # 过滤出与自选股相关的
                related = [d for d in dt if d.symbol in symbols]
                data["dragon_tiger"] = [
                    {
                        "symbol": d.symbol,
                        "name": d.name,
                        "reason": d.reason,
                        "close": d.close,
                        "change_pct": d.change_pct,
                        "net_buy": d.net_buy,
                        "buy_amt": d.buy_amt,
                        "sell_amt": d.sell_amt,
                        "turnover_pct": d.turnover_pct,
                    }
                    for d in related
                ]
                logger.info("[%s] 龙虎榜: total=%s related=%s", trace_id, len(dt), len(related))
            except Exception as e:
                logger.warning("[%s] 龙虎榜采集失败: %s", trace_id, e)
                data["dragon_tiger"] = []

            # 2. 资金流(每只自选股)
            flows = []
            for sym in symbols:
                try:
                    flow = md.capital_flow(sym, market="CN")
                    if flow:
                        flows.append(
                            {
                                "symbol": sym,
                                "main_net_inflow": flow.main_net_inflow,
                                "main_net_inflow_pct": flow.main_net_inflow_pct,
                                "super_net_inflow": flow.super_net_inflow,
                                "main_net_5d": flow.main_net_5d,
                            }
                        )
                except Exception as e:
                    logger.debug("[%s] 资金流失败 %s: %s", trace_id, sym, e)
            data["capital_flows"] = flows

            # 2.5 行情快照(涨幅/换手/量比 — 归因的核心市场证据)
            quotes = []
            try:
                rows = await asyncio.to_thread(
                    lambda: md_quote_rows_wrapper(symbols, "CN")
                )
                quotes = rows
                logger.info("[%s] 行情快照: %s 条", trace_id, len(rows))
            except Exception as e:
                logger.warning("[%s] 行情快照失败: %s", trace_id, e)
            data["quotes"] = quotes

            # 3. 公告/事件(信息证据)
            events = md.events(symbols, market="CN", since_days=7)
            data["events"] = [
                {
                    "symbol": getattr(e, "symbol", ""),
                    "title": getattr(e, "title", ""),
                    "content": (getattr(e, "content", "") or "")[:200],
                    "time": str(getattr(e, "publish_time", "") or ""),
                }
                for e in events[:30]
            ]
            logger.info("[%s] 事件: %s 条", trace_id, len(events))

        except Exception as e:
            logger.warning("[%s] 归因数据采集失败: %s", trace_id, e)
            data["collect_error"] = str(e)

        logger.info(
            "[%s] 归因采集完成: elapsed_ms=%s",
            trace_id,
            int((__import__("time").monotonic() - start_ts) * 1000),
        )

        return {
            "attribution_data": data,
            "timestamp": datetime.now().isoformat(),
            "run_trace_id": trace_id,
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """构建短线归因 prompt(5W + 三拆解 + 三一致性)。"""
        system_prompt = """你是一个A股短线归因分析员。你的任务:判断自选股短线上涨/涨停/异动的**核心原因**。

目标不是贴概念标签,而是回答:
- 为什么是这只股票?
- 为什么是这个时间点?
- 为什么涨到这个强度?
- 最能解释上涨的增量因素是什么?

【分析框架】

## 5W 证据收集
- When:拉升起点是什么时间?
- With:同步异动的股票有哪些?
- Where:共同归属什么题材/行业/风格?
- What:拉升前后发生了什么新闻、公告、政策、研报?
- Why:为什么资金选择这只股票作为载体?

## 三拆解(结论必须按此结构)
- 触发原因:市场开始交易什么方向
- 交易主因:短线资金为什么今天买它
- 载体原因:为什么是这只股票承载这个方向

## 三一致性评分(每项 0-1)
- peerConsistency:同伴股是否共同上涨,题材是否真实联动
- timeConsistency:证据发布时间是否早于或同步于拉升
- volumeConsistency:涨幅、成交量、换手、封单、资金流是否配合

## 候选原因类型
market(市场普涨) / style(小盘低价国资次新等风格) / theme(题材联动) /
company_event(公告业绩订单重组) / trading_structure(连板龙头补涨卡位) /
unknown(证据不足)

【硬约束】
- 不要把"股票属于某概念"直接当作主因
- 题材归因但同伴股不涨 → 不能硬编
- 公告发布时间明显晚于拉升 → 不能作为主因
- 官方公告否认相关业务 → 必须降置信度
- 证据不足 → 明确输出"不确定",不要强行归因
- 不给买卖建议,不承诺后续涨跌

【输出格式】
结论:
主因:...
置信度:高/中/低/不确定

一句话解释:
...

拆解:
- 触发原因:...
- 交易主因:...
- 载体原因:...

证据:
1. 信息证据(公告/新闻):...
2. 市场证据(涨停/龙虎榜):...
3. 资金证据(资金流):...

反证/不确定性:
...

后续观察:
1. ...
2. ...
3. ..."""

        user_content = []
        ad = data.get("attribution_data", {}) or {}
        user_content.append(f"## 日期:{data.get('timestamp', datetime.now().isoformat())[:10]}\n")

        if ad.get("error"):
            user_content.append(f"错误:{ad['error']}")
            return system_prompt, "\n".join(user_content)

        # 自选股
        syms = ad.get("symbols", [])
        user_content.append(f"## 待归因标的:{'、'.join(syms)}\n")

        # 龙虎榜
        dt = ad.get("dragon_tiger", [])
        if dt:
            user_content.append("## 龙虎榜(今日上榜)")
            for d in dt:
                user_content.append(
                    f"- {d['name']}({d['symbol']}) 净买:{d['net_buy']/1e8:.2f}亿 "
                    f"买入:{d['buy_amt']/1e8:.2f}亿 卖出:{d['sell_amt']/1e8:.2f}亿 "
                    f"换手:{d['turnover_pct']:.1f}% 涨幅:{d['change_pct']:.2f}%"
                )
                user_content.append(f"  原因:{d['reason']}")
            user_content.append("")

        # 资金流
        flows = ad.get("capital_flows", [])
        if flows:
            user_content.append("## 资金流")
            for f in flows:
                user_content.append(
                    f"- {f['symbol']}: 主力净流入{f['main_net_inflow']/1e8:.2f}亿 "
                    f"占比{f['main_net_inflow_pct']*100:.1f}% "
                    f"超大单{f['super_net_inflow']/1e8:.2f}亿 "
                    f"5日{f['main_net_5d']/1e8:.2f}亿"
                )
            user_content.append("")

        # 行情快照
        quotes = ad.get("quotes", [])
        if quotes:
            user_content.append("## 行情快照(今日)")
            for q in quotes:
                name = q.get("name", q.get("symbol", ""))
                price = q.get("current_price", "--")
                chg = q.get("change_pct", "--")
                vol = q.get("volume", "--")
                user_content.append(f"- {name}({q.get('symbol','')}): 价格{price} 涨跌{chg}% 成交量{vol}")
            user_content.append("")

        # 公告/事件
        events = ad.get("events", [])
        if events:
            user_content.append("## 公告/事件(近7天)")
            for e in events[:15]:
                user_content.append(f"- [{e['symbol']}] {e['title']}")
                if e.get("content"):
                    user_content.append(f"  {e['content'][:100]}")
            user_content.append("")

        return system_prompt, "\n".join(user_content)
