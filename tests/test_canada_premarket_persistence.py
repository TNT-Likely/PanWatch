"""Premarket completion retains the collected currency snapshot in real storage.

The model response is scripted; no live model or notification is used.
"""
import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.automation.base import AgentContext, PortfolioInfo
from src.modules.automation.premarket_outlook import PremarketOutlookAgent
from src.modules.research import analysis_history, context_store
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.database import Base
from src.platform.persistence.models import AgentContextRun, AnalysisHistory
from src.platform.runtime.config import AppConfig, Settings, StockConfig


@pytest.mark.parametrize("market", [MarketCode.CN, MarketCode.TSX, MarketCode.TSXV])
@pytest.mark.parametrize("incomplete", [False, True])
def test_successful_premarket_analysis_persists_collected_valuation(monkeypatch, market, incomplete):
    class ScriptedAI:
        async def chat(self, system_prompt, user_prompt):
            return "Controlled premarket narrative without trade recommendations."

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(analysis_history, "SessionLocal", sessions)
    monkeypatch.setattr(context_store, "SessionLocal", sessions)
    symbol = "600519" if market == MarketCode.CN else "T"
    context = AgentContext(
        ScriptedAI(), None,
        AppConfig(Settings(_env_file=None), [StockConfig(symbol, "Review fixture", market)]),
        PortfolioInfo(), suppress_notify=True,
    )
    valuation = None if market == MarketCode.CN else {
        "accounts": [], "positions": {}, "cash_currency": "CNY",
        "total_cost_cny": None if incomplete else 5000.0,
        "valuation_incomplete": incomplete,
        "fx": {"CAD": {
            "rate": None if incomplete else 5.0,
            "source": "Frankfurter/ECB",
            "source_date": None if incomplete else "2026-10-09",
        }},
    }
    data = {
        "signal_packs": {}, "symbol_contexts": {},
        "portfolio_valuation": valuation, "timestamp": "2026-10-09T12:00:00",
    }
    try:
        result = asyncio.run(PremarketOutlookAgent().analyze(context, data))
        assert result.content == "Controlled premarket narrative without trade recommendations."
        with sessions() as db:
            context_run = db.query(AgentContextRun).one()
            history = db.query(AnalysisHistory).one()
            assert context_run.context_payload["portfolio_valuation"] == valuation
            assert history.raw_data["portfolio_valuation"] == valuation
            assert history.content == result.content
    finally:
        engine.dispose()
