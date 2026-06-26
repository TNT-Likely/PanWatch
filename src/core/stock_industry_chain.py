"""自选股产业链自动分类（老马 LMD 框架：沿 AI 物理连接顺序的行情轮动）。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from src.collectors.concept_collector import can_auto_fetch_concept_tags, fetch_cn_industry
from src.core.notifier import get_global_proxy
from src.core.stock_concept_tags import _as_str_list
from src.core.timezone import utc_now
from src.web.database import SessionLocal
from src.web.models import Stock

logger = logging.getLogger(__name__)

_CHAIN_CONFIG_PATH = (
    Path(__file__).parent.parent.parent / "config" / "lmd_industry_chains.yaml"
)
_MIN_SCORE = 10
_SYMBOL_SCORE = 100

# 旧版上中下游分层 → 新版 AI 行情轮动环节（与前端 CHAIN_LAYER_LEGACY_MAP 对齐）
_LEGACY_LAYER_MAP: dict[str, str] = {
    "upstream": "gpu",
    "midstream": "cloud_llm",
    "downstream": "physical_ai",
    "foundation": "semi_pcb_equip",
    "middleware": "cloud_llm",
    "integration": "idc",
    "application": "physical_ai",
}


@lru_cache(maxsize=1)
def load_chain_taxonomy() -> dict[str, Any]:
    if not _CHAIN_CONFIG_PATH.is_file():
        logger.warning("产业链配置文件不存在: %s", _CHAIN_CONFIG_PATH)
        return {}
    with _CHAIN_CONFIG_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("chains") or {}


def clear_chain_taxonomy_cache() -> None:
    load_chain_taxonomy.cache_clear()


def list_chain_layer_options(
    *,
    taxonomy: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """返回可选轮动环节列表，供前端下拉与 AI 提示词使用。"""
    chains = taxonomy if taxonomy is not None else load_chain_taxonomy()
    options: list[dict[str, str]] = []
    for sector_key, sector in chains.items():
        display_name = str(sector.get("display_name") or sector_key)
        for layer_key, layer in (sector.get("layers") or {}).items():
            options.append(
                {
                    "sector": sector_key,
                    "layer": layer_key,
                    "label": str(layer.get("label") or layer_key),
                    "group": "compute"
                    if resolve_chain_layer_key(layer_key)
                    in {
                        "gpu",
                        "cpo",
                        "hbm",
                        "pcb",
                        "liquid_cooling",
                        "semi_pcb_equip",
                        "server",
                        "idc",
                        "power",
                    }
                    else "mainline",
                    "sector_label": display_name,
                    "description": str(layer.get("description") or ""),
                }
            )
    options.append(
        {
            "sector": "OTHER",
            "layer": "other",
            "label": "其他",
            "group": "other",
            "sector_label": "其他",
            "description": "不属于 AI 行情轮动框架",
        }
    )
    return options


def _valid_layer_keys(*, taxonomy: dict[str, Any] | None = None) -> set[str]:
    chains = taxonomy if taxonomy is not None else load_chain_taxonomy()
    keys: set[str] = {"other"}
    for sector in chains.values():
        keys.update((sector.get("layers") or {}).keys())
    keys.update(_LEGACY_LAYER_MAP.keys())
    return keys


def normalize_manual_chain_layer(layer: str | None) -> str:
    raw = (layer or "").strip()
    if not raw:
        return ""
    return resolve_chain_layer_key(raw)


def build_chain_from_layer(
    layer_key: str,
    *,
    sector_key: str = "AI",
    match_source: str = "",
    matched: list[str] | None = None,
    score: int = 0,
    taxonomy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = resolve_chain_layer_key(layer_key)
    if resolved == "other":
        return _build_other_category()
    chains = taxonomy if taxonomy is not None else load_chain_taxonomy()
    sector = chains.get(sector_key) or next(iter(chains.values()), {})
    display_name = str(sector.get("display_name") or sector_key)
    layer = (sector.get("layers") or {}).get(resolved) or {}
    return {
        "sector": sector_key,
        "sector_label": display_name,
        "layer": resolved,
        "layer_label": str(layer.get("label") or resolved),
        "description": str(layer.get("description") or ""),
        "score": score,
        "matched": (matched or [])[:8],
        "match_source": match_source,
        "display": str(layer.get("label") or resolved),
    }


def has_manual_industry_chain(stock: Stock) -> bool:
    manual = getattr(stock, "industry_chain_manual", None)
    if not isinstance(manual, dict):
        return False
    return bool(str(manual.get("layer") or "").strip())


def resolve_industry_chain(
    stock: Stock,
    *,
    taxonomy: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """手动覆盖优先，其次自动分类（含 AI）。"""
    manual = getattr(stock, "industry_chain_manual", None)
    if isinstance(manual, dict):
        manual_layer = normalize_manual_chain_layer(str(manual.get("layer") or ""))
        if str(manual.get("layer") or "").strip():
            if manual_layer == "other":
                result = _build_other_category()
            else:
                sector_key = str(manual.get("sector") or "AI").strip() or "AI"
                result = build_chain_from_layer(
                    manual_layer,
                    sector_key=sector_key,
                    match_source="manual",
                    score=100,
                )
            result["source"] = "manual"
            return result

    auto = stock.industry_chain_auto
    if isinstance(auto, dict) and auto.get("sector") and auto.get("layer"):
        result = dict(auto)
        result["source"] = "manual" if auto.get("match_source") == "manual" else "auto"
        return result
    return None


def needs_industry_chain_refresh(stock: Stock) -> bool:
    if has_manual_industry_chain(stock):
        return False
    existing = stock.industry_chain_auto
    if not isinstance(existing, dict) or not existing.get("sector") or not existing.get("layer"):
        return True
    if existing.get("layer") == "other" and existing.get("match_source") != "ai":
        return True
    return False


def _normalize_symbol(symbol: str, market: str) -> str:
    sym = (symbol or "").strip().upper()
    mkt = (market or "CN").strip().upper()
    if mkt == "CN":
        return sym.zfill(6) if sym.isdigit() else sym
    if mkt == "HK":
        return sym.lstrip("0") or "0"
    return sym


def _text_blob(stock: Stock, *, industry: str = "") -> str:
    parts = [
        str(getattr(stock, "name", "") or ""),
        industry,
        *(_as_str_list(getattr(stock, "concept_tags_auto", None))),
        *(_as_str_list(getattr(stock, "concept_tags_manual", None))),
    ]
    return " ".join(p for p in parts if p)


def _keyword_hit(keyword: str, blob: str) -> bool:
    kw = (keyword or "").strip()
    if not kw:
        return False
    if kw in blob:
        return True
    # 英文 ticker / 缩写按词边界匹配
    if re.fullmatch(r"[A-Za-z0-9]+", kw):
        return bool(re.search(rf"\b{re.escape(kw)}\b", blob, flags=re.IGNORECASE))
    return False


def resolve_chain_layer_key(layer_key: str) -> str:
    key = (layer_key or "").strip()
    if not key:
        return ""
    return _LEGACY_LAYER_MAP.get(key, key)


def chain_layer_label(
    layer_key: str,
    *,
    taxonomy: dict[str, Any] | None = None,
) -> str:
    """返回轮动环节展示名（GPU / CPO / 物理AI 等），不含「人工智能·」前缀。"""
    resolved = resolve_chain_layer_key(layer_key)
    if resolved == "other":
        return "其他"
    chains = taxonomy if taxonomy is not None else load_chain_taxonomy()
    for sector in chains.values():
        layer = (sector.get("layers") or {}).get(resolved)
        if layer:
            return str(layer.get("label") or resolved)
    return resolved


def normalize_chain_display(
    raw: dict[str, Any],
    *,
    taxonomy: dict[str, Any] | None = None,
) -> str:
    """统一产业链标签展示：算力细分 / A股主线环节名，兼容 DB 中旧版分类。"""
    layer = str(raw.get("layer") or "").strip()
    if not layer or layer == "other" or str(raw.get("sector") or "").upper() == "OTHER":
        return "其他"
    return chain_layer_label(layer, taxonomy=taxonomy)


def _sector_gate_passed(sector: dict[str, Any], blob: str) -> bool:
    """标的须先命中赛道关键词，才允许按层级关键词归类。"""
    for kw in sector.get("sector_keywords") or []:
        if _keyword_hit(str(kw), blob):
            return True
    return False


def _build_other_category(*, industry: str = "") -> dict[str, Any]:
    """未命中任何产业链赛道时归入「其他」。"""
    matched: list[str] = []
    if industry:
        matched.append(industry)
    return {
        "sector": "OTHER",
        "sector_label": "其他",
        "layer": "other",
        "layer_label": "其他",
        "description": "不属于人工智能产业链的标的",
        "score": 1,
        "matched": matched[:8],
        "match_source": "fallback",
        "display": "其他",
    }


def classify_stock_by_rules(
    stock: Stock,
    *,
    industry: str = "",
    taxonomy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """规则归类：白名单代码 + 关键词匹配。"""
    chains = taxonomy if taxonomy is not None else load_chain_taxonomy()
    if not chains:
        return None

    market = (getattr(stock, "market", "CN") or "CN").strip().upper()
    symbol = _normalize_symbol(getattr(stock, "symbol", "") or "", market)
    blob = _text_blob(stock, industry=industry)

    best: dict[str, Any] | None = None
    best_score = 0

    for sector_key, sector in chains.items():
        display_name = str(sector.get("display_name") or sector_key)
        layers = sector.get("layers") or {}
        ai_related = _sector_gate_passed(sector, blob)

        for layer_key, layer in layers.items():
            score = 0
            matched: list[str] = []
            match_source = ""

            symbols_map = layer.get("symbols") or {}
            market_symbols = symbols_map.get(market) or []
            norm_symbols = {
                _normalize_symbol(str(s), market) for s in market_symbols if str(s).strip()
            }
            if symbol and symbol in norm_symbols:
                score = _SYMBOL_SCORE
                matched.append(f"symbol:{symbol}")
                match_source = "symbol"

            if match_source != "symbol":
                if not ai_related:
                    continue
                for kw in layer.get("keywords") or []:
                    if _keyword_hit(str(kw), blob):
                        score += 10
                        matched.append(str(kw))

            if score > best_score:
                best_score = score
                best = {
                    "sector": sector_key,
                    "sector_label": display_name,
                    "layer": layer_key,
                    "layer_label": str(layer.get("label") or layer_key),
                    "description": str(layer.get("description") or ""),
                    "score": score,
                    "matched": matched[:8],
                    "match_source": match_source or ("keyword" if score >= _MIN_SCORE else ""),
                    "display": str(layer.get("label") or layer_key),
                }

    if not best or best_score < _MIN_SCORE:
        return _build_other_category(industry=industry)
    return best


def _parse_ai_layer_response(content: str) -> str:
    raw = (content or "").strip()
    if not raw:
        return ""
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            raw = "\n".join(lines[1:-1]).strip()
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return normalize_manual_chain_layer(str(obj.get("layer") or ""))
    except json.JSONDecodeError:
        pass
    match = re.search(r'"layer"\s*:\s*"([^"]+)"', raw)
    if match:
        return normalize_manual_chain_layer(match.group(1))
    return ""


def _build_ai_prompt(
    stock: Stock,
    *,
    industry: str = "",
    taxonomy: dict[str, Any] | None = None,
) -> tuple[str, str]:
    options = list_chain_layer_options(taxonomy=taxonomy)
    layer_lines = [
        f"- {opt['layer']}: {opt['label']} — {opt['description']}"
        for opt in options
        if opt["layer"] != "other"
    ]
    auto_tags = _as_str_list(getattr(stock, "concept_tags_auto", None))
    manual_tags = _as_str_list(getattr(stock, "concept_tags_manual", None))
    system_prompt = (
        "你是 A 股 AI 行情轮动分类助手。根据股票信息，将其归入最合适的轮动环节。\n"
        "框架：沿 AI 物理连接顺序 GPU→CPO→HBM→PCB→液冷→材料设备→服务器→IDC→电力，"
        "之后是云&大模型、软件应用（A股通常跳过）、物理AI。\n"
        "只输出 JSON：{\"layer\":\"环节key\",\"reason\":\"一句话理由\"}。\n"
        "若与 AI 产业链完全无关才用 other。"
    )
    user_content = "\n".join(
        [
            f"股票：{getattr(stock, 'name', '')} ({getattr(stock, 'symbol', '')})",
            f"市场：{getattr(stock, 'market', 'CN')}",
            f"行业：{industry or '未知'}",
            f"概念标签：{', '.join(manual_tags + auto_tags) or '无'}",
            "可选环节：",
            *layer_lines,
            "- other: 其他 — 与 AI 行情轮动无关",
        ]
    )
    return system_prompt, user_content


async def classify_stock_by_ai(
    stock: Stock,
    *,
    industry: str = "",
    taxonomy: dict[str, Any] | None = None,
    db: Session | None = None,
) -> dict[str, Any] | None:
    """调用 LLM 归类；失败时返回 None。"""
    chains = taxonomy if taxonomy is not None else load_chain_taxonomy()
    if not chains:
        return None
    try:
        if db is not None:
            from src.web.api.chat import _get_ai_client

            client = _get_ai_client(db)
        else:
            from src.config import Settings
            from src.core.ai_client import AIClient

            settings = Settings()
            if not (settings.ai_api_key or "").strip():
                return None
            client = AIClient(
                base_url=settings.ai_base_url,
                api_key=settings.ai_api_key,
                model=settings.ai_model,
            )
    except Exception:
        logger.debug("AI 客户端初始化失败，跳过产业链 AI 分类", exc_info=True)
        return None

    system_prompt, user_content = _build_ai_prompt(stock, industry=industry, taxonomy=chains)
    try:
        content = await client.chat(system_prompt, user_content, temperature=0.2)
    except Exception:
        logger.warning("产业链 AI 分类调用失败: %s", getattr(stock, "symbol", ""), exc_info=True)
        return None

    layer_key = _parse_ai_layer_response(content)
    valid_keys = _valid_layer_keys(taxonomy=chains)
    if not layer_key or layer_key not in valid_keys:
        logger.info(
            "产业链 AI 分类无效结果 %s: %s",
            getattr(stock, "symbol", ""),
            (content or "")[:200],
        )
        return None

    if layer_key == "other":
        result = _build_other_category(industry=industry)
    else:
        result = build_chain_from_layer(
            layer_key,
            match_source="ai",
            matched=[f"ai:{layer_key}"],
            score=50,
            taxonomy=chains,
        )
    result["ai_reason"] = content[:500]
    return result


def classify_stock(
    stock: Stock,
    *,
    industry: str = "",
    taxonomy: dict[str, Any] | None = None,
    db: Session | None = None,
    use_ai: bool = True,
) -> dict[str, Any]:
    """规则优先；仍为「其他」且允许时走 AI 分类。"""
    chains = taxonomy if taxonomy is not None else load_chain_taxonomy()
    if not chains:
        return _build_other_category(industry=industry)

    result = classify_stock_by_rules(stock, industry=industry, taxonomy=chains)
    if result.get("layer") != "other" or not use_ai:
        return result

    try:
        ai_result = asyncio.run(
            classify_stock_by_ai(stock, industry=industry, taxonomy=chains, db=db)
        )
    except Exception:
        logger.warning("产业链 AI 分类异常: %s", getattr(stock, "symbol", ""), exc_info=True)
        ai_result = None
    if ai_result and ai_result.get("layer") != "other":
        return ai_result
    if ai_result:
        return ai_result
    fallback = _build_other_category(industry=industry)
    fallback["match_source"] = "fallback"
    return fallback


def refresh_stock_industry_chain(db: Session, stock: Stock) -> dict[str, Any] | None:
    industry = ""
    if can_auto_fetch_concept_tags(stock.market):
        proxy = (get_global_proxy() or "").strip() or None
        try:
            industry = fetch_cn_industry(stock.symbol, proxy=proxy) or ""
        except Exception:
            logger.debug("拉取行业失败 %s", stock.symbol, exc_info=True)

    result = classify_stock(stock, industry=industry, db=db, use_ai=True)
    stock.industry_chain_auto = result or {}
    stock.industry_chain_updated_at = utc_now()
    db.commit()
    db.refresh(stock)
    return resolve_industry_chain(stock) or result


def set_manual_industry_chain(
    db: Session,
    stock: Stock,
    *,
    layer: str | None,
    sector: str = "AI",
) -> dict[str, Any] | None:
    """设置或清除手动产业链分类。"""
    if layer is not None and str(layer).strip():
        normalized = normalize_manual_chain_layer(layer)
        if not normalized:
            raise ValueError("无效的产业链环节")
        valid_keys = _valid_layer_keys()
        if normalized not in valid_keys:
            raise ValueError(f"不支持的产业链环节: {layer}")
        stock.industry_chain_manual = {
            "sector": sector if normalized != "other" else "OTHER",
            "layer": normalized,
        }
    else:
        stock.industry_chain_manual = {}
    db.commit()
    db.refresh(stock)
    return resolve_industry_chain(stock)


def refresh_stock_industry_chain_by_id(stock_id: int) -> bool:
    db = SessionLocal()
    try:
        stock = db.query(Stock).filter(Stock.id == stock_id).first()
        if not stock:
            return False
        refresh_stock_industry_chain(db, stock)
        return True
    except Exception:
        logger.exception("刷新股票 %s 产业链分类失败", stock_id)
        return False
    finally:
        db.close()


def refresh_industry_chains(limit: int = 100) -> int:
    """重新归类自选股产业链；每次最多处理 limit 只。"""
    db = SessionLocal()
    refreshed = 0
    try:
        rows = db.query(Stock).order_by(Stock.id.asc()).all()
        for stock in rows:
            if refreshed >= limit:
                break
            try:
                refresh_stock_industry_chain(db, stock)
                refreshed += 1
            except Exception:
                logger.exception("批量刷新产业链分类失败: %s", stock.symbol)
        return refreshed
    finally:
        db.close()


def refresh_missing_industry_chains(limit: int = 50) -> int:
    """刷新未完成分类、或仍停留在规则「其他」的自选股。"""
    db = SessionLocal()
    refreshed = 0
    try:
        rows = db.query(Stock).order_by(Stock.id.asc()).all()
        for stock in rows:
            if refreshed >= limit:
                break
            if not needs_industry_chain_refresh(stock):
                continue
            try:
                refresh_stock_industry_chain(db, stock)
                refreshed += 1
            except Exception:
                logger.exception("批量刷新产业链分类失败: %s", stock.symbol)
        return refreshed
    finally:
        db.close()


def schedule_refresh_stock_industry_chain(stock_id: int) -> None:
    t = threading.Thread(
        target=refresh_stock_industry_chain_by_id,
        args=(stock_id,),
        name=f"industry-chain-{stock_id}",
        daemon=True,
    )
    t.start()


def schedule_refresh_missing_industry_chains(limit: int = 50) -> None:
    t = threading.Thread(
        target=refresh_missing_industry_chains,
        args=(limit,),
        name="industry-chain-batch",
        daemon=True,
    )
    t.start()
