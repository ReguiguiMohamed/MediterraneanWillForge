"""Weather Silver labelling and Gold heat-event detection."""

from __future__ import annotations

import pandas as pd
import pytest

from data.ingestion.gold.marts import (
    build_daily_country_weather,
    flag_heat_events,
)
from data.ingestion.silver.weather import clean, enrich


def _station_days(
    station_id: str,
    country: str,
    temps: list[float],
    start: str = "2026-06-01",
) -> pd.DataFrame:
    """One row per day for a station, with a given run of daily highs."""
    dates = pd.date_range(start, periods=len(temps), freq="D").strftime("%Y-%m-%d")
    return pd.DataFrame(
        {
            "station_id": station_id,
            "station_name": station_id.title(),
            "country_code": country,
            "partition_date": dates,
            "date": dates,
            "temp_max_c": temps,
            "temp_min_c": [t - 10 for t in temps],
            "temp_mean_c": [t - 5 for t in temps],
            "apparent_temp_max_c": [t + 2 for t in temps],
            "precipitation_mm": 0.0,
            "wind_gust_max_kmh": 25.0,
            "humidity_pct": 50.0,
            "dust": 4.0,
            "condition": "clear",
            "wind_level": "breezy",
            "dust_level": "none",
        }
    )


# ── Silver labelling ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, "clear"),
        (3, "cloudy"),
        (48, "fog"),
        (63, "rain"),
        (82, "rain"),
        (75, "snow"),
        (95, "thunderstorm"),
        (7, "unknown"),
    ],
)
def test_condition_labels_follow_the_wmo_code(code, expected):
    raw = pd.DataFrame(
        {
            "station_id": ["tunis_tn"],
            "date": ["2026-08-20"],
            "temp_max_c": [30.0],
            "weather_code": [code],
        }
    )

    assert enrich(clean(raw))["condition"].iloc[0] == expected


def test_wind_and_dust_are_banded_and_nulls_stay_unknown():
    raw = pd.DataFrame(
        {
            "station_id": ["a", "b", "c"],
            "date": ["2026-08-20"] * 3,
            "temp_max_c": [30.0, 31.0, 32.0],
            "weather_code": [0, 0, 0],
            "wind_gust_max_kmh": [10.0, 95.0, None],
            "dust": [5.0, 240.0, None],
        }
    )

    labelled = enrich(clean(raw))

    assert list(labelled["wind_level"]) == ["calm", "storm", "unknown"]
    assert list(labelled["dust_level"]) == ["none", "severe", "unknown"]


def test_clean_drops_rows_without_a_temperature():
    raw = pd.DataFrame(
        {
            "station_id": ["tunis_tn", "athens_gr"],
            "date": ["2026-08-20", "2026-08-20"],
            "temp_max_c": [33.0, None],
            "weather_code": [0, 0],
        }
    )

    assert list(clean(raw)["station_id"]) == ["tunis_tn"]


def test_clean_clips_an_impossible_temperature():
    raw = pd.DataFrame(
        {
            "station_id": ["tunis_tn"],
            "date": ["2026-08-20"],
            "temp_max_c": [1_200.0],
            "weather_code": [0],
        }
    )

    assert clean(raw)["temp_max_c"].iloc[0] == 60.0


# ── Gold heat events ───────────────────────────────────────────────────────────


def test_no_verdict_before_a_baseline_exists():
    # Five days is under the minimum history, however hot they are.
    frame = _station_days("tunis_tn", "TN", [44.0] * 5)

    flagged = flag_heat_events(frame)

    assert flagged["heat_baseline_c"].isna().all()
    assert not flagged["is_hot_day"].any()
    assert (flagged["heat_streak_days"] == 0).all()


def test_a_three_day_spike_after_a_mild_month_is_a_heatwave():
    frame = _station_days("tunis_tn", "TN", [26.0] * 30 + [39.0, 39.5, 40.5])

    flagged = flag_heat_events(frame)
    tail = flagged.tail(3)

    assert list(tail["is_hot_day"]) == [True, True, True]
    assert list(tail["heat_streak_days"]) == [1, 2, 3]

    country = build_daily_country_weather(frame).sort_values("partition_date")
    assert list(country["heat_alert"].tail(3)) == [
        "heat_advisory",
        "heat_advisory",
        "extreme_heatwave",
    ]


def test_an_ordinary_hot_month_raises_no_alert():
    # Steadily hot, never hotter than it has been: the relative baseline is
    # what stops an entire Mediterranean August reading as a heatwave.
    frame = _station_days("tunis_tn", "TN", [35.0] * 45)

    country = build_daily_country_weather(frame)

    assert set(country["heat_alert"].tail(10)) == {"none"}


def test_a_warm_spell_below_the_floor_is_not_a_heatwave():
    # Ten degrees above a cold baseline, but 22 C is nobody's heatwave.
    frame = _station_days("athens_gr", "GR", [12.0] * 30 + [22.0, 22.5, 23.0])

    country = build_daily_country_weather(frame)

    assert set(country["heat_alert"]) == {"none"}


def test_a_cold_snap_below_freezing_is_a_severe_cold_wave():
    frame = _station_days("istanbul_tr", "TR", [18.0] * 30 + [8.0, 7.0, 6.0])

    country = build_daily_country_weather(frame).sort_values("partition_date")

    assert list(country["cold_alert"].tail(3)) == [
        "cold_advisory",
        "cold_advisory",
        "severe_cold_wave",
    ]


def test_streaks_do_not_run_across_two_stations():
    hot_tail = [26.0] * 30 + [38.0, 38.5, 39.0]
    frame = pd.concat(
        [
            _station_days("tunis_tn", "TN", hot_tail),
            _station_days("athens_gr", "GR", [20.0] * 33),
        ],
        ignore_index=True,
    )

    flagged = flag_heat_events(frame)
    athens = flagged[flagged["station_id"] == "athens_gr"]

    assert (athens["heat_streak_days"] == 0).all()
    assert flagged[flagged["station_id"] == "tunis_tn"]["heat_streak_days"].max() == 3


def test_a_country_takes_the_worst_of_its_cities():
    frame = pd.concat(
        [
            _station_days("cairo_eg", "EG", [26.0] * 30 + [41.0, 41.0, 41.0]),
            _station_days("alexandria_eg", "EG", [26.0] * 33),
        ],
        ignore_index=True,
    )
    frame.loc[frame["station_id"] == "alexandria_eg", "condition"] = "thunderstorm"

    country = build_daily_country_weather(frame).sort_values("partition_date")
    latest = country.iloc[-1]

    assert latest["stations"] == 2
    assert latest["stations_hot"] == 1
    assert latest["temp_max_c"] == 41.0
    assert latest["heat_alert"] == "extreme_heatwave"
    assert latest["condition"] == "thunderstorm"


# ── Bronze range ingest ────────────────────────────────────────────────────────


def test_range_timeout_grows_with_the_range():
    from datetime import date

    from data.ingestion.bronze import weather_ingestor as wi

    one_day = date(2026, 8, 23)
    assert wi._timeout(one_day, one_day) == 30
    assert wi._timeout(date(2026, 3, 31), one_day) == 175
    # A request for a decade still gets a bounded wait.
    assert wi._timeout(date(2016, 1, 1), one_day) == wi._MAX_TIMEOUT


def test_a_partial_range_is_refused_rather_than_written():
    """One city short must not mark the whole range present."""
    from datetime import date

    from data.ingestion.bronze import weather_ingestor as wi

    ingestor = wi.WeatherIngestor.__new__(wi.WeatherIngestor)
    partial = pd.DataFrame(
        {
            "station_id": ["tunis_tn", "tunis_tn"],
            "partition_date": ["2026-08-22", "2026-08-23"],
        }
    )
    ingestor._existing_partition_dates = lambda: set()
    ingestor._fetch_range = lambda start, end: partial
    ingestor._write = lambda df: pytest.fail("a partial range must not be written")

    with pytest.raises(RuntimeError, match="Range ingest incomplete"):
        ingestor.run_range(date(2026, 8, 22), date(2026, 8, 23))


def test_rebuild_replaces_a_range_a_plain_run_would_skip():
    """The repair path: present partitions are refetched, not skipped."""
    from datetime import date

    from data.ingestion.bronze import weather_ingestor as wi

    ingestor = wi.WeatherIngestor.__new__(wi.WeatherIngestor)
    complete = pd.DataFrame(
        {
            "station_id": [c[0] for c in wi.MEDITERRANEAN_CITIES],
            "partition_date": ["2026-08-22"] * len(wi.MEDITERRANEAN_CITIES),
        }
    )
    calls = []
    ingestor._existing_partition_dates = lambda: pytest.fail(
        "a rebuild must not consult the existing partitions"
    )
    ingestor._fetch_range = lambda start, end: complete
    ingestor._replace = lambda df, start, end: calls.append(("replace", start, end))
    ingestor._write = lambda df: pytest.fail("a rebuild must replace, not append")
    ingestor._push_metrics = lambda rows, elapsed: None

    ingestor.run_range(date(2026, 8, 22), date(2026, 8, 22), rebuild=True)

    assert calls == [("replace", date(2026, 8, 22), date(2026, 8, 22))]
