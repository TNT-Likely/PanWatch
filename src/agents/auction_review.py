"""竞价复盘 Agent(9:30 触发):wudao 竞价数据 + 竞价博弈方法论。

方法论移植自 wudao-auction-review skill:
1. 情绪定性(sentimentSignal: cooling/neutral/warming)
2. 主线定向(consistency 一致性,不是总额最大)
3. 盯盘名单(bidStrength 排序,3-5 只)

数据源:wudao MCP(HTTP 直连,含 consistency/bidStrength/弱转强等独家字段)。
"""
from __future__ import annotations

import logging
from datetime import datetime

from src.agents.base import AgentContext, BaseAgent

logger = logging.getLogger(__name__)


class AuctionReviewAgent(BaseAgent):
    """竞价复盘:9:30 后解读当日竞价(情绪/主线/盯盘名单)。"""

    name = "auction_review"
    display_name = "竞价复盘"
    description = "9:30后解读当日集合竞价:情绪定性/主线定向/盯盘名单"

    async def collect(self, context: AgentContext) -> dict:
        """采集 wudao 竞价数据:全景 + 题材一致性 + 个股强度 + 弱转强。"""
        trace_id = datetime.now().strftime("%m%d%H%M%S%f")[-10:]
        start_ts = __import__("time").monotonic()

        data: dict = {}

        try:
            from src.collectors.wudao_mcp_client import WudaoMCPClient

            client = WudaoMCPClient()
            data["client_ok"] = True

            # 1. 竞价全景(涨停/跌停/昨炸板反馈/委买额)
            try:
                data["opening_snapshot"] = client.auction_opening_snapshot(limit=30)
                logger.info("[%s] 竞价全景 OK", trace_id)
            except Exception as e:
                logger.warning("[%s] 竞价全景失败: %s", trace_id, e)
                data["opening_snapshot"] = {}

            # 2. 题材一致性(主线定向,最值钱)
            try:
                data["theme_strength"] = client.auction_theme_strength(
                    limit=10, theme_source="concept"
                )
                logger.info("[%s] 题材一致性 OK", trace_id)
            except Exception as e:
                logger.warning("[%s] 题材一致性失败: %s", trace_id, e)
                data["theme_strength"] = {}

            # 3. 个股竞价强度榜(bidStrength)
            try:
                data["market_scan"] = client.auction_market_scan(
                    sort_by="bidStrength", limit=10
                )
                logger.info("[%s] 竞价强度榜 OK", trace_id)
            except Exception as e:
                logger.warning("[%s] 竞价强度榜失败: %s", trace_id, e)
                data["market_scan"] = {}

            # 4. 弱转强(昨炸板反包)
            try:
                data["weak_to_strong"] = client.auction_weak_to_strong(limit=15)
                logger.info("[%s] 弱转强 OK", trace_id)
            except Exception as e:
                logger.warning("[%s] 弱转强失败: %s", trace_id, e)
                data["weak_to_strong"] = {}

            # 5. 昨涨停反馈(高标被核?)
            try:
                data["limitup_feedback"] = client.auction_limitup_feedback(
                    focus="all", group_by="streak"
                )
                logger.info("[%s] 昨涨停反馈 OK", trace_id)
            except Exception as e:
                logger.warning("[%s] 昨涨停反馈失败: %s", trace_id, e)
                data["limitup_feedback"] = {}

        except Exception as e:
            logger.error("[%s] wudao 客户端初始化失败: %s", trace_id, e)
            data["client_ok"] = False
            data["client_error"] = str(e)

        logger.info(
            "[%s] 竞价采集完成: elapsed_ms=%s",
            trace_id,
            int((__import__("time").monotonic() - start_ts) * 1000),
        )

        return {
            "auction_data": data,
            "timestamp": datetime.now().isoformat(),
            "run_trace_id": trace_id,
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """构建竞价复盘 prompt(先情绪后主线再名单)。"""
        system_prompt = """你是一个A股短线竞价分析员。你的任务:解读当日集合竞价,回答短线选手开盘前最关心的三件事:
1) 今天资金的态度变了吗?(情绪修复/中性/退潮)
2) 今天主线在哪?(哪个题材有一致性,不只是总额最大)
3) 9:25 之后该盯哪 3-5 只票?(强龙头、反包候选、风险标的)

【判断标准】
- consistency ≥ 0.5 = 题材有一致性,真主线
- consistency ≤ 0.2 = 少数票抱团,不算主线(即使总额大)
- bidStrength > 50 = 强; > 100 = 很强(市值归一化,跨市值可比)
- bidAmountPercentile ≥ 90 = 自身历史强
- breakRate ≥ 40% 或 highBoardBreakCount ≥ 3 = 退潮
- breakRate ≤ 15% 且 hotOpenCount ≥ 30% = 修复

【输出格式】
1. 情绪定性:修复/中性/退潮(一句话+证据)
2. 主线定向:哪 2-3 个题材有真一致性(consistency 值),哪些是假强(总额大但一致性低)
3. 盯盘名单:3-5 只票,每只标注「为什么入选」
4. 弱转强/被核:昨炸板谁反包强,昨高标谁被核
5. 风险提示

【硬约束】
- 先结论后证据
- 不给买卖建议,不预测后续涨跌,只做竞价截面解释
- 数据缺字段就明说,不要编"""

        user_content = []
        ad = data.get("auction_data", {}) or {}
        user_content.append(f"## 日期:{data.get('timestamp', datetime.now().isoformat())[:10]} 竞价复盘\n")

        if not ad.get("client_ok"):
            user_content.append(f"⚠️ wudao 竞价数据不可用:{ad.get('client_error', '未知错误')}")
            user_content.append("(无法获取竞价全景/一致性/弱转强,竞价复盘降级为普通盘前展望)")
            return system_prompt, "\n".join(user_content)

        # 竞价全景
        snap = ad.get("opening_snapshot", {}) or {}
        if snap.get("text"):
            user_content.append(f"## 竞价全景\n{snap['text']}\n")
        elif snap:
            user_content.append(f"## 竞价全景\n{json_dumps(snap)[:500]}\n")

        # 题材一致性
        themes = ad.get("theme_strength", {}) or {}
        theme_list = themes.get("themes") or themes.get("data", {}).get("themes") or []
        if theme_list:
            user_content.append("## 题材竞价一致性(consistency)")
            for t in theme_list[:8]:
                user_content.append(
                    f"- {t.get('name')}: 总额{t.get('totalBidAmountText','-')} "
                    f"一致性{t.get('consistencyText','-')} "
                    f"高开{t.get('hotOpenCount','-')}家 涨停开{t.get('limitUpOpenCount','-')}家"
                )
            user_content.append("")

        # 竞价强度榜
        scan = ad.get("market_scan", {}) or {}
        scan_list = (
            scan.get("rows")
            or scan.get("stocks")
            or scan.get("data", {}).get("rows")
            or scan.get("data", {}).get("stocks")
            or scan.get("list")
            or []
        )
        if scan_list:
            user_content.append("## 竞价强度榜(bidStrength)")
            for s in scan_list[:10]:
                user_content.append(
                    f"- {s.get('name')}({s.get('code','')}): 强度{s.get('bidStrength','-')} "
                    f"涨幅{s.get('changeRate','-')}% "
                    f"竞价额{s.get('bidAmountText', str(s.get('bidAmount','-'))) if 'bidAmountText' in s else s.get('bidAmount','-')}"
                )
            user_content.append("")

        # 弱转强
        wts = ad.get("weak_to_strong", {}) or {}
        wts_list = (
            wts.get("rows")
            or wts.get("stocks")
            or wts.get("data", {}).get("rows")
            or wts.get("data", {}).get("stocks")
            or wts.get("list")
            or []
        )
        if wts_list:
            user_content.append("## 弱转强候选(昨炸板反包)")
            for s in wts_list[:8]:
                user_content.append(
                    f"- {s.get('name')}({s.get('code','')}): 强度{s.get('wtsScore','-')}"
                )
            user_content.append("")

        # 昨涨停反馈(被核?)
        fb = ad.get("limitup_feedback", {}) or {}
        if fb.get("text"):
            user_content.append(f"## 昨涨停反馈\n{fb['text'][:400]}\n")
        elif fb:
            summary = fb.get("summary") or fb.get("data", {}).get("summary") or {}
            if summary:
                user_content.append(f"## 昨涨停反馈\n{json_dumps(summary)[:400]}\n")

        return system_prompt, "\n".join(user_content)


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, default=str)
