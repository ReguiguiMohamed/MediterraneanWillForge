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
from deltalake.exceptions import TableNotFoundError
from loguru import logger
from prometheus_client import CollectorRegistry, Counter, Gauge, push_to_gateway

from data.metrics import push_to_grafana
from data.storage import delta_storage_options

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

_SILVER_COLUMNS = [
    "station_id",
    "station_name",
    "city",
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
    "who_pm10_exceed",
    "who_no2_exceed",
    "who_o3_exceed",
    "data_completeness",
    "source",
    "silver_ts",
    "partition_date",
]

_STRING_COLUMNS = [
    "station_id",
    "station_name",
    "city",
    "country_code",
    "date",
    "aqi_category",
    "source",
    "silver_ts",
    "partition_date",
]

_FLOAT_COLUMNS = [
    "latitude",
    "longitude",
    "pm2_5",
    "pm10",
    "nitrogen_dioxide",
    "ozone",
    "data_completeness",
]

_INT_COLUMNS = [
    "who_pm25_exceed",
    "who_pm10_exceed",
    "who_no2_exceed",
    "who_o3_exceed",
]


# ── Config ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SilverConfig:
    bronze_bucket: str
    silver_bucket: str
    pushgateway_url: str
    storage_options: dict[str, str]

    @classmethod
    def from_env(cls) -> SilverConfig:
        return cls(
            bronze_bucket=os.environ.get("MINIO_BUCKET_BRONZE", "bronze"),
            silver_bucket=os.environ.get("MINIO_BUCKET_SILVER", "silver"),
            pushgateway_url=os.environ.get(
                "PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091"
            ),
            storage_options=delta_storage_options(),
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


def _canonicalize_for_delta(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return Silver rows in canonical column order with Delta-safe dtypes.

    delta-rs rejects Arrow Null columns. Open-Meteo has no city field, so the
    city column is intentionally all-null for that source; keep it as a string
    column with null values instead of letting pyarrow infer the Null type.
    """
    df = df.copy()

    for col in _SILVER_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    for col in _STRING_COLUMNS:
        df[col] = df[col].astype("string")

    for col in _FLOAT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    for col in _INT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("int64")

    return df[_SILVER_COLUMNS]


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


def _dates_from_files(files: list[str]) -> set[str]:
    """
    Extract partition_date values by parsing Delta file paths.

    With engine="rust", partition columns are excluded from Parquet data files
    and live only in the directory path (e.g. partition_date=2024-01-15/…).
    Reading them via to_pandas()["partition_date"] fails silently on
    deltalake 0.18.x because the column is absent from the Parquet schema.
    Parsing dt.files() is immune to that problem.
    """
    dates: set[str] = set()
    for path in files:
        for segment in path.replace("\\", "/").split("/"):
            if segment.startswith("partition_date="):
                dates.add(segment.split("=", 1)[1])
                break
    return dates


def _unprocessed_partitions(
    bronze_path: str,
    silver_path: str,
    source: str,
    storage_options: dict[str, str],
) -> list[str]:
    """
    Return Bronze partition dates not yet present in Silver for this source.

    Uses dt.files() path-parsing instead of to_pandas()["partition_date"].
    With engine="rust", partition columns are excluded from Parquet data files,
    so any attempt to read them via column projection or DataFrame indexing
    raises a silent error that makes the transformer skip all partitions.
    """
    try:
        bronze_dt = DeltaTable(bronze_path, storage_options=storage_options)
        bronze_dates = _dates_from_files(bronze_dt.files())
    except TableNotFoundError:
        logger.warning(
            f"[{source}] Bronze table not found at {bronze_path} — nothing to process."
        )
        return []

    if not bronze_dates:
        logger.warning(
            f"[{source}] Bronze table exists but contains no partition_date files: {bronze_path}"
        )
        return []

    try:
        silver_dt = DeltaTable(silver_path, storage_options=storage_options)
        silver_source_files = [f for f in silver_dt.files() if f"source={source}" in f]
        silver_dates = _dates_from_files(silver_source_files)
    except TableNotFoundError:
        silver_dates = set()

    return sorted(bronze_dates - silver_dates)


# ── Metrics ────────────────────────────────────────────────────────────────────


def _build_metrics() -> tuple[CollectorRegistry, Gauge, Gauge, Gauge, Counter]:
    reg = CollectorRegistry()
    rows = Gauge(
        "med_ops_silver_rows",
        "Total rows written to Silver this run",
        [],
        registry=reg,
    )
    fresh = Gauge(
        "med_ops_silver_last_run_ts",
        "Unix timestamp of last successful Silver run",
        [],
        registry=reg,
    )
    dur = Gauge(
        "med_ops_silver_duration_seconds",
        "Wall-clock seconds for Silver transform",
        [],
        registry=reg,
    )
    qual = Counter(
        "med_ops_silver_quality_failures_total",
        "Data quality gate failures",
        [],
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
    failures: list[str] = []

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

        # Open Bronze once — each DeltaTable() call reads the _delta_log/ from B2
        # (Class B transactions). One open per source covers all partitions.
        bronze_dt = DeltaTable(bronze_path, storage_options=cfg.storage_options)

        # Collect all transformed frames before writing — then one write_deltalake()
        # call per source instead of one per partition. write_deltalake() opens the
        # Silver Delta log internally on every call; batching cuts N log-reads to 1.
        batch_frames: list[pd.DataFrame] = []
        batch_partitions: list[str] = []

        for partition in partitions:
            try:
                raw_df = bronze_dt.to_pandas(
                    filters=[("partition_date", "=", partition)]
                )
                # engine="rust" excludes partition columns from Parquet data
                # files; they live only in the directory path.  If to_pandas()
                # doesn't reconstruct them, add partition_date back manually so
                # write_deltalake can use it for Silver partitioning.
                if "partition_date" not in raw_df.columns:
                    raw_df = raw_df.copy()
                    raw_df["partition_date"] = partition

                cleaned = clean(raw_df, source)
                enriched = enrich(cleaned)

                if enriched.empty:
                    logger.warning(
                        f"[{source}] Partition {partition} — 0 rows after cleaning, skipping write."
                    )
                    continue

                enriched = _canonicalize_for_delta(enriched)
                batch_frames.append(enriched)
                batch_partitions.append(partition)
                logger.info(
                    f"[{source}] Partition {partition}: {len(enriched)} rows prepared."
                )

            except Exception as exc:
                failures.append(f"{source}/{partition}: {exc}")
                logger.error(f"[{source}] Partition {partition} failed: {exc}")

        source_rows = 0
        if batch_frames:
            try:
                batch_df = pd.concat(batch_frames, ignore_index=True)
                write_deltalake(
                    silver_path,
                    batch_df,
                    mode="append",
                    engine="rust",
                    partition_by=["partition_date", "source"],
                    storage_options=cfg.storage_options,
                    schema_mode="merge",
                )
                try:
                    DeltaTable(
                        silver_path, storage_options=cfg.storage_options
                    ).create_checkpoint()
                except Exception as exc:
                    logger.warning(
                        f"[{source}] Silver Delta checkpoint failed (non-fatal): {exc}"
                    )
                source_rows = len(batch_df)
                total_rows += source_rows
                logger.info(
                    f"[{source}] Wrote {source_rows} rows "
                    f"({len(batch_frames)} partition(s)) → Silver."
                )
            except Exception as exc:
                for p in batch_partitions:
                    failures.append(f"{source}/{p}: batch write failed — {exc}")
                logger.error(f"[{source}] Batch write failed: {exc}")

    rows_gauge.set(total_rows)
    elapsed = time.monotonic() - t_start
    fresh_gauge.set(time.time())
    dur_gauge.set(elapsed)

    try:
        push_to_gateway(
            cfg.pushgateway_url, job="med_ops_silver_transform", registry=reg
        )
    except Exception as exc:
        logger.warning(f"Pushgateway push failed (best-effort): {exc}")
    push_to_grafana(reg, job="med_ops_silver_transform")

    if failures:
        raise RuntimeError(
            "Silver transformation failed for partition(s): " + "; ".join(failures)
        )

    logger.success(
        f"Silver transformation complete — {total_rows} rows across all sources, {elapsed:.1f}s"
    )


if __name__ == "__main__":
    run()
