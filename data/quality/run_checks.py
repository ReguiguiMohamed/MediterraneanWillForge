"""
data/quality/run_checks.py
───────────────────────────
Data quality runner for Bronze and Silver Delta Lake tables.

Reads the most recent partition of each table, executes a suite of
named checks, logs results, pushes failure counts to Prometheus
Pushgateway, and exits non-zero if any hard-fail threshold is breached.

Called by the quality Docker image:
  python -m quality.run_checks

Environment variables required:
  MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY
  PROMETHEUS_PUSHGATEWAY_URL  (default: http://localhost:9091)
  MINIO_BUCKET_BRONZE         (default: bronze)
  MINIO_BUCKET_SILVER         (default: silver)
  QUALITY_LOOKBACK_DAYS       (default: 1  — how many past days to check)
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from deltalake import DeltaTable
from loguru import logger
from prometheus_client import CollectorRegistry, Counter, Gauge, push_to_gateway

from data.storage import delta_storage_options

# ── Storage config ─────────────────────────────────────────────────────────────


def _storage_options() -> dict[str, str]:
    return delta_storage_options()


# ── Check result ───────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    check_name: str
    layer: str
    table: str
    passed: bool
    severity: str  # "warn" | "fail"
    detail: str = ""


# ── Individual check functions ─────────────────────────────────────────────────


def check_row_count(
    df: pd.DataFrame,
    layer: str,
    table: str,
    min_rows: int = 1,
) -> CheckResult:
    passed = len(df) >= min_rows
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
) -> list[CheckResult]:
    results = []
    for col in required:
        present = col in df.columns
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
    null_rate = df[col].isna().mean()
    if null_rate >= fail_threshold:
        return CheckResult(
            check_name=f"null_rate_{col}",
            layer=layer,
            table=table,
            passed=False,
            severity="fail",
            detail=f"{null_rate:.1%} nulls (fail threshold {fail_threshold:.0%})",
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
    mask = pd.Series([True] * len(series), index=series.index)
    if min_val is not None:
        mask &= series >= min_val
    if max_val is not None:
        mask &= series <= max_val
    compliance = mask.mean()
    passed = compliance >= mostly
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
    invalid = df[col].dropna()[~df[col].dropna().isin(valid_set)]
    passed = len(invalid) == 0
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
    below = (df[col] < floor).sum()
    passed = below == 0
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

    results.append(check_row_count(df, layer, table))
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
        )
    )
    for col in ("pm2_5", "pm10", "nitrogen_dioxide", "ozone"):
        results.append(
            check_null_rate(
                df, layer, table, col, warn_threshold=0.40, fail_threshold=0.80
            )
        )
        results.append(check_value_range(df, layer, table, col, min_val=0))

    results.append(
        check_valid_values(
            df,
            layer,
            table,
            "source",
            valid_set={"openmeteo", "openaq"},
        )
    )
    return results


def run_silver_checks(df: pd.DataFrame) -> list[CheckResult]:
    layer = "silver"
    table = "silver/air_quality"
    results: list[CheckResult] = []

    results.append(check_row_count(df, layer, table))
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
        )
    )
    for col in ("pm2_5", "pm10", "nitrogen_dioxide", "ozone"):
        results.append(
            check_null_rate(
                df, layer, table, col, warn_threshold=0.30, fail_threshold=0.70
            )
        )
        results.append(check_value_range(df, layer, table, col, min_val=0))

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
        )
    )
    results.append(
        check_valid_values(
            df,
            layer,
            table,
            "source",
            valid_set={"openmeteo", "openaq"},
        )
    )
    results.append(check_data_completeness_floor(df, layer, table))
    return results


# ── Table loader ───────────────────────────────────────────────────────────────


def load_recent_partition(
    table_path: str,
    storage_options: dict[str, str],
    lookback_days: int = 1,
) -> pd.DataFrame:
    """
    Load the most recent available partition, going back up to `lookback_days`.
    Returns an empty DataFrame if the table doesn't exist or has no data.
    """
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    try:
        dt = DeltaTable(table_path, storage_options=storage_options)
        df = dt.to_pandas(filters=[("partition_date", ">=", cutoff)])
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

    logger.info(f"Quality checks starting — lookback={lookback}d")
    t_start = time.monotonic()
    all_results: list[CheckResult] = []

    # ── Bronze: openmeteo ─────────────────────────────────────────────────────
    path = f"s3://{bronze_bucket}/openmeteo/air_quality"
    df = load_recent_partition(path, storage_opts, lookback)
    if df.empty:
        logger.warning("[bronze/openmeteo] No recent data found — skipping checks.")
    else:
        logger.info(f"[bronze/openmeteo] Loaded {len(df)} rows.")
        all_results.extend(run_bronze_checks(df, "openmeteo"))

    # ── Bronze: openaq ────────────────────────────────────────────────────────
    path = f"s3://{bronze_bucket}/openaq/air_quality"
    df = load_recent_partition(path, storage_opts, lookback)
    if df.empty:
        logger.warning("[bronze/openaq] No recent data found — skipping checks.")
    else:
        logger.info(f"[bronze/openaq] Loaded {len(df)} rows.")
        all_results.extend(run_bronze_checks(df, "openaq"))

    # ── Silver ────────────────────────────────────────────────────────────────
    path = f"s3://{silver_bucket}/air_quality"
    df = load_recent_partition(path, storage_opts, lookback)
    if df.empty:
        logger.warning("[silver] No recent data found — skipping checks.")
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
