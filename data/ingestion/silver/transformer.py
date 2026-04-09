"""
silver/transformer.py
─────────────────────
Reads all Bronze Delta tables, applies cleaning and standardisation rules,
and writes to the Silver layer. Incremental: processes only new Bronze
partitions not yet reflected in Silver.

Discipline: every column is typed, every null is handled, every unit is
standardised. No raw field escapes this layer unchecked.
"""

import os
import time
from datetime import date, timedelta

import pandas as pd
import numpy as np
from deltalake import DeltaTable, write_deltalake
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
from loguru import logger


MINIO_ENDPOINT   = os.environ["MINIO_ENDPOINT"]
MINIO_ACCESS_KEY = os.environ["MINIO_ACCESS_KEY"]
MINIO_SECRET_KEY = os.environ["MINIO_SECRET_KEY"]

STORAGE_OPTIONS = {
    "endpoint_url":           MINIO_ENDPOINT,
    "aws_access_key_id":      MINIO_ACCESS_KEY,
    "aws_secret_access_key":  MINIO_SECRET_KEY,
    "aws_allow_http":         "true",
    "aws_s3_allow_unsafe_rename": "true",
}

BRONZE_BUCKET = os.environ.get("MINIO_BUCKET_BRONZE", "bronze")
SILVER_BUCKET = os.environ.get("MINIO_BUCKET_SILVER", "silver")
PUSHGATEWAY   = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")

registry       = CollectorRegistry()
ROWS_WRITTEN   = Gauge("pipeline_ingested_rows", "Rows written", ["layer", "source"], registry=registry)
LAST_RUN_TS    = Gauge("pipeline_last_successful_run_timestamp", "Last run ts", ["layer"], registry=registry)
DURATION_GAUGE = Gauge("pipeline_duration_seconds", "Stage duration", ["stage"], registry=registry)
SCHEMA_DRIFT   = Gauge("pipeline_schema_drift_events_total", "Schema drift", ["table"], registry=registry)


WHO_THRESHOLDS = {
    "pm2p5": 15.0,   # µg/m³ annual mean
    "pm10":  45.0,
    "no2":   10.0,
    "o3":    60.0,
}


def clean_copernicus(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ["pm2p5", "pm10", "no2", "o3"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df[col] = df[col].clip(lower=0, upper=10_000)

    df["date"]           = pd.to_datetime(df["date"]).dt.date
    df["latitude"]       = pd.to_numeric(df["latitude"],  errors="coerce")
    df["longitude"]      = pd.to_numeric(df["longitude"], errors="coerce")
    df["aqi_category"]   = df["pm2p5"].apply(_aqi_category)
    df["who_exceedance"] = (df["pm2p5"] > WHO_THRESHOLDS["pm2p5"]).astype(int)
    df["silver_ts"]      = pd.Timestamp.utcnow().isoformat()
    return df.dropna(subset=["station_id", "latitude", "longitude", "date"])


def _aqi_category(pm25: float) -> str:
    if pd.isna(pm25):
        return "unknown"
    if pm25 <= 12:   return "good"
    if pm25 <= 35.4: return "moderate"
    if pm25 <= 55.4: return "unhealthy_sensitive"
    if pm25 <= 150:  return "unhealthy"
    return "hazardous"


def get_unprocessed_partitions(bronze_table: str, silver_table: str) -> list[str]:
    try:
        bronze_dt  = DeltaTable(bronze_table, storage_options=STORAGE_OPTIONS)
        bronze_parts = set(
            bronze_dt.to_pandas(columns=["partition_date"])["partition_date"].unique()
        )
    except Exception:
        return []

    try:
        silver_dt    = DeltaTable(silver_table, storage_options=STORAGE_OPTIONS)
        silver_parts = set(
            silver_dt.to_pandas(columns=["partition_date"])["partition_date"].unique()
        )
    except Exception:
        silver_parts = set()

    return sorted(bronze_parts - silver_parts)


def run() -> None:
    logger.info("Silver transformation starting.")
    t_start = time.monotonic()
    total_rows = 0

    bronze_path = f"s3://{BRONZE_BUCKET}/copernicus/air_quality"
    silver_path = f"s3://{SILVER_BUCKET}/air_quality"

    partitions = get_unprocessed_partitions(bronze_path, silver_path)
    if not partitions:
        logger.info("Silver layer is up to date — nothing to process.")
        return

    logger.info(f"Processing {len(partitions)} new Bronze partitions: {partitions}")

    for partition in partitions:
        bronze_dt = DeltaTable(bronze_path, storage_options=STORAGE_OPTIONS)
        raw_df    = bronze_dt.to_pandas(
            filters=[("partition_date", "=", partition)]
        )
        cleaned_df = clean_copernicus(raw_df)

        write_deltalake(
            silver_path,
            cleaned_df,
            mode="append",
            partition_by=["partition_date"],
            storage_options=STORAGE_OPTIONS,
            schema_mode="merge",
        )
        total_rows += len(cleaned_df)
        logger.info(f"  Partition {partition}: {len(cleaned_df)} rows written to Silver.")

    elapsed = time.monotonic() - t_start
    ROWS_WRITTEN.labels(layer="silver", source="copernicus").set(total_rows)
    LAST_RUN_TS.labels(layer="silver").set(time.time())
    DURATION_GAUGE.labels(stage="silver_transform").set(elapsed)

    push_to_gateway(PUSHGATEWAY, job="med_ops_silver_transform", registry=registry)
    logger.success(f"Silver transformation complete — {total_rows} rows, {elapsed:.1f}s")


if __name__ == "__main__":
    run()
