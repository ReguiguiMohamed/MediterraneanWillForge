"""
data/quality/run_checks.py
───────────────────────────
Data quality runner for Bronze and Silver Delta Lake tables.

Reads the most recent partition of each table, evaluates Great Expectations
expectations, logs results, pushes failure counts to Prometheus Pushgateway,
and exits non-zero if any hard-fail threshold is breached.

Called by the quality Docker image and scheduled pipeline:
  python -m data.quality.run_checks

Environment variables required:
  MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY
  PROMETHEUS_PUSHGATEWAY_URL  (default: http://localhost:9091)
  MINIO_BUCKET_BRONZE         (default: bronze)
  MINIO_BUCKET_SILVER         (default: silver)
  QUALITY_LOOKBACK_DAYS       (default: 1  — how many past days to check)
  QUALITY_PARTITION_DATES     (optional comma/space-separated YYYY-MM-DD dates)
"""

from __future__ import annotations

import os
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import date, timedelta
from inspect import signature
from typing import Any

import great_expectations as gx
import pandas as pd
from deltalake import DeltaTable
from great_expectations.core.expectation_suite import ExpectationSuite
from great_expectations.data_context.types.base import (
    DataContextConfig,
    InMemoryStoreBackendDefaults,
)
from loguru import logger
from prometheus_client import CollectorRegistry, Counter, Gauge, push_to_gateway

from data.storage import delta_storage_options

try:
    from great_expectations.data_context.types.base import ProgressBarsConfig
except ImportError:  # Great Expectations 0.18 compatibility.
    ProgressBarsConfig = None

VALID_SOURCES = {"openmeteo", "openaq", "waqi"}
REQUIRED_BRONZE_SOURCES = {"openmeteo"}

# ── Storage config ─────────────────────────────────────────────────────────────


def _storage_options() -> dict[str, str]:
    return delta_storage_options()


def _expectation_suite(name: str) -> ExpectationSuite:
    try:
        return ExpectationSuite(name=name)
    except TypeError:
        return ExpectationSuite(expectation_suite_name=name)


def _gx_context() -> Any:
    config_kwargs: dict[str, Any] = {
        "config_version": 3.0,
        "analytics_enabled": False,
        "anonymous_usage_statistics": {"enabled": False},
        "store_backend_defaults": InMemoryStoreBackendDefaults(),
    }
    if ProgressBarsConfig is not None:
        config_kwargs["progress_bars"] = ProgressBarsConfig(
            globally=False,
            metric_calculations=False,
        )

    supported_kwargs = set(signature(DataContextConfig).parameters)
    filtered_kwargs = {
        key: value for key, value in config_kwargs.items() if key in supported_kwargs
    }

    return gx.get_context(project_config=DataContextConfig(**filtered_kwargs))


def _gx_validator(df: pd.DataFrame, suite_name: str) -> Any:
    context = _gx_context()

    # Great Expectations 0.18 fluent API returns a Validator here.
    sources = getattr(context, "sources", None)
    if sources is not None:
        try:
            validator = sources.pandas_default.read_dataframe(df)
            if hasattr(validator, "expect_column_to_exist"):
                return validator
        except Exception:
            pass
        try:
            datasource = sources.add_pandas(name=f"{suite_name}_pandas")
            validator = datasource.read_dataframe(df)
            if hasattr(validator, "expect_column_to_exist"):
                return validator
        except Exception:
            pass

    datasource_name = f"{suite_name}_pandas"
    asset_name = f"{suite_name}_asset"

    # Great Expectations 1.x returns a Batch from read_dataframe, so build the
    # Validator explicitly.
    datasource = context.data_sources.add_pandas(name=datasource_name)
    asset = datasource.add_dataframe_asset(name=asset_name)
    batch_definition = asset.add_batch_definition_whole_dataframe("batch")
    batch = batch_definition.get_batch(batch_parameters={"dataframe": df})
    return context.get_validator(
        batch=batch,
        expectation_suite=_expectation_suite(suite_name),
    )


def _ensure_gx_validator(
    validator: Any | None,
    df: pd.DataFrame,
    layer: str,
    table: str,
) -> Any:
    if validator is not None:
        return validator
    suite_name = f"{layer}_{table}".replace("/", "_")
    return _gx_validator(df, suite_name)


def _gx_expectation(method: Any, *args: Any, **kwargs: Any) -> Any:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="`result_format` configured at the Validator-level.*",
            category=UserWarning,
        )
        return method(*args, **kwargs)


def _gx_success(result: Any) -> bool:
    success = getattr(result, "success", None)
    if success is None and isinstance(result, dict):
        success = result.get("success")
    return bool(success)


def parse_partition_dates(raw: str) -> list[str]:
    """Parse a comma/whitespace-separated list of ISO partition dates."""
    tokens = raw.replace(",", " ").split()
    partition_dates = []
    seen = set()
    for token in tokens:
        normalized = date.fromisoformat(token).isoformat()
        if normalized not in seen:
            seen.add(normalized)
            partition_dates.append(normalized)
    return partition_dates


# ── Check result ───────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    check_name: str
    layer: str
    table: str
    passed: bool
    severity: str  # "warn" | "fail"
    detail: str = ""


def missing_data_result(
    layer: str,
    table: str,
    required: bool,
    partition_dates: list[str],
) -> CheckResult:
    target = (
        f"target partition(s): {', '.join(partition_dates)}"
        if partition_dates
        else "configured lookback window"
    )
    return CheckResult(
        check_name="data_present",
        layer=layer,
        table=table,
        passed=False,
        severity="fail" if required else "warn",
        detail=f"no data found for {target}",
    )


# ── Individual check functions ─────────────────────────────────────────────────


def check_row_count(
    df: pd.DataFrame,
    layer: str,
    table: str,
    min_rows: int = 1,
    validator: Any | None = None,
) -> CheckResult:
    gx_validator = _ensure_gx_validator(validator, df, layer, table)
    expectation = _gx_expectation(
        gx_validator.expect_table_row_count_to_be_between,
        min_value=min_rows,
    )
    passed = _gx_success(expectation)
    return CheckResult(
        check_name="row_count_non_zero",
        layer=layer,
        table=table,
        passed=passed,
        severity="fail",
        detail=f"{len(df)} rows (min {min_rows})",
    )


def check_required_columns(
    df: pd.DataFrame,
    layer: str,
    table: str,
    required: list[str],
    validator: Any | None = None,
) -> list[CheckResult]:
    gx_validator = _ensure_gx_validator(validator, df, layer, table)
    results = []
    for col in required:
        expectation = _gx_expectation(gx_validator.expect_column_to_exist, col)
        present = _gx_success(expectation)
        results.append(
            CheckResult(
                check_name=f"column_exists_{col}",
                layer=layer,
                table=table,
                passed=present,
                severity="fail",
                detail="present" if present else "MISSING",
            )
        )
    return results


def check_null_rate(
    df: pd.DataFrame,
    layer: str,
    table: str,
    col: str,
    warn_threshold: float = 0.30,
    fail_threshold: float = 0.70,
    hard_fail: bool = True,
    validator: Any | None = None,
) -> CheckResult:
    if col not in df.columns:
        return CheckResult(
            check_name=f"null_rate_{col}",
            layer=layer,
            table=table,
            passed=False,
            severity="fail",
            detail=f"column '{col}' missing",
        )
    gx_validator = _ensure_gx_validator(validator, df, layer, table)
    _gx_expectation(
        gx_validator.expect_column_values_to_not_be_null,
        col,
        mostly=max(0.0, 1.0 - fail_threshold),
    )
    null_rate = df[col].isna().mean()
    if null_rate >= fail_threshold:
        severity = "fail" if hard_fail else "warn"
        policy = "" if hard_fail else "; non-blocking optional source"
        return CheckResult(
            check_name=f"null_rate_{col}",
            layer=layer,
            table=table,
            passed=False,
            severity=severity,
            detail=f"{null_rate:.1%} nulls (threshold {fail_threshold:.0%}{policy})",
        )
    if null_rate >= warn_threshold:
        return CheckResult(
            check_name=f"null_rate_{col}",
            layer=layer,
            table=table,
            passed=False,
            severity="warn",
            detail=f"{null_rate:.1%} nulls (warn threshold {warn_threshold:.0%})",
        )
    return CheckResult(
        check_name=f"null_rate_{col}",
        layer=layer,
        table=table,
        passed=True,
        severity="warn",
        detail=f"{null_rate:.1%} nulls — OK",
    )


def check_value_range(
    df: pd.DataFrame,
    layer: str,
    table: str,
    col: str,
    min_val: float | None = None,
    max_val: float | None = None,
    mostly: float = 0.99,
    validator: Any | None = None,
) -> CheckResult:
    if col not in df.columns:
        return CheckResult(
            check_name=f"range_{col}",
            layer=layer,
            table=table,
            passed=False,
            severity="warn",
            detail=f"column '{col}' missing",
        )
    series = pd.to_numeric(df[col], errors="coerce").dropna()
    if series.empty:
        return CheckResult(
            check_name=f"range_{col}",
            layer=layer,
            table=table,
            passed=True,
            severity="warn",
            detail="no non-null values to check",
        )
    gx_validator = _ensure_gx_validator(validator, df, layer, table)
    expectation = _gx_expectation(
        gx_validator.expect_column_values_to_be_between,
        col,
        min_value=min_val,
        max_value=max_val,
        mostly=mostly,
    )
    mask = pd.Series([True] * len(series), index=series.index)
    if min_val is not None:
        mask &= series >= min_val
    if max_val is not None:
        mask &= series <= max_val
    compliance = mask.mean()
    passed = _gx_success(expectation)
    return CheckResult(
        check_name=f"range_{col}",
        layer=layer,
        table=table,
        passed=passed,
        severity="warn",
        detail=f"{compliance:.1%} in [{min_val}, {max_val}] (required {mostly:.0%})",
    )


def check_valid_values(
    df: pd.DataFrame,
    layer: str,
    table: str,
    col: str,
    valid_set: set[str],
    validator: Any | None = None,
) -> CheckResult:
    if col not in df.columns:
        return CheckResult(
            check_name=f"valid_values_{col}",
            layer=layer,
            table=table,
            passed=False,
            severity="warn",
            detail=f"column '{col}' missing",
        )
    gx_validator = _ensure_gx_validator(validator, df, layer, table)
    expectation = _gx_expectation(
        gx_validator.expect_column_values_to_be_in_set,
        col,
        value_set=sorted(valid_set),
    )
    invalid = df[col].dropna()[~df[col].dropna().isin(valid_set)]
    passed = _gx_success(expectation)
    return CheckResult(
        check_name=f"valid_values_{col}",
        layer=layer,
        table=table,
        passed=passed,
        severity="warn",
        detail=f"{len(invalid)} invalid values" if not passed else "all valid",
    )


def check_data_completeness_floor(
    df: pd.DataFrame,
    layer: str,
    table: str,
    col: str = "data_completeness",
    floor: float = 0.20,
    validator: Any | None = None,
) -> CheckResult:
    if col not in df.columns:
        return CheckResult(
            check_name="data_completeness_floor",
            layer=layer,
            table=table,
            passed=True,
            severity="warn",
            detail="column absent — skipped",
        )
    gx_validator = _ensure_gx_validator(validator, df, layer, table)
    expectation = _gx_expectation(
        gx_validator.expect_column_values_to_be_between,
        col,
        min_value=floor,
    )
    below = (df[col] < floor).sum()
    passed = _gx_success(expectation)
    return CheckResult(
        check_name="data_completeness_floor",
        layer=layer,
        table=table,
        passed=passed,
        severity="warn",
        detail=f"{below} rows below {floor:.0%} completeness",
    )


# ── Table-level check suites ───────────────────────────────────────────────────


def run_bronze_checks(
    df: pd.DataFrame,
    source: str,
) -> list[CheckResult]:
    layer = "bronze"
    table = f"bronze/{source}/air_quality"
    results: list[CheckResult] = []
    validator = _gx_validator(df, f"{layer}_{source}_air_quality")

    results.append(check_row_count(df, layer, table, validator=validator))
    results.extend(
        check_required_columns(
            df,
            layer,
            table,
            required=[
                "station_id",
                "latitude",
                "longitude",
                "date",
                "partition_date",
                "source",
            ],
            validator=validator,
        )
    )
    for col in ("pm2_5", "pm10", "nitrogen_dioxide", "ozone"):
        # Optional APIs may return only the pollutants available at each station.
        # Their schemas remain strict, but expected coverage gaps stay non-blocking.
        results.append(
            check_null_rate(
                df,
                layer,
                table,
                col,
                warn_threshold=0.40,
                fail_threshold=0.80,
                hard_fail=source in REQUIRED_BRONZE_SOURCES,
                validator=validator,
            )
        )
        results.append(
            check_value_range(df, layer, table, col, min_val=0, validator=validator)
        )

    results.append(
        check_valid_values(
            df,
            layer,
            table,
            "source",
            valid_set=VALID_SOURCES,
            validator=validator,
        )
    )
    return results


def run_silver_checks(df: pd.DataFrame) -> list[CheckResult]:
    layer = "silver"
    table = "silver/air_quality"
    results: list[CheckResult] = []
    validator = _gx_validator(df, f"{layer}_air_quality")

    results.append(check_row_count(df, layer, table, validator=validator))
    results.extend(
        check_required_columns(
            df,
            layer,
            table,
            required=[
                "station_id",
                "country_code",
                "latitude",
                "longitude",
                "date",
                "pm2_5",
                "pm10",
                "nitrogen_dioxide",
                "ozone",
                "aqi_category",
                "who_pm25_exceed",
                "data_completeness",
                "source",
                "silver_ts",
                "partition_date",
            ],
            validator=validator,
        )
    )
    for col in ("pm2_5", "pm10", "nitrogen_dioxide", "ozone"):
        results.append(
            check_null_rate(
                df,
                layer,
                table,
                col,
                warn_threshold=0.30,
                fail_threshold=0.70,
                validator=validator,
            )
        )
        results.append(
            check_value_range(df, layer, table, col, min_val=0, validator=validator)
        )

    results.append(
        check_valid_values(
            df,
            layer,
            table,
            "aqi_category",
            valid_set={
                "good",
                "moderate",
                "unhealthy_sensitive",
                "unhealthy",
                "very_unhealthy",
                "hazardous",
                "unknown",
            },
            validator=validator,
        )
    )
    results.append(
        check_valid_values(
            df,
            layer,
            table,
            "source",
            valid_set=VALID_SOURCES,
            validator=validator,
        )
    )
    results.append(check_data_completeness_floor(df, layer, table, validator=validator))
    return results


# ── Table loader ───────────────────────────────────────────────────────────────


def load_recent_partition(
    table_path: str,
    storage_options: dict[str, str],
    lookback_days: int = 1,
    partition_dates: list[str] | None = None,
) -> pd.DataFrame:
    """
    Load configured partitions or recent partitions within `lookback_days`.
    Returns an empty DataFrame if the table doesn't exist or has no data.
    """
    try:
        dt = DeltaTable(table_path, storage_options=storage_options)
        if partition_dates:
            filters = [("partition_date", "in", partition_dates)]
        else:
            cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
            filters = [("partition_date", ">=", cutoff)]
        df = dt.to_pandas(filters=filters)
        return df
    except Exception as exc:
        logger.warning(f"Could not load {table_path}: {exc}")
        return pd.DataFrame()


# ── Metrics ────────────────────────────────────────────────────────────────────


def _push_results(
    results: list[CheckResult],
    pushgateway_url: str,
) -> None:
    reg = CollectorRegistry()
    fail_counter = Counter(
        "pipeline_quality_check_failures_total",
        "Number of data quality gate failures",
        ["check_name", "layer"],
        registry=reg,
    )
    run_gauge = Gauge(
        "pipeline_last_quality_run_timestamp",
        "Unix timestamp of last quality check run",
        ["layer"],
        registry=reg,
    )

    for r in results:
        if not r.passed:
            fail_counter.labels(
                check_name=r.check_name,
                layer=r.layer,
            ).inc()

    layers = {r.layer for r in results}
    for layer in layers:
        run_gauge.labels(layer=layer).set(time.time())

    try:
        push_to_gateway(pushgateway_url, job="med_ops_quality", registry=reg)
    except Exception as exc:
        logger.warning(f"Pushgateway unreachable — metrics not sent: {exc}")


# ── Entry point ────────────────────────────────────────────────────────────────


def run() -> int:
    storage_opts = _storage_options()
    bronze_bucket = os.environ.get("MINIO_BUCKET_BRONZE", "bronze")
    silver_bucket = os.environ.get("MINIO_BUCKET_SILVER", "silver")
    pushgateway = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")
    lookback = int(os.environ.get("QUALITY_LOOKBACK_DAYS", "1"))
    partition_dates = parse_partition_dates(
        os.environ.get("QUALITY_PARTITION_DATES", "")
    )

    target_description = (
        f"partitions={partition_dates}" if partition_dates else f"lookback={lookback}d"
    )
    logger.info(f"Quality checks starting — {target_description}")
    t_start = time.monotonic()
    all_results: list[CheckResult] = []

    # ── Bronze sources ────────────────────────────────────────────────────────
    for source in sorted(VALID_SOURCES):
        path = f"s3://{bronze_bucket}/{source}/air_quality"
        df = load_recent_partition(
            path,
            storage_opts,
            lookback,
            partition_dates=partition_dates,
        )
        if df.empty:
            result = missing_data_result(
                "bronze",
                f"bronze/{source}/air_quality",
                required=source in REQUIRED_BRONZE_SOURCES,
                partition_dates=partition_dates,
            )
            all_results.append(result)
            log = logger.error if result.severity == "fail" else logger.warning
            log(f"[bronze/{source}] {result.detail}")
        else:
            logger.info(f"[bronze/{source}] Loaded {len(df)} rows.")
            all_results.extend(run_bronze_checks(df, source))

    # ── Silver ────────────────────────────────────────────────────────────────
    path = f"s3://{silver_bucket}/air_quality"
    df = load_recent_partition(
        path,
        storage_opts,
        lookback,
        partition_dates=partition_dates,
    )
    if df.empty:
        result = missing_data_result(
            "silver",
            "silver/air_quality",
            required=True,
            partition_dates=partition_dates,
        )
        all_results.append(result)
        logger.error(f"[silver] {result.detail}")
    else:
        logger.info(f"[silver] Loaded {len(df)} rows.")
        all_results.extend(run_silver_checks(df))

    # ── Report ────────────────────────────────────────────────────────────────
    _push_results(all_results, pushgateway)

    failures = [r for r in all_results if not r.passed and r.severity == "fail"]
    warnings = [r for r in all_results if not r.passed and r.severity == "warn"]
    passed = [r for r in all_results if r.passed]

    elapsed = time.monotonic() - t_start
    logger.info(
        f"Quality run complete in {elapsed:.1f}s — "
        f"{len(passed)} passed, {len(warnings)} warn, {len(failures)} failed"
    )

    for r in warnings:
        logger.warning(f"  WARN  [{r.layer}/{r.table}] {r.check_name}: {r.detail}")
    for r in failures:
        logger.error(f"  FAIL  [{r.layer}/{r.table}] {r.check_name}: {r.detail}")

    if failures:
        logger.error(f"{len(failures)} hard-fail check(s) breached — exiting non-zero.")
        return 1

    logger.success("All hard-fail checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
