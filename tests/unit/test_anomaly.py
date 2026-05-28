import pandas as pd

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
