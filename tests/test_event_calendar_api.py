"""事件日历 API/service 与导入脚本单测(内存库,离线可跑)。"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import EventCalendarItem, SectorPrediction


@pytest.fixture
def db():
    import src.platform.persistence.models  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()
    engine.dispose()


# ────────────────────────── service 层 ──────────────────────────


def test_normalize_item_valid_and_invalid(db):
    """归一:合法条目全字段归一;非法枚举/缺 name/坏日期抛 ValueError。"""
    from src.modules.market.event_calendar_service import normalize_item

    item = normalize_item(
        {
            "event_date": "2026-10-01",
            "level": "HIGH",
            "name": "示例事件",
            "direction": "Bullish",
            "impact_boards": None,
        }
    )
    assert item["level"] == "high"
    assert item["direction"] == "bullish"
    assert item["impact_boards"] == []
    assert item["expected"] == ""

    with pytest.raises(ValueError):
        normalize_item({"event_date": "2026-10-01", "name": " "})
    with pytest.raises(ValueError):
        normalize_item({"event_date": "10-01", "name": "坏日期"})
    with pytest.raises(ValueError):
        normalize_item({"event_date": "2026-10-01", "name": "x", "level": "urgent"})
    with pytest.raises(ValueError):
        normalize_item({"event_date": "2026-10-01", "name": "x", "direction": "up"})


def test_upsert_is_idempotent_by_date_and_name(db):
    """UPSERT 键为 (event_date, name):重复导入更新而非追加。"""
    from src.modules.market.event_calendar_service import upsert_items

    raw = {"event_date": "2026-10-01", "name": "示例事件", "level": "high"}
    first = upsert_items(db, [raw])
    assert first == {"created": 1, "updated": 0}
    second = upsert_items(db, [{**raw, "actual": "公布值"}])
    assert second == {"created": 0, "updated": 1}
    assert db.query(EventCalendarItem).count() == 1
    row = db.query(EventCalendarItem).first()
    assert row.actual == "公布值"
    assert row.level == "high"


def test_update_and_delete_item(db):
    from src.modules.market import event_calendar_service as svc

    svc.upsert_items(db, [{"event_date": "2026-10-01", "name": "示例事件"}])
    row = db.query(EventCalendarItem).first()

    updated = svc.update_item(db, row.id, {"actual": "7.1%", "level": "low"})
    assert updated.level == "low"
    assert updated.actual == "7.1%"

    with pytest.raises(LookupError):
        svc.update_item(db, 99999, {"actual": "x"})
    with pytest.raises(ValueError):
        svc.update_item(db, row.id, {"level": "urgent"})

    svc.delete_item(db, row.id)
    assert db.query(EventCalendarItem).count() == 0
    with pytest.raises(LookupError):
        svc.delete_item(db, row.id)


def test_list_by_window_filters(db):
    """日期窗过滤 + 默认未来 30 天 + level 过滤。"""
    from src.modules.market.event_calendar_service import list_by_window, upsert_items

    upsert_items(
        db,
        [
            {"event_date": "2026-09-01", "name": "窗外过去"},
            {"event_date": "2026-09-25", "name": "窗内高", "level": "high"},
            {"event_date": "2026-10-10", "name": "窗内低", "level": "low"},
            {"event_date": "2027-06-01", "name": "窗外远期"},
        ],
    )
    rows = list_by_window(db, start="2026-09-20", end="2026-10-15")
    assert [r.name for r in rows] == ["窗内高", "窗内低"]
    rows_level = list_by_window(db, start="2026-09-20", end="2026-10-15", level="low")
    assert [r.name for r in rows_level] == ["窗内低"]


# ────────────────────────── HTTP 层(直调路由函数) ──────────────────────────


def test_api_routes_crud_flow(db):
    """POST 单条/批量 → GET 窗口 → PUT → DELETE 全流程。"""
    from src.modules.market.api import event_calendar as api

    created = api.create_event(
        api.EventCalendarItemIn(
            event_date="2026-10-01", name="示例事件", level="high"
        ),
        db=db,
    )
    assert created["created"] == 1

    batch = api.create_events_batch(
        api.EventCalendarBatchIn(
            items=[
                api.EventCalendarItemIn(event_date="2026-10-02", name="批量甲"),
                api.EventCalendarItemIn(event_date="2026-10-03", name="批量乙"),
            ]
        ),
        db=db,
    )
    assert batch == {"created": 2, "updated": 0}

    rows = api.list_events(start="2026-10-01", end="2026-10-31", db=db)
    assert len(rows) == 3
    assert rows[0]["name"] == "示例事件"
    assert rows[0]["event_date"] == "2026-10-01"

    target = next(r for r in rows if r["name"] == "批量甲")
    updated = api.update_event(
        target["id"], api.EventCalendarUpdate(actual="已公布", direction="bullish"), db=db
    )
    assert updated["actual"] == "已公布"
    assert updated["direction"] == "bullish"

    deleted = api.delete_event(target["id"], db=db)
    assert deleted == {"deleted": target["id"]}
    with pytest.raises(HTTPException) as exc:
        api.delete_event(target["id"], db=db)
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc2:
        api.create_event(
            api.EventCalendarItemIn(event_date="not-a-date", name="坏数据"), db=db
        )
    assert exc2.value.status_code == 400


# ────────────────────────── 导入脚本 ──────────────────────────


def _write_json(tmp_path, payload, name="cal.json"):
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_import_script_load_items_validation(tmp_path):
    """脚本文件读取:非数组/坏 JSON/含非对象元素 → SystemExit。"""
    import scripts.import_event_calendar as imp

    p = _write_json(tmp_path, [{"event_date": "2026-10-01", "name": "甲"}])
    items = imp.load_items(p)
    assert len(items) == 1

    bad_top = _write_json(tmp_path, {"not": "array"}, name="top.json")
    with pytest.raises(SystemExit):
        imp.load_items(bad_top)

    bad_json = tmp_path / "broken.json"
    bad_json.write_text("{oops", encoding="utf-8")
    with pytest.raises(SystemExit):
        imp.load_items(bad_json)

    bad_elem = _write_json(tmp_path, ["str-item"], name="elem.json")
    with pytest.raises(SystemExit):
        imp.load_items(bad_elem)

    missing = tmp_path / "nope.json"
    with pytest.raises(SystemExit):
        imp.load_items(missing)


def test_import_script_main_upserts_into_db(db, monkeypatch, tmp_path):
    """main():替身 SessionLocal 指向内存库;UPSERT 幂等;坏条目回滚返回 2。"""
    import scripts.import_event_calendar as imp

    monkeypatch.setattr(imp, "SessionLocal", lambda: db)
    monkeypatch.setattr(imp, "init_db", lambda: None)

    payload = [
        {"event_date": "2026-10-01", "name": "甲", "level": "high"},
        {"event_date": "2026-10-02", "name": "乙", "impact_boards": ["BK0475"]},
    ]
    p = _write_json(tmp_path, payload)
    assert imp.main(["--file", str(p)]) == 0
    assert db.query(EventCalendarItem).count() == 2

    # 重复执行幂等:同日同名更新
    payload[0]["actual"] = "已落地"
    p2 = _write_json(tmp_path, payload, name="cal2.json")
    assert imp.main(["--file", str(p2)]) == 0
    assert db.query(EventCalendarItem).count() == 2
    row = db.query(EventCalendarItem).filter(EventCalendarItem.name == "甲").first()
    assert row.actual == "已落地"

    # 坏条目:整体回滚退出码 2
    p3 = _write_json(
        tmp_path,
        [{"event_date": "2026-10-05", "name": "丙"}, {"event_date": "bad", "name": "丁"}],
        name="cal3.json",
    )
    assert imp.main(["--file", str(p3)]) == 2
    assert db.query(EventCalendarItem).filter(EventCalendarItem.name == "丙").count() == 0


# ────────────────────────── sectors 预测接口 ──────────────────────────


def test_sectors_predictions_api(db):
    """GET predictions:默认最新快照日;显式 date 过滤;空库返回空结构。"""
    from src.modules.market.api import sectors as sectors_api

    empty = sectors_api.list_sector_predictions(db=db)
    assert empty == {"requested_date": "", "predictions": []}

    db.add_all(
        [
            SectorPrediction(
                snapshot_date="2026-09-22",
                board_code="BK1",
                board_name="甲",
                momentum_score=1.0,
            ),
            SectorPrediction(
                snapshot_date="2026-09-23",
                board_code="BK2",
                board_name="乙",
                momentum_score=3.0,
            ),
            SectorPrediction(
                snapshot_date="2026-09-23",
                board_code="BK3",
                board_name="丙",
                momentum_score=9.0,
            ),
        ]
    )
    db.commit()

    latest = sectors_api.list_sector_predictions(db=db)
    assert latest["requested_date"] == "2026-09-23"
    # 按 momentum_score 降序
    assert [p["board_code"] for p in latest["predictions"]] == ["BK3", "BK2"]

    explicit = sectors_api.list_sector_predictions(date="2026-09-22", db=db)
    assert explicit["requested_date"] == "2026-09-22"
    assert len(explicit["predictions"]) == 1

    with pytest.raises(HTTPException) as exc:
        sectors_api.list_sector_predictions(date="bad", db=db)
    assert exc.value.status_code == 400


def test_routers_mounted_protected():
    """应用装配:事件日历与行业路由已挂载且保持 protected。

    兼容 FastAPI 的两种路由结构:直接展开的 APIRoute 与延迟 include 的
    _IncludedRouter(include_context.prefix + original_router.routes)。
    """
    from src.bootstrap.application import app

    mounted: dict[str, list] = {}  # prefix -> [dependency, ...]

    def _collect(routes) -> None:
        for route in routes:
            type_name = type(route).__name__
            if type_name == "_IncludedRouter":
                ctx = getattr(route, "include_context", None)
                prefix = str(getattr(ctx, "prefix", "") or "")
                if prefix:
                    mounted.setdefault(prefix, list(getattr(ctx, "dependencies", []) or []))
                _collect(getattr(route, "original_router", None).routes)
                continue
            path = getattr(route, "path", "")
            if path.startswith(("/api/event-calendar", "/api/sectors")):
                # 顶层直挂(无 prefix 层)的场景:按完整路径记录
                mounted.setdefault(path, list(getattr(route, "dependencies", []) or []))

    _collect(app.routes)

    def _deps_of(prefix: str, sub_path: str) -> list:
        deps = mounted.get(prefix, [])
        if not deps:  # 顶层直挂形态:/api/event-calendar/batch 等完整路径
            deps = mounted.get((prefix + sub_path) or prefix, [])
        return deps

    expected = [
        ("/api/event-calendar", ""),
        ("/api/event-calendar", "/batch"),
        ("/api/event-calendar", "/{item_id}"),
        ("/api/sectors", "/predictions"),
    ]
    for prefix, sub in expected:
        deps = _deps_of(prefix, sub)
        assert deps, f"缺少挂载路由: {prefix}{sub}"
        assert any("get_current_user" in str(d) for d in deps), f"{prefix}{sub} 未保持 protected"
