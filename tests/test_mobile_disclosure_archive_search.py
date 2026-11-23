from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.modules.mobile.disclosures import create_disclosures_router
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import AgentContextRun, Stock


def test_archive_identifies_only_runs_that_recorded_each_verified_disclosure(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'archive.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    def db_session():
        with sessions() as db:
            yield db

    app = FastAPI()
    app.include_router(create_disclosures_router(lambda: None))
    app.dependency_overrides[get_db] = db_session
    article = "AN202611190001"
    recorded_on = "2026-11-20"
    event = {
        "time": "2026-11-19T10:21:00", "source": "eastmoney", "external_id": article, "event_type": "earnings",
        "url": f"https://data.eastmoney.com/notices/detail/600999/{article}.html", "title": "private legacy body",
    }

    def archive(agent, on, events):
        return AgentContextRun(
            agent_name=agent, stock_symbol="*", analysis_date=on,
            context_payload={"symbols": {"CN:600999": {"events": events}}},
        )

    try:
        with sessions.begin() as db:
            db.add_all([
                Stock(symbol="600999", market="CN", name="Example issuer"),
                archive("premarket_outlook", recorded_on, [event]),
                archive("daily_report", recorded_on, [event, event]),
                archive("intraday_monitor", recorded_on, [{**event, "source": "unverified"}]),
                archive("intraday_monitor", "2026-11-21", [event]),
            ])
        with TestClient(app) as client:
            def items(**params):
                response = client.get("/facts/disclosures/archive", params={"on": recorded_on, **params})
                assert response.status_code == 200
                assert "private legacy body" not in response.text
                return response.json()["data"]["items"]

            default = items()
            assert len(default) == 1 and "archive_agents" not in default[0]
            enriched = items(include_agents=True, market="CN", symbol="600999")
            assert enriched == [{**default[0], "archive_agents": ["daily_report", "premarket_outlook"]}]
            assert enriched[0]["date"] == "2026-11-19"
            assert items(agent="premarket_outlook", include_agents=True) == [
                {**default[0], "archive_agents": ["premarket_outlook"]},
            ]
            assert items(agent="intraday_monitor", include_agents=True) == []
            assert items(on="2026-11-21", include_agents=True)[0]["archive_agents"] == ["intraday_monitor"]
            assert items(market="US", symbol="600999", include_agents=True) == []
            assert items(symbol="000001", include_agents=True) == []
            with sessions.begin() as db:
                db.add_all(archive("intraday_monitor", recorded_on, []) for _ in range(31))
            assert items(include_agents=True, symbol="600999") == enriched
    finally:
        engine.dispose()
