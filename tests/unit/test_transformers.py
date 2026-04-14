"""
Unit tests for data/ingestion/silver/transformer.py

Tests cover:
  - Cleaning: type enforcement, negative clip, null dropping
  - Enrichment: AQI categories, WHO exceedance flags, data completeness
  - Multi-source: openmeteo and openaq DataFrames both pass through cleanly
  - Edge cases: all-null pollutants, missing columns, invalid coordinates
"""

import pandas as pd
import pyarrow as pa
import pytest

from data.ingestion.silver.transformer import (
    _aqi_category,
    _canonicalize_for_delta,
    _dates_from_files,
    clean,
    enrich,
)

# ── Shared fixtures ────────────────────────────────────────────────────────────


@pytest.fixture()
def openmeteo_df():
    """Minimal openmeteo-style Bronze row (one grid point per city)."""
    return pd.DataFrame(
        {
            "station_id": ["tunis_tn", "algiers_dz", "cairo_eg"],
            "station_name": ["Tunis", "Algiers", "Cairo"],
            "country_code": ["TN", "DZ", "EG"],
            "latitude": [36.82, 36.74, 30.06],
            "longitude": [10.17, 3.06, 31.24],
            "date": ["2024-06-01", "2024-06-01", "2024-06-01"],
            "pm2_5": [12.5, 55.0, None],
            "pm10": [20.0, 80.0, 15.0],
            "nitrogen_dioxide": [5.0, 40.0, 8.0],
            "ozone": [80.0, 140.0, 70.0],
            "source": ["openmeteo"] * 3,
            "ingestion_ts": ["2024-06-02T00:00:00Z"] * 3,
            "partition_date": ["2024-06-01"] * 3,
        }
    )


@pytest.fixture()
def openaq_df():
    """Minimal openaq-style Bronze row (real station observations)."""
    return pd.DataFrame(
        {
            "station_id": ["1001", "1002", "1003"],
            "station_name": ["Tunis-Bardo", "Annaba", "Casablanca-Ain"],
            "city": ["Tunis", "Annaba", "Casablanca"],
            "country_code": ["TN", "DZ", "MA"],
            "latitude": [36.82, 36.90, 33.57],
            "longitude": [10.13, 7.77, -7.59],
            "date": ["2024-06-01"] * 3,
            "pm2_5": [18.0, 9.5, None],
            "pm10": [32.0, 16.0, 44.0],
            "nitrogen_dioxide": [28.0, 11.0, 20.0],
            "ozone": [95.0, 60.0, 88.0],
            "source": ["openaq"] * 3,
            "ingestion_ts": ["2024-06-02T00:00:00Z"] * 3,
            "partition_date": ["2024-06-01"] * 3,
        }
    )


# ── clean() ───────────────────────────────────────────────────────────────────


class TestClean:
    def test_drops_row_with_null_station_id(self, openmeteo_df):
        openmeteo_df.loc[0, "station_id"] = None
        result = clean(openmeteo_df, "openmeteo")
        assert "tunis_tn" not in result["station_id"].values

    def test_clips_negative_pm25_to_zero(self, openmeteo_df):
        openmeteo_df.loc[0, "pm2_5"] = -15.0
        result = clean(openmeteo_df, "openmeteo")
        assert result.loc[result["station_id"] == "tunis_tn", "pm2_5"].iloc[0] == 0.0

    def test_clips_extreme_pm10_to_upper_bound(self, openmeteo_df):
        openmeteo_df.loc[0, "pm10"] = 99_999.0
        result = clean(openmeteo_df, "openmeteo")
        assert result.loc[result["station_id"] == "tunis_tn", "pm10"].iloc[0] == 2_000.0

    def test_drops_invalid_coordinates(self, openmeteo_df):
        openmeteo_df.loc[0, "latitude"] = 999.0
        result = clean(openmeteo_df, "openmeteo")
        assert "tunis_tn" not in result["station_id"].values

    def test_normalises_date_to_string(self, openmeteo_df):
        result = clean(openmeteo_df, "openmeteo")
        assert result["date"].iloc[0] == "2024-06-01"

    def test_source_column_set(self, openaq_df):
        result = clean(openaq_df, "openaq")
        assert (result["source"] == "openaq").all()

    def test_missing_pollutant_column_becomes_nan(self, openmeteo_df):
        openmeteo_df = openmeteo_df.drop(columns=["ozone"])
        result = clean(openmeteo_df, "openmeteo")
        assert "ozone" in result.columns
        assert result["ozone"].isna().all()

    def test_openaq_df_passes_through(self, openaq_df):
        result = clean(openaq_df, "openaq")
        assert len(result) == len(openaq_df)
        assert set(result.columns).issuperset({"station_id", "pm2_5", "pm10"})


# ── enrich() ──────────────────────────────────────────────────────────────────


class TestEnrich:
    def test_aqi_category_present(self, openmeteo_df):
        cleaned = clean(openmeteo_df, "openmeteo")
        enriched = enrich(cleaned)
        assert "aqi_category" in enriched.columns
        assert enriched["aqi_category"].notna().all()

    def test_who_pm25_flag_below_threshold(self, openmeteo_df):
        cleaned = clean(openmeteo_df, "openmeteo")
        enriched = enrich(cleaned)
        # tunis_tn pm2_5=12.5 < 15 → 0
        row = enriched[enriched["station_id"] == "tunis_tn"].iloc[0]
        assert row["who_pm25_exceed"] == 0

    def test_who_pm25_flag_above_threshold(self, openmeteo_df):
        cleaned = clean(openmeteo_df, "openmeteo")
        enriched = enrich(cleaned)
        # algiers_dz pm2_5=55.0 > 15 → 1
        row = enriched[enriched["station_id"] == "algiers_dz"].iloc[0]
        assert row["who_pm25_exceed"] == 1

    def test_who_no2_flag(self, openaq_df):
        cleaned = clean(openaq_df, "openaq")
        enriched = enrich(cleaned)
        # Tunis-Bardo NO2=28.0 > 25 → 1
        row = enriched[enriched["station_id"] == "1001"].iloc[0]
        assert row["who_no2_exceed"] == 1

    def test_data_completeness_full_row(self, openmeteo_df):
        cleaned = clean(openmeteo_df, "openmeteo")
        enriched = enrich(cleaned)
        # tunis_tn has all 4 pollutants → completeness = 1.0
        row = enriched[enriched["station_id"] == "tunis_tn"].iloc[0]
        assert row["data_completeness"] == 1.0

    def test_data_completeness_partial_row(self, openmeteo_df):
        # cairo_eg has pm2_5=None → 3/4 pollutants present
        cleaned = clean(openmeteo_df, "openmeteo")
        enriched = enrich(cleaned)
        row = enriched[enriched["station_id"] == "cairo_eg"].iloc[0]
        assert row["data_completeness"] == pytest.approx(0.75)

    def test_drops_rows_below_min_completeness(self):
        df = pd.DataFrame(
            {
                "station_id": ["X-001"],
                "station_name": ["Ghost"],
                "country_code": ["TN"],
                "latitude": [36.0],
                "longitude": [10.0],
                "date": ["2024-06-01"],
                "pm2_5": [None],
                "pm10": [None],
                "nitrogen_dioxide": [None],
                "ozone": [None],
                "source": ["openmeteo"],
                "partition_date": ["2024-06-01"],
                "ingestion_ts": ["2024-06-02T00:00:00Z"],
            }
        )
        cleaned = clean(df, "openmeteo")
        enriched = enrich(cleaned)
        assert enriched.empty

    def test_silver_ts_added(self, openmeteo_df):
        cleaned = clean(openmeteo_df, "openmeteo")
        enriched = enrich(cleaned)
        assert "silver_ts" in enriched.columns
        assert enriched["silver_ts"].notna().all()


class TestDeltaPreparation:
    def test_all_null_city_stays_string_for_delta(self, openmeteo_df):
        openmeteo_df = openmeteo_df.drop(columns=["city"], errors="ignore")
        cleaned = clean(openmeteo_df, "openmeteo")
        enriched = enrich(cleaned)
        prepared = _canonicalize_for_delta(enriched)

        arrow_schema = pa.Table.from_pandas(prepared, preserve_index=False).schema

        assert prepared.columns.tolist()[-1] == "partition_date"
        assert pa.types.is_string(arrow_schema.field("city").type)
        assert not pa.types.is_null(arrow_schema.field("city").type)

    def test_dates_from_files_reads_hive_partition_paths(self):
        files = [
            "partition_date=2024-01-15/part-0001.snappy.parquet",
            "partition_date=2024-01-16/source=openmeteo/part-0002.snappy.parquet",
            "source=openmeteo/partition_date=2024-01-17/part-0003.snappy.parquet",
        ]

        assert _dates_from_files(files) == {
            "2024-01-15",
            "2024-01-16",
            "2024-01-17",
        }


# ── _aqi_category() ───────────────────────────────────────────────────────────


class TestAQICategory:
    @pytest.mark.parametrize(
        "pm25,expected",
        [
            (0.0, "good"),
            (12.0, "good"),
            (12.1, "moderate"),
            (35.4, "moderate"),
            (35.5, "unhealthy_sensitive"),
            (55.4, "unhealthy_sensitive"),
            (55.5, "unhealthy"),
            (150.4, "unhealthy"),
            (150.5, "very_unhealthy"),
            (250.4, "very_unhealthy"),
            (250.5, "hazardous"),
            (500.0, "hazardous"),
        ],
    )
    def test_breakpoints(self, pm25, expected):
        assert _aqi_category(pm25) == expected

    def test_none_returns_unknown(self):
        assert _aqi_category(None) == "unknown"

    def test_nan_returns_unknown(self):
        assert _aqi_category(float("nan")) == "unknown"
