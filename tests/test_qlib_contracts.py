import copy
import pytest
from pydantic import ValidationError

from src.modules.qlib_research.contracts import DatasetInput, ResearchConfig
from tests.qlib_fixture import panel


def test_valid_synthetic_panel_is_explicitly_not_market():
    result = DatasetInput.model_validate(panel())
    assert result.data_kind == "synthetic"
    assert len(result.calendar) == 360


@pytest.mark.parametrize("fault", ["duplicate", "ohlc", "nan", "code", "license", "membership", "calendar", "unknown_field"])
def test_invalid_data_is_rejected(fault):
    data = panel()
    if fault == "duplicate":
        data["bars"].append(copy.deepcopy(data["bars"][0]))
    elif fault == "ohlc":
        data["bars"][0]["high"] = 0.01
    elif fault == "nan":
        data["bars"][0]["factor"] = float("nan")
    elif fault == "code":
        data["bars"][0]["symbol"] = "AAPL"
    elif fault == "license":
        data["license_acknowledged"] = False
    elif fault == "membership":
        data["members"].append(copy.deepcopy(data["members"][0]))
    elif fault == "calendar":
        data["calendar"].reverse()
    else:
        data["model_pickle"] = "not allowed"
    with pytest.raises(ValidationError):
        DatasetInput.model_validate(data)


def test_config_cannot_execute_code_or_accept_nonfinite_costs():
    with pytest.raises(ValidationError):
        ResearchConfig.model_validate({"algorithm": "__import__"})
    with pytest.raises(ValidationError):
        ResearchConfig(close_cost=float("nan"))
    with pytest.raises(ValidationError):
        ResearchConfig(train_fraction=0.7, valid_fraction=0.25)
