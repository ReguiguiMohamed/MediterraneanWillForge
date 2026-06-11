"""
Unit tests for data/quality/run_checks.py

Tests cover every check function in isolation — no Delta Lake or
Prometheus connection required.
"""

import pandas as pd
import pytest

import data.quality.run_checks as run_checks
from data.quality.run_checks import (
    check_data_completeness_floor,
    check_null_rate,
    check_required_columns,
    check_row_count,
    check_valid_values,
    check_value_range,
    missing_data_result,
    parse_partition_dates,
    run_bronze_checks,
    run_silver_checks,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def bronze_df():
    return pd.DataFrame(
        {
            "station_id": ["tunis_tn", "algiers_dz", "cairo_eg"],
            "station_name": ["Tunis", "Algiers", "Cairo"],
            "country_code": ["TN", "DZ", "EG"],
            "latitude": [36.82, 36.74, 30.06],
            "longitude": [10.17, 3.06, 31.24],
            "date": ["2024-06-01"] * 3,
            "pm2_5": [12.5, 55.0, 8.0],
            "pm10": [20.0, 80.0, 15.0],
            "nitrogen_dioxide": [5.0, 40.0, 8.0],
            "ozone": [80.0, 140.0, 70.0],
            "source": ["openmeteo"] * 3,
            "ingestion_ts": ["2024-06-02T00:00:00Z"] * 3,
            "partition_date": ["2024-06-01"] * 3,
        }
    )


@pytest.fixture()
def silver_df():
    return pd.DataFrame(
        {
            "station_id": ["tunis_tn", "algiers_dz"],
            "station_name": ["Tunis", "Algiers"],
            "city": ["Tunis", "Algiers"],
            "country_code": ["TN", "DZ"],
            "latitude": [36.82, 36.74],
            "longitude": [10.17, 3.06],
            "date": ["2024-06-01", "2024-06-01"],
            "pm2_5": [12.5, 55.0],
            "pm10": [20.0, 80.0],
            "nitrogen_dioxide": [5.0, 40.0],
            "ozone": [80.0, 140.0],
            "aqi_category": ["good", "unhealthy"],
            "who_pm25_exceed": [0, 1],
            "who_pm10_exceed": [0, 1],
            "who_no2_exceed": [0, 1],
            "who_o3_exceed": [0, 1],
            "data_completeness": [1.0, 1.0],
            "source": ["openmeteo", "openmeteo"],
            "silver_ts": ["2024-06-02T00:00:00Z"] * 2,
            "partition_date": ["2024-06-01"] * 2,
        }
    )


# ── check_row_count ────────────────────────────────────────────────────────────


def test_row_count_passes(bronze_df):
    r = check_row_count(bronze_df, "bronze", "t")
    assert r.passed


def test_row_count_fails_on_empty():
    r = check_row_count(pd.DataFrame(), "bronze", "t")
    assert not r.passed
    assert r.severity == "fail"


# ── check_required_columns ─────────────────────────────────────────────────────


def test_required_columns_all_present(bronze_df):
    results = check_required_columns(bronze_df, "bronze", "t", ["station_id", "pm2_5"])
    assert all(r.passed for r in results)


def test_required_columns_missing_detected(bronze_df):
    results = check_required_columns(
        bronze_df, "bronze", "t", ["station_id", "nonexistent_col"]
    )
    missing = [r for r in results if not r.passed]
    assert len(missing) == 1
    assert "nonexistent_col" in missing[0].check_name


def test_required_columns_uses_great_expectations_validator():
    class FakeExpectationResult:
        def __init__(self, success):
            self.success = success

    class FakeValidator:
        def __init__(self):
            self.columns = []

        def expect_column_to_exist(self, col):
            self.columns.append(col)
            return FakeExpectationResult(success=col != "blocked_by_ge")

    validator = FakeValidator()

    results = check_required_columns(
        pd.DataFrame({"blocked_by_ge": [1]}),
        "bronze",
        "t",
        ["station_id", "blocked_by_ge"],
        validator=validator,
    )

    assert validator.columns == ["station_id", "blocked_by_ge"]
    assert [r.passed for r in results] == [True, False]


def test_gx_context_filters_version_specific_config_kwargs(monkeypatch):
    class LegacyDataContextConfig:
        def __init__(
            self,
            config_version=None,
            anonymous_usage_statistics=None,
            store_backend_defaults=None,
        ):
            self.kwargs = {
                "config_version": config_version,
                "anonymous_usage_statistics": anonymous_usage_statistics,
                "store_backend_defaults": store_backend_defaults,
            }

    def fake_get_context(project_config):
        return project_config

    monkeypatch.setattr(run_checks, "DataContextConfig", LegacyDataContextConfig)
    monkeypatch.setattr(run_checks.gx, "get_context", fake_get_context)

    context = run_checks._gx_context()

    assert context.kwargs["config_version"] == 3.0
    assert context.kwargs["anonymous_usage_statistics"] == {"enabled": False}
    assert context.kwargs["store_backend_defaults"] is not None


def test_parse_partition_dates_accepts_workflow_and_csv_formats():
    assert parse_partition_dates("2026-06-04 2026-06-05,2026-06-04") == [
        "2026-06-04",
        "2026-06-05",
    ]


def test_parse_partition_dates_rejects_invalid_dates():
    with pytest.raises(ValueError):
        parse_partition_dates("2026-06-31")


def test_missing_required_data_is_a_hard_failure():
    result = missing_data_result(
        "silver",
        "silver/air_quality",
        required=True,
        partition_dates=["2026-06-05"],
    )

    assert not result.passed
    assert result.severity == "fail"
    assert "2026-06-05" in result.detail


def test_missing_optional_source_is_a_warning():
    result = missing_data_result(
        "bronze",
        "bronze/openaq/air_quality",
        required=False,
        partition_dates=["2026-06-05"],
    )

    assert not result.passed
    assert result.severity == "warn"


# ── check_null_rate ────────────────────────────────────────────────────────────


def test_null_rate_passes_clean_column(bronze_df):
    r = check_null_rate(bronze_df, "bronze", "t", "station_id")
    assert r.passed


def test_null_rate_warns_above_warn_threshold():
    df = pd.DataFrame({"pm2_5": [None, None, 10.0, 5.0, 3.0]})  # 40% null → warn
    r = check_null_rate(
        df, "bronze", "t", "pm2_5", warn_threshold=0.30, fail_threshold=0.70
    )
    assert not r.passed
    assert r.severity == "warn"


def test_null_rate_fails_above_fail_threshold():
    df = pd.DataFrame({"pm2_5": [None, None, None, None, 1.0]})  # 80%
    r = check_null_rate(
        df, "bronze", "t", "pm2_5", warn_threshold=0.30, fail_threshold=0.70
    )
    assert not r.passed
    assert r.severity == "fail"


def test_null_rate_warns_for_optional_source_above_fail_threshold():
    df = pd.DataFrame({"pm2_5": [None, None, None, None, 1.0]})  # 80%
    r = check_null_rate(
        df,
        "bronze",
        "t",
        "pm2_5",
        warn_threshold=0.30,
        fail_threshold=0.70,
        hard_fail=False,
    )
    assert not r.passed
    assert r.severity == "warn"
    assert "non-blocking optional source" in r.detail


def test_null_rate_missing_column_is_fail():
    r = check_null_rate(pd.DataFrame({"x": [1]}), "bronze", "t", "pm2_5")
    assert not r.passed
    assert r.severity == "fail"


# ── check_value_range ──────────────────────────────────────────────────────────


def test_value_range_passes(bronze_df):
    r = check_value_range(bronze_df, "bronze", "t", "pm2_5", min_val=0, max_val=1000)
    assert r.passed


def test_value_range_fails_on_negative():
    df = pd.DataFrame({"pm2_5": [-5.0, 10.0, 20.0]})
    r = check_value_range(df, "bronze", "t", "pm2_5", min_val=0, mostly=1.0)
    assert not r.passed


def test_value_range_respects_mostly():
    df = pd.DataFrame({"pm2_5": [-5.0, 10.0, 20.0, 15.0, 8.0]})  # 1/5 violates
    r = check_value_range(df, "bronze", "t", "pm2_5", min_val=0, mostly=0.75)
    assert r.passed  # 4/5 = 80% compliant ≥ 75%


# ── check_valid_values ─────────────────────────────────────────────────────────


def test_valid_values_passes(bronze_df):
    bronze_df = bronze_df.assign(source=["openmeteo", "openaq", "waqi"])
    r = check_valid_values(
        bronze_df, "bronze", "t", "source", {"openmeteo", "openaq", "waqi"}
    )
    assert r.passed


def test_valid_values_fails_on_unknown():
    df = pd.DataFrame({"source": ["openmeteo", "synthetic"]})
    r = check_valid_values(df, "bronze", "t", "source", {"openmeteo", "openaq", "waqi"})
    assert not r.passed


# ── check_data_completeness_floor ─────────────────────────────────────────────


def test_completeness_floor_passes(silver_df):
    r = check_data_completeness_floor(silver_df, "silver", "t")
    assert r.passed


def test_completeness_floor_fails_when_below():
    df = pd.DataFrame({"data_completeness": [0.10, 1.0]})  # one below 0.2
    r = check_data_completeness_floor(df, "silver", "t", floor=0.20)
    assert not r.passed


def test_completeness_floor_skips_if_column_absent():
    r = check_data_completeness_floor(pd.DataFrame({"x": [1]}), "silver", "t")
    assert r.passed  # gracefully skipped


# ── Full suite smoke tests ─────────────────────────────────────────────────────


def test_bronze_suite_all_pass(bronze_df):
    results = run_bronze_checks(bronze_df, "openmeteo")
    failures = [r for r in results if not r.passed and r.severity == "fail"]
    assert len(failures) == 0, [r.check_name for r in failures]


def test_bronze_suite_accepts_waqi_source(bronze_df):
    bronze_df = bronze_df.assign(source=["waqi"] * len(bronze_df))
    results = run_bronze_checks(bronze_df, "waqi")
    failures = [r for r in results if not r.passed and r.severity == "fail"]
    assert len(failures) == 0, [r.check_name for r in failures]


@pytest.mark.parametrize("source", ["openaq", "waqi"])
def test_bronze_suite_treats_optional_source_sparsity_as_warning(bronze_df, source):
    sparse = pd.concat([bronze_df] * 2, ignore_index=True).iloc[:5].copy()
    sparse["station_id"] = [f"station-{i}" for i in range(len(sparse))]
    sparse["source"] = source
    sparse["pm2_5"] = [None, None, None, None, 10.0]

    results = run_bronze_checks(sparse, source)
    pm25_result = next(r for r in results if r.check_name == "null_rate_pm2_5")

    assert not pm25_result.passed
    assert pm25_result.severity == "warn"


def test_bronze_suite_keeps_openmeteo_pollutant_sparsity_as_failure(bronze_df):
    sparse = pd.concat([bronze_df] * 2, ignore_index=True).iloc[:5].copy()
    sparse["station_id"] = [f"station-{i}" for i in range(len(sparse))]
    sparse["source"] = "openmeteo"
    sparse["pm2_5"] = [None, None, None, None, 10.0]

    results = run_bronze_checks(sparse, "openmeteo")
    pm25_result = next(r for r in results if r.check_name == "null_rate_pm2_5")

    assert not pm25_result.passed
    assert pm25_result.severity == "fail"


def test_silver_suite_all_pass(silver_df):
    results = run_silver_checks(silver_df)
    failures = [r for r in results if not r.passed and r.severity == "fail"]
    assert len(failures) == 0, [r.check_name for r in failures]


def test_silver_suite_accepts_waqi_source(silver_df):
    silver_df = silver_df.assign(source=["waqi"] * len(silver_df))
    results = run_silver_checks(silver_df)
    failures = [r for r in results if not r.passed and r.severity == "fail"]
    assert len(failures) == 0, [r.check_name for r in failures]


def test_quality_metrics_are_forwarded_to_grafana(monkeypatch):
    calls = []
    monkeypatch.setattr(run_checks, "push_to_gateway", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        run_checks,
        "push_to_grafana",
        lambda registry, job: calls.append(job),
    )

    run_checks._push_results(
        [
            run_checks.CheckResult(
                check_name="row_count_non_zero",
                layer="silver",
                table="silver/air_quality",
                passed=True,
                severity="fail",
            )
        ],
        "http://localhost:9091",
    )

    assert calls == ["med_ops_quality"]


def test_bronze_suite_detects_missing_required_column(bronze_df):
    bronze_df = bronze_df.drop(columns=["station_id"])
    results = run_bronze_checks(bronze_df, "openmeteo")
    failures = [r for r in results if not r.passed and r.severity == "fail"]
    assert any("station_id" in r.check_name for r in failures)
