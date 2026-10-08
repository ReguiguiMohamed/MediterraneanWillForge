import pandas as pd

from data.quality.verify_outputs import validate_gold_frame


def _summary(partition_date="2026-06-05"):
    return pd.DataFrame(
        {
            "partition_date": [partition_date],
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


def test_daily_summary_passes_its_contract():
    assert validate_gold_frame("daily_country_summary", _summary(), []) == []


def test_daily_summary_requires_requested_partition():
    errors = validate_gold_frame(
        "daily_country_summary",
        _summary("2026-06-04"),
        target_dates=["2026-06-05"],
    )

    assert errors == [
        "gold/daily_country_summary: missing requested partition(s) ['2026-06-05']"
    ]


def test_empty_table_breaks_its_contract():
    errors = validate_gold_frame("daily_country_summary", _summary().iloc[0:0], [])

    assert errors == [
        "gold/daily_country_summary: Check that model daily_country_summary has "
        "row_count > 0: Actual row_count(daily_country_summary) was 0, expected > 0"
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

    assert validate_gold_frame("anomaly_alerts", frame, ["2026-06-05"]) == [
        "gold/anomaly_alerts: Check that field source has invalid_count = 0: "
        "Actual invalid_count(source) was 1, expected = 0"
    ]


def test_anomaly_contract_requires_every_column():
    frame = pd.DataFrame(
        {
            "partition_date": ["2026-06-05"],
            "source": ["openaq"],
            "station_id": ["station-1"],
            "anomaly_score": [-0.5],
        }
    )

    assert validate_gold_frame("anomaly_alerts", frame, ["2026-06-05"]) == [
        "gold/anomaly_alerts: Check that field 'is_anomaly' is present: "
        "Required column 'is_anomaly' is missing"
    ]


def test_weather_contract_rejects_an_impossible_day():
    frame = pd.DataFrame(
        {
            "partition_date": ["2026-06-05"],
            "country_code": ["TN"],
            "stations": [1],
            "temp_max_c": [12.0],
            "temp_mean_c": [20.0],
            "temp_min_c": [28.0],
            "condition": ["clear"],
            "wind_level": ["breezy"],
            "dust_level": ["none"],
            "heat_alert": ["scorching"],
            "heat_streak_days": [-1],
            "cold_alert": ["none"],
            "cold_streak_days": [0],
        }
    )

    assert validate_gold_frame("daily_country_weather", frame, ["2026-06-05"]) == [
        "gold/daily_country_weather: Check that field heat_alert has "
        "invalid_count = 0: Actual invalid_count(heat_alert) was 1, expected = 0",
        "gold/daily_country_weather: Check that field heat_streak_days has a "
        "minimum of 0: Actual invalid_count(heat_streak_days) was 1, expected = 0",
        "gold/daily_country_weather: The low never sits above the high.: "
        "Actual custom_sql(daily_country_weather) was 1, expected = 0",
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

    assert validate_gold_frame("wildfire_risk_index", frame, ["2026-06-05"]) == [
        "gold/wildfire_risk_index: Check that field risk_index has a maximum of "
        "100: Actual invalid_count(risk_index) was 1, expected = 0",
        "gold/wildfire_risk_index: Check that field risk_level has "
        "invalid_count = 0: Actual invalid_count(risk_level) was 1, expected = 0",
    ]
