import pandas as pd

from data.quality.verify_outputs import (
    GOLD_TABLE_CONTRACTS,
    validate_gold_frame,
)


def _contract(name):
    return next(item for item in GOLD_TABLE_CONTRACTS if item.name == name)


def test_daily_summary_requires_requested_partition():
    frame = pd.DataFrame(
        {
            "partition_date": ["2026-06-04"],
            "country_code": ["TN"],
            "source": ["openmeteo"],
            "mean_pm2_5": [10.0],
            "max_pm2_5": [15.0],
            "mean_pm10": [20.0],
            "mean_no2": [5.0],
            "mean_o3": [30.0],
            "station_count": [1],
            "who_pm25_exceed_pct": [0.0],
            "who_pm10_exceed_pct": [0.0],
            "who_no2_exceed_pct": [0.0],
            "who_o3_exceed_pct": [0.0],
        }
    )

    errors = validate_gold_frame(
        _contract("daily_country_summary"),
        frame,
        target_dates=["2026-06-05"],
    )

    assert errors == [
        "gold/daily_country_summary: missing requested partition(s) ['2026-06-05']"
    ]


def test_anomaly_contract_rejects_waqi_rows():
    frame = pd.DataFrame(
        {
            "partition_date": ["2026-06-05"],
            "source": ["waqi"],
            "station_id": ["station-1"],
            "anomaly_score": [-0.5],
            "is_anomaly": [1],
        }
    )

    errors = validate_gold_frame(
        _contract("anomaly_alerts"),
        frame,
        target_dates=["2026-06-05"],
    )

    assert errors == [
        "gold/anomaly_alerts: concentration model contains sources ['waqi']"
    ]


def test_wildfire_contract_rejects_invalid_domains():
    frame = pd.DataFrame(
        {
            "partition_date": ["2026-06-05"],
            "source": ["openmeteo"],
            "station_id": ["station-1"],
            "risk_index": [120.0],
            "risk_level": ["unknown"],
        }
    )

    errors = validate_gold_frame(
        _contract("wildfire_risk_index"),
        frame,
        target_dates=["2026-06-05"],
    )

    assert errors == [
        "gold/wildfire_risk_index: 1 risk_index values outside [0, 100]",
        "gold/wildfire_risk_index: unexpected risk levels ['unknown']",
    ]
