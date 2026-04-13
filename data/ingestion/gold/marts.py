"""
data/ingestion/gold/marts.py
─────────────────────────────
Silver → Gold aggregation layer.

Two analytics-ready marts are produced on each run:

  1. daily_country_summary
     Daily mean / max pollutant concentrations, station count, and WHO
     exceedance rate, grouped by country and date.

  2. wildfire_risk_index
     Composite risk score (0–100) per station per day.
     Formula: 0.6 × normalised O3  +  0.4 × normalised PM2.5.
     Intended to demonstrate multi-metric aggregation; not a production
     fire-danger forecast.

Gold tables are written with mode=overwrite — they are always rebuilt
from the full Silver layer so consumers always get a consistent snapshot.
"""

from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd
from deltalake import DeltaTable, write_deltalake
from loguru import logger
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

# ── Config ─────────────────────────────────────────────────────────────────────


def _storage_options() -> dict[str, str]:
    return {
        "endpoint_url": os.environ["MINIO_ENDPOINT"],
        "aws_access_key_id": os.environ["MINIO_ACCESS_KEY"],
        "aws_secret_access_key": os.environ["MINIO_SECRET_KEY"],
        "aws_allow_http": "true",
        "aws_s3_allow_unsafe_rename": "true",
    }


# ── Mart builders ──────────────────────────────────────────────────────────────


def build_daily_country_summary(silver_df: pd.DataFrame) -> pd.DataFrame:
    """
    Daily mean / max / WHO exceedance per country.

    Groups by (partition_date, country_code, source) so downstream consumers
    can compare station-based observations (openaq) against gridded model
    output (openmeteo) side by side.
    """
    group_cols = ["partition_date", "country_code", "source"]

    # openmeteo rows may not have a country_code — derive from station_id suffix
    silver_df = silver_df.copy()
    if "country_code" in silver_df.columns:
        # Fill blanks from station_id where possible (e.g. "tunis_tn" → "TN")
        mask = silver_df["country_code"].isna() & silver_df["station_id"].notna()
        silver_df.loc[mask, "country_code"] = (
            silver_df.loc[mask, "station_id"].str.split("_").str[-1].str.upper()
        )
    else:
        silver_df["country_code"] = None

    summary = (
        silver_df.groupby(group_cols, dropna=False)
        .agg(
            mean_pm2_5=("pm2_5", "mean"),
            max_pm2_5=("pm2_5", "max"),
            mean_pm10=("pm10", "mean"),
            mean_no2=("nitrogen_dioxide", "mean"),
            mean_o3=("ozone", "mean"),
            station_count=("station_id", "nunique"),
            who_pm25_exceed_pct=("who_pm25_exceed", "mean"),
            who_pm10_exceed_pct=("who_pm10_exceed", "mean"),
            who_no2_exceed_pct=("who_no2_exceed", "mean"),
            who_o3_exceed_pct=("who_o3_exceed", "mean"),
        )
        .reset_index()
    )

    # Convert mean exceedance fractions → percentages
    for col in (
        "who_pm25_exceed_pct",
        "who_pm10_exceed_pct",
        "who_no2_exceed_pct",
        "who_o3_exceed_pct",
    ):
        summary[col] = (summary[col] * 100).round(1)

    for col in ("mean_pm2_5", "max_pm2_5", "mean_pm10", "mean_no2", "mean_o3"):
        summary[col] = summary[col].round(2)

    summary["gold_ts"] = pd.Timestamp.utcnow().isoformat()
    return summary


def build_wildfire_risk_index(silver_df: pd.DataFrame) -> pd.DataFrame:
    """
    Composite wildfire risk score per station × day.

    Risk = 60% normalised O3 contribution  +  40% normalised PM2.5 contribution,
    scaled to 0–100.  Uses 180 µg/m³ as O3 upper reference and 150 µg/m³ for PM2.5.
    """
    df = silver_df.copy()

    o3_ref = 180.0
    pm25_ref = 150.0

    df["o3_norm"] = (df["ozone"].fillna(0) / o3_ref).clip(0, 1)
    df["pm25_norm"] = (df["pm2_5"].fillna(0) / pm25_ref).clip(0, 1)
    df["risk_index"] = ((df["o3_norm"] * 0.6 + df["pm25_norm"] * 0.4) * 100).round(1)

    df["risk_level"] = pd.cut(
        df["risk_index"],
        bins=[-np.inf, 25, 50, 75, np.inf],
        labels=["low", "moderate", "high", "extreme"],
    ).astype(str)

    keep = [
        "partition_date",
        "source",
        "station_id",
        "station_name",
        "country_code",
        "latitude",
        "longitude",
        "pm2_5",
        "ozone",
        "risk_index",
        "risk_level",
    ]
    # Only keep columns that actually exist
    keep = [c for c in keep if c in df.columns]
    risk = df[keep].copy()
    risk["gold_ts"] = pd.Timestamp.utcnow().isoformat()
    return risk


# ── Entry point ────────────────────────────────────────────────────────────────


def run() -> None:
    storage_opts = _storage_options()
    silver_bucket = os.environ.get("MINIO_BUCKET_SILVER", "silver")
    gold_bucket = os.environ.get("MINIO_BUCKET_GOLD", "gold")
    pushgateway = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")

    reg = CollectorRegistry()
    rows_gauge = Gauge(
        "pipeline_ingested_rows", "Rows written", ["layer", "source"], registry=reg
    )
    fresh_gauge = Gauge(
        "pipeline_last_successful_run_timestamp",
        "Last run timestamp",
        ["layer"],
        registry=reg,
    )
    dur_gauge = Gauge(
        "pipeline_duration_seconds", "Stage duration", ["stage"], registry=reg
    )

    logger.info("Gold mart build starting.")
    t_start = time.monotonic()

    silver_path = f"s3://{silver_bucket}/air_quality"
    try:
        silver_dt = DeltaTable(silver_path, storage_options=storage_opts)
        silver_df = silver_dt.to_pandas()
    except Exception as exc:
        logger.error(f"Cannot read Silver layer: {exc}")
        return

    if silver_df.empty:
        logger.warning("Silver layer is empty — nothing to mart.")
        return

    logger.info(
        f"Silver snapshot: {len(silver_df)} rows across {silver_df['partition_date'].nunique()} dates."
    )

    # ── daily_country_summary ─────────────────────────────────────────────────
    summary = build_daily_country_summary(silver_df)
    write_deltalake(
        f"s3://{gold_bucket}/daily_country_summary",
        summary,
        mode="overwrite",
        storage_options=storage_opts,
        schema_mode="overwrite",
    )
    logger.info(f"Gold daily_country_summary: {len(summary)} rows written.")

    # ── wildfire_risk_index ───────────────────────────────────────────────────
    risk = build_wildfire_risk_index(silver_df)
    write_deltalake(
        f"s3://{gold_bucket}/wildfire_risk_index",
        risk,
        mode="overwrite",
        storage_options=storage_opts,
        schema_mode="overwrite",
    )
    logger.info(f"Gold wildfire_risk_index: {len(risk)} rows written.")

    total_rows = len(summary) + len(risk)
    elapsed = time.monotonic() - t_start

    rows_gauge.labels(layer="gold", source="all").set(total_rows)
    fresh_gauge.labels(layer="gold").set(time.time())
    dur_gauge.labels(stage="gold_mart").set(elapsed)

    push_to_gateway(pushgateway, job="med_ops_gold_mart", registry=reg)
    logger.success(f"Gold marts complete — {total_rows} total rows, {elapsed:.1f}s")


if __name__ == "__main__":
    run()
