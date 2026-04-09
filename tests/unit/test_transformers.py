"""Unit tests for silver/transformer.py cleaning logic."""
import pytest
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "data" / "ingestion"))
from silver.transformer import clean_copernicus, _aqi_category


@pytest.fixture()
def raw_df():
    return pd.DataFrame({
        "station_id": ["TN-001", "TN-002", "TN-003"],
        "station_name": ["Tunis", "Sfax", "Sousse"],
        "latitude":  [36.82, 34.74, 35.83],
        "longitude": [10.17, 10.76, 10.64],
        "date":      ["2024-06-01", "2024-06-01", "2024-06-01"],
        "pm2p5":     [12.5, 55.0, None],
        "pm10":      [20.0, 80.0, 15.0],
        "no2":       [5.0, 40.0, 8.0],
        "o3":        [80.0, 140.0, 70.0],
        "source":    ["copernicus", "copernicus", "copernicus"],
        "ingestion_date": ["2024-06-02"] * 3,
        "ingestion_ts":   ["2024-06-02T00:00:00Z"] * 3,
        "partition_date": ["2024-06-01"] * 3,
    })


def test_clean_drops_null_station(raw_df):
    raw_df.loc[0, "station_id"] = None
    cleaned = clean_copernicus(raw_df)
    assert "TN-001" not in cleaned["station_id"].values


def test_aqi_category_good():
    assert _aqi_category(8.0) == "good"


def test_aqi_category_moderate():
    assert _aqi_category(25.0) == "moderate"


def test_aqi_category_unhealthy():
    assert _aqi_category(100.0) == "unhealthy"


def test_aqi_category_hazardous():
    assert _aqi_category(200.0) == "hazardous"


def test_aqi_category_null():
    assert _aqi_category(float("nan")) == "unknown"


def test_who_exceedance_flag(raw_df):
    cleaned = clean_copernicus(raw_df)
    # TN-001 pm2p5=12.5 < 15 threshold → 0
    row = cleaned[cleaned["station_id"] == "TN-001"].iloc[0]
    assert row["who_exceedance"] == 0
    # TN-002 pm2p5=55.0 > 15 → 1
    row2 = cleaned[cleaned["station_id"] == "TN-002"].iloc[0]
    assert row2["who_exceedance"] == 1


def test_pm25_clipped_to_zero(raw_df):
    raw_df.loc[0, "pm2p5"] = -10.0
    cleaned = clean_copernicus(raw_df)
    assert cleaned.loc[cleaned["station_id"] == "TN-001", "pm2p5"].iloc[0] == 0.0


def test_silver_ts_added(raw_df):
    cleaned = clean_copernicus(raw_df)
    assert "silver_ts" in cleaned.columns
    assert cleaned["silver_ts"].notna().all()
