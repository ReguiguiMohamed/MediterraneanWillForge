import pandas as pd
import pytest

import data.ingestion.gold.anomaly as anomaly
from data.ingestion.gold.anomaly import _feature_frame


def test_feature_frame_excludes_waqi_index_values():
    silver = pd.DataFrame(
        {
            "source": ["openmeteo", "openaq", "waqi", "openmeteo"],
            "pm2_5": [10.0, None, 80.0, None],
            "ozone": [20.0, 30.0, 2.0, None],
            "nitrogen_dioxide": [5.0, None, 40.0, None],
        }
    )

    features = _feature_frame(silver)

    assert features["source"].tolist() == ["openmeteo", "openaq"]


def test_run_fails_when_silver_is_unreadable(monkeypatch):
    def unreadable_table(*args, **kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(anomaly, "delta_storage_options", lambda: {})
    monkeypatch.setattr(anomaly, "DeltaTable", unreadable_table)

    with pytest.raises(RuntimeError, match="Cannot read Silver layer"):
        anomaly.run()


def test_run_fails_when_silver_is_empty(monkeypatch):
    class EmptyTable:
        def __init__(self, *args, **kwargs):
            pass

        def to_pandas(self):
            return pd.DataFrame()

    monkeypatch.setattr(anomaly, "delta_storage_options", lambda: {})
    monkeypatch.setattr(anomaly, "DeltaTable", EmptyTable)

    with pytest.raises(RuntimeError, match="Silver layer is empty"):
        anomaly.run()
