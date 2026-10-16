"""Synthetic observations exercise display contracts, never market effectiveness."""

import uuid
from src.modules.qlib_research import service
from src.modules.qlib_research.contracts import DatasetInput
from src.platform.persistence.qlib_models import QlibRecord
from tests.qlib_fixture import panel
from tests.test_qlib_service import storage, model


def test_featured_view_preserves_complete_count_rank_and_real_observation_math(storage):
    data = panel()
    data["data_kind"] = "market"  # Controlled protocol label, not real market data.
    dataset = service.import_dataset(DatasetInput.model_validate(data), "controlled")
    candidate = model(storage)
    service.publish(candidate, 0, "Controlled context test", "research", "controlled")
    symbols = sorted({b["symbol"] for b in data["bars"]})
    predictions = [
        {
            "symbol": s,
            "market": "CN",
            "rank": i + 1,
            "raw_score": 10 - i,
            "tradable": True,
            "reference_close": 10,
        }
        for i, s in enumerate(symbols)
    ]
    ident = uuid.uuid4().hex
    with storage() as db:
        db.add(
            QlibRecord(
                id=ident,
                kind="selection",
                parent_id=candidate,
                name="controlled",
                payload={
                    "model_id": candidate,
                    "dataset_id": dataset["id"],
                    "asof_date": data["calendar"][-1],
                    "market": "CN",
                    "data_kind": "market",
                    "predictions": predictions,
                    "config": {"topk": 2},
                },
            )
        )
        db.commit()
    all_rows = service.opportunities(limit=50, view="all")
    featured = service.opportunities(limit=50, view="featured")
    assert featured["prediction_count"] == len(symbols) == all_rows["prediction_count"]
    assert featured["total"] == 2 and [r["rank"] for r in featured["items"]] == [1, 2]
    bars = sorted(
        (b for b in data["bars"] if b["symbol"] == symbols[0]), key=lambda b: b["date"]
    )
    context = featured["items"][0]["observed_context"]
    assert (
        context["return_20_observations"]
        == bars[-1]["close"]
        * bars[-1]["factor"]
        / (bars[-21]["close"] * bars[-21]["factor"])
        - 1
    )
    assert "not_model_contributions" in context["basis"]
    assert (
        "raw_score" in featured["items"][0]
        and featured["items"][0]["raw_score"] == predictions[0]["raw_score"]
    )
    service.dataset_path(dataset["id"]).write_text("{}")
    assert service.opportunities(limit=50)["items"][0]["observed_context"] is None
