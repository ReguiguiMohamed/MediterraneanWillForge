"""
gold/marts.py
─────────────
Aggregates the Silver air quality data into analytics-ready Gold marts.
Two marts are produced:

  1. daily_country_summary   — daily mean/max per country + WHO exceedance %
  2. wildfire_risk_index     — composite risk indicator per station/day

These are the datasets consumers read. They must be correct, documented,
and fast to query. No shortcuts. No half-measures.
"""

import os
import time

import pandas as pd
from deltalake import DeltaTable, write_deltalake
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
from loguru import logger


MINIO_ENDPOINT   = os.environ["MINIO_ENDPOINT"]
MINIO_ACCESS_KEY = os.environ["MINIO_ACCESS_KEY"]
MINIO_SECRET_KEY = os.environ["MINIO_SECRET_KEY"]
SILVER_BUCKET    = os.environ.get("MINIO_BUCKET_SILVER", "silver")
GOLD_BUCKET      = os.environ.get("MINIO_BUCKET_GOLD",   "gold")
PUSHGATEWAY      = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")

STORAGE_OPTIONS = {
    "endpoint_url":           MINIO_ENDPOINT,
    "aws_access_key_id":      MINIO_ACCESS_KEY,
    "aws_secret_access_key":  MINIO_SECRET_KEY,
    "aws_allow_http":         "true",
    "aws_s3_allow_unsafe_rename": "true",
}

registry       = CollectorRegistry()
ROWS_WRITTEN   = Gauge("pipeline_ingested_rows", "Rows written", ["layer", "source"], registry=registry)
LAST_RUN_TS    = Gauge("pipeline_last_successful_run_timestamp", "Last run ts", ["layer"], registry=registry)
DURATION_GAUGE = Gauge("pipeline_duration_seconds", "Stage duration", ["stage"], registry=registry)


def build_daily_country_summary(silver_df: pd.DataFrame) -> pd.DataFrame:
    grp = silver_df.groupby(["partition_date", "aqi_category"])
    summary = silver_df.groupby("partition_date").agg(
        mean_pm25=("pm2p5", "mean"),
        max_pm25=("pm2p5", "max"),
        mean_pm10=("pm10", "mean"),
        mean_no2=("no2",  "mean"),
        mean_o3=("o3",    "mean"),
        station_count=("station_id", "nunique"),
        who_exceedance_pct=("who_exceedance", "mean"),
    ).reset_index()
    summary["mean_pm25"] = summary["mean_pm25"].round(2)
    summary["who_exceedance_pct"] = (summary["who_exceedance_pct"] * 100).round(1)
    summary["gold_ts"] = pd.Timestamp.utcnow().isoformat()
    return summary


def build_wildfire_risk_index(silver_df: pd.DataFrame) -> pd.DataFrame:
    """
    Simple composite risk: normalised O3 + PM2.5 combination.
    Scaled 0–100. Purely indicative — intended to demonstrate
    multi-metric aggregation, not as a production wildfire model.
    """
    df = silver_df.copy()
    df["o3_norm"]    = (df["o3"]    / 180).clip(0, 1)
    df["pm25_norm"]  = (df["pm2p5"] / 150).clip(0, 1)
    df["risk_index"] = ((df["o3_norm"] * 0.6 + df["pm25_norm"] * 0.4) * 100).round(1)
    df["risk_level"] = pd.cut(
        df["risk_index"],
        bins=[0, 25, 50, 75, 100],
        labels=["low", "moderate", "high", "extreme"],
    ).astype(str)
    risk = df[["partition_date", "station_id", "latitude", "longitude",
               "risk_index", "risk_level"]].copy()
    risk["gold_ts"] = pd.Timestamp.utcnow().isoformat()
    return risk


def run() -> None:
    logger.info("Gold mart build starting.")
    t_start = time.monotonic()

    silver_path = f"s3://{SILVER_BUCKET}/air_quality"
    try:
        silver_dt = DeltaTable(silver_path, storage_options=STORAGE_OPTIONS)
        silver_df = silver_dt.to_pandas()
    except Exception as exc:
        logger.error(f"Cannot read Silver layer: {exc}")
        return

    if silver_df.empty:
        logger.warning("Silver layer is empty — nothing to mart.")
        return

    summary = build_daily_country_summary(silver_df)
    write_deltalake(
        f"s3://{GOLD_BUCKET}/daily_country_summary",
        summary,
        mode="overwrite",
        storage_options=STORAGE_OPTIONS,
        schema_mode="overwrite",
    )
    logger.info(f"Gold daily_country_summary: {len(summary)} rows written.")

    risk = build_wildfire_risk_index(silver_df)
    write_deltalake(
        f"s3://{GOLD_BUCKET}/wildfire_risk_index",
        risk,
        mode="overwrite",
        storage_options=STORAGE_OPTIONS,
        schema_mode="overwrite",
    )
    logger.info(f"Gold wildfire_risk_index: {len(risk)} rows written.")

    total_rows = len(summary) + len(risk)
    elapsed    = time.monotonic() - t_start

    ROWS_WRITTEN.labels(layer="gold", source="all").set(total_rows)
    LAST_RUN_TS.labels(layer="gold").set(time.time())
    DURATION_GAUGE.labels(stage="gold_mart").set(elapsed)

    push_to_gateway(PUSHGATEWAY, job="med_ops_gold_mart", registry=registry)
    logger.success(f"Gold marts complete — {total_rows} total rows, {elapsed:.1f}s")


if __name__ == "__main__":
    run()
