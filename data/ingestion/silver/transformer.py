"""
data/ingestion/silver/transformer.py
─────────────────────────────────────
Bronze → Silver transformation layer.

Reads raw partitions from both Bronze sources (openmeteo + openaq),
applies cleaning, type enforcement, WHO 2021 threshold flagging, AQI
categorisation, and data-completeness scoring, then writes the unified
canonical Silver table partitioned by (partition_date, source).

Incremental: only partitions absent from Silver are processed each run.
Idempotent: re-running for an already-written partition is a no-op.

Silver canonical schema
───────────────────────
station_id         str   — unique station / grid-point identifier
station_name       str   — human-readable name
city               str   — city name (null for grid cells)
country_code       str   — ISO-3166-1 alpha-2
latitude           float — WGS-84
longitude          float — WGS-84
date               str   — YYYY-MM-DD observation date
pm2_5              float — µg/m³ daily mean
pm10               float — µg/m³ daily mean
nitrogen_dioxide   float — µg/m³ daily mean
ozone              float — µg/m³ daily mean
aqi_category       str   — US EPA PM2.5 breakpoints
who_pm25_exceed    int   — 1 if PM2.5 > 15 µg/m³ (WHO 2021)
who_pm10_exceed    int   — 1 if PM10  > 45 µg/m³
who_no2_exceed     int   — 1 if NO2   > 25 µg/m³
who_o3_exceed      int   — 1 if O3    > 100 µg/m³
data_completeness  float — fraction of the 4 pollutant columns that are non-null
source             str   — openmeteo | openaq
silver_ts          str   — UTC ISO-8601 write timestamp
partition_date     str   — YYYY-MM-DD (Delta partition key)
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import timezone

import numpy as np
import pandas as pd
from deltalake import DeltaTable, write_deltalake
from loguru import logger
from prometheus_client import CollectorRegistry, Counter, Gauge, push_to_gateway

# ── WHO 2021 24-hour guideline thresholds (µg/m³) ─────────────────────────────
WHO = {
    "pm2_5": 15.0,
    "pm10": 45.0,
    "nitrogen_dioxide": 25.0,
    "ozone": 100.0,
}

# Physical upper bounds for clip — values beyond these are instrument errors
_CLIP_MAX = {
    "pm2_5": 1_000.0,
    "pm10": 2_000.0,
    "nitrogen_dioxide": 500.0,
    "ozone": 500.0,
}

_POLLUTANTS = list(WHO.keys())

# Stations with fewer than this fraction of valid pollutant values are dropped
_MIN_COMPLETENESS = 0.20


# ── Config ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SilverConfig:
    bronze_bucket: str
    silver_bucket: str
    pushgateway_url: str
    storage_options: dict[str, str]

    @classmethod
    def from_env(cls) -> SilverConfig:
        endpoint = os.environ["MINIO_ENDPOINT"]
        access_key = os.environ["MINIO_ACCESS_KEY"]
        secret_key = os.environ["MINIO_SECRET_KEY"]
        return cls(
            bronze_bucket=os.environ.get("MINIO_BUCKET_BRONZE", "bronze"),
            silver_bucket=os.environ.get("MINIO_BUCKET_SILVER", "silver"),
            pushgateway_url=os.environ.get(
                "PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091"
            ),
            storage_options={
                "endpoint_url": endpoint,
                "aws_access_key_id": access_key,
                "aws_secret_access_key": secret_key,
                "aws_allow_http": "true",
                "aws_s3_allow_unsafe_rename": "true",
            },
        )


# ── Pure cleaning / enrichment functions ──────────────────────────────────────


def clean(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """
    Type-enforce, clip, and standardise a raw Bronze DataFrame.

    Accepts both openmeteo and openaq DataFrames (same column names after
    Bronze normalisation). Returns a copy — never mutates the input.
    """
    df = df.copy()

    # ── Numeric enforcement + physical clip ───────────────────────────────────
    for col in _POLLUTANTS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").clip(
                lower=0, upper=_CLIP_MAX[col]
            )
        else:
            df[col] = np.nan

    # ── Coordinate enforcement ────────────────────────────────────────────────
    df["latitude"] = pd.to_numeric(df.get("latitude"), errors="coerce")
    df["longitude"] = pd.to_numeric(df.get("longitude"), errors="coerce")

    # ── Normalise date to string YYYY-MM-DD ───────────────────────────────────
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")

    # ── Ensure optional columns exist ─────────────────────────────────────────
    for col in ("city", "country_code", "station_name"):
        if col not in df.columns:
            df[col] = None
    df["source"] = source

    # ── Drop rows without minimum viable identity ─────────────────────────────
    df = df.dropna(subset=["station_id", "latitude", "longitude", "date"])
    df = df[df["latitude"].between(-90, 90) & df["longitude"].between(-180, 180)]

    return df.reset_index(drop=True)


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add AQI category, WHO exceedance flags, and data-completeness score.
    Drops stations that are too sparse to be useful.
    """
    df = df.copy()

    # ── Data completeness (fraction of non-null pollutants per row) ───────────
    present = df[_POLLUTANTS].notna().sum(axis=1)
    df["data_completeness"] = (present / len(_POLLUTANTS)).round(4)
    df = df[df["data_completeness"] >= _MIN_COMPLETENESS].reset_index(drop=True)

    # ── AQI category from PM2.5 (US EPA breakpoints) ──────────────────────────
    df["aqi_category"] = df["pm2_5"].apply(_aqi_category)

    # ── WHO 2021 exceedance flags ─────────────────────────────────────────────
    df["who_pm25_exceed"] = _exceed_flag(df, "pm2_5", WHO["pm2_5"])
    df["who_pm10_exceed"] = _exceed_flag(df, "pm10", WHO["pm10"])
    df["who_no2_exceed"] = _exceed_flag(df, "nitrogen_dioxide", WHO["nitrogen_dioxide"])
    df["who_o3_exceed"] = _exceed_flag(df, "ozone", WHO["ozone"])

    # ── Write timestamp ───────────────────────────────────────────────────────
    df["silver_ts"] = pd.Timestamp.now(tz=timezone.utc).isoformat()

    return df


def _aqi_category(pm25: float | None) -> str:
    """US EPA PM2.5 AQI breakpoints."""
    if pm25 is None or (isinstance(pm25, float) and np.isnan(pm25)):
        return "unknown"
    if pm25 <= 12.0:
        return "good"
    if pm25 <= 35.4:
        return "moderate"
    if pm25 <= 55.4:
        return "unhealthy_sensitive"
    if pm25 <= 150.4:
        return "unhealthy"
    if pm25 <= 250.4:
        return "very_unhealthy"
    return "hazardous"


def _exceed_flag(df: pd.DataFrame, col: str, threshold: float) -> pd.Series:
    return (df[col].fillna(0) > threshold).astype(int)


# ── Incremental partition management ──────────────────────────────────────────


def _unprocessed_partitions(
    bronze_path: str,
    silver_path: str,
    source: str,
    storage_options: dict[str, str],
) -> list[str]:
    """
    Return Bronze partition dates not yet present in Silver for this source.
    """
    try:
        bronze_dt = DeltaTable(bronze_path, storage_options=storage_options)
        bronze_dates = set(
            bronze_dt.to_pandas(columns=["partition_date"])["partition_date"].unique()
        )
    except Exception:
        return []

    try:
        silver_dt = DeltaTable(silver_path, storage_options=storage_options)
        silver_df = silver_dt.to_pandas(
            columns=["partition_date", "source"],
            filters=[("source", "=", source)],
        )
        silver_dates = set(silver_df["partition_date"].unique())
    except Exception:
        silver_dates = set()

    return sorted(bronze_dates - silver_dates)


# ── Metrics ────────────────────────────────────────────────────────────────────


def _build_metrics() -> tuple[CollectorRegistry, Gauge, Gauge, Gauge, Counter]:
    reg = CollectorRegistry()
    rows = Gauge(
        "pipeline_ingested_rows",
        "Rows written in this run",
        ["layer", "source"],
        registry=reg,
    )
    fresh = Gauge(
        "pipeline_last_successful_run_timestamp",
        "Unix timestamp of last successful run",
        ["layer"],
        registry=reg,
    )
    dur = Gauge(
        "pipeline_duration_seconds",
        "Wall-clock seconds per pipeline stage",
        ["stage"],
        registry=reg,
    )
    qual = Counter(
        "pipeline_quality_check_failures_total",
        "Data quality gate failures",
        ["check_name", "layer"],
        registry=reg,
    )
    return reg, rows, fresh, dur, qual


# ── Entry point ────────────────────────────────────────────────────────────────


def run() -> None:
    cfg = SilverConfig.from_env()
    reg, rows_gauge, fresh_gauge, dur_gauge, _ = _build_metrics()

    silver_path = f"s3://{cfg.silver_bucket}/air_quality"

    sources = [
        ("openmeteo", f"s3://{cfg.bronze_bucket}/openmeteo/air_quality"),
        ("openaq", f"s3://{cfg.bronze_bucket}/openaq/air_quality"),
    ]

    logger.info("Silver transformation starting.")
    t_start = time.monotonic()
    total_rows = 0

    for source, bronze_path in sources:
        partitions = _unprocessed_partitions(
            bronze_path, silver_path, source, cfg.storage_options
        )

        if not partitions:
            logger.info(f"[{source}] Silver up to date — nothing to process.")
            continue

        logger.info(
            f"[{source}] Processing {len(partitions)} new partition(s): {partitions}"
        )

        for partition in partitions:
            try:
                bronze_dt = DeltaTable(bronze_path, storage_options=cfg.storage_options)
                raw_df = bronze_dt.to_pandas(
                    filters=[("partition_date", "=", partition)]
                )

                cleaned = clean(raw_df, source)
                enriched = enrich(cleaned)

                if enriched.empty:
                    logger.warning(
                        f"[{source}] Partition {partition} — 0 rows after cleaning, skipping write."
                    )
                    continue

                write_deltalake(
                    silver_path,
                    enriched,
                    mode="append",
                    partition_by=["partition_date", "source"],
                    storage_options=cfg.storage_options,
                    schema_mode="merge",
                )
                total_rows += len(enriched)
                logger.info(
                    f"[{source}] Partition {partition}: {len(enriched)} rows → Silver."
                )

            except Exception as exc:
                logger.error(f"[{source}] Partition {partition} failed: {exc}")

        rows_gauge.labels(layer="silver", source=source).set(total_rows)

    elapsed = time.monotonic() - t_start
    fresh_gauge.labels(layer="silver").set(time.time())
    dur_gauge.labels(stage="silver_transform").set(elapsed)

    push_to_gateway(cfg.pushgateway_url, job="med_ops_silver_transform", registry=reg)
    logger.success(
        f"Silver transformation complete — {total_rows} rows across all sources, {elapsed:.1f}s"
    )


if __name__ == "__main__":
    run()
