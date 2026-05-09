import pandas as pd

from data.reporting.analytics import (
    anomaly_daily_rates,
    coverage_by_date,
    mark_coverage_readiness,
    reporting_dates,
)


def test_anomaly_daily_rates_normalizes_by_rows():
    anomalies = pd.DataFrame(
        {
            "partition_date": ["2026-05-01"] * 10 + ["2026-05-02"] * 100,
            "is_anomaly": [1] + [0] * 9 + [1] * 5 + [0] * 95,
        }
    )

    rates = anomaly_daily_rates(anomalies)

    assert rates.loc[0, "anomaly_flags"] == 1
    assert rates.loc[0, "anomaly_rate_pct"] == 10.0
    assert rates.loc[1, "anomaly_flags"] == 5
    assert rates.loc[1, "anomaly_rate_pct"] == 5.0


def test_coverage_readiness_flags_large_latest_day_jump():
    coverage = pd.DataFrame(
        {
            "partition_date": [f"2026-05-0{i}" for i in range(1, 8)],
            "station_days": [20, 21, 19, 20, 22, 21, 100],
        }
    )

    marked = mark_coverage_readiness(coverage, lookback=4, max_ratio=2.25)

    assert marked.iloc[-1]["coverage_ratio"] > 4
    assert bool(marked.iloc[-1]["is_reporting_ready"]) is False


def test_reporting_dates_excludes_unready_coverage_date():
    summary = pd.DataFrame(
        {
            "partition_date": [f"2026-05-0{i}" for i in range(1, 8)],
            "country_code": ["TN"] * 7,
            "source": ["openmeteo"] * 7,
            "station_count": [20, 21, 19, 20, 22, 21, 100],
        }
    )

    dates = reporting_dates(summary)

    assert "2026-05-07" not in dates
    assert "2026-05-06" in dates


def test_coverage_by_date_tracks_source_mix():
    summary = pd.DataFrame(
        {
            "partition_date": ["2026-05-01", "2026-05-01", "2026-05-02"],
            "country_code": ["TN", "TN", "MA"],
            "source": ["openmeteo", "waqi", "waqi"],
            "station_count": [1, 3, 4],
        }
    )

    coverage = coverage_by_date(summary)

    assert coverage.loc[0, "station_days"] == 4
    assert coverage.loc[0, "source_count"] == 2
    assert coverage.loc[0, "sources"] == "openmeteo, waqi"
