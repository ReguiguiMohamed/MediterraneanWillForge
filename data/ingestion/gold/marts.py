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
     This is an air-quality indicator, not a fire-danger forecast.

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

from data.metrics import push_to_grafana
from data.storage import delta_storage_options, read_delta

# ── Config ─────────────────────────────────────────────────────────────────────


def _storage_options() -> dict[str, str]:
    return delta_storage_options()


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
        "med_ops_gold_mart_rows", "Total rows written to Gold mart", [], registry=reg
    )
    fresh_gauge = Gauge(
        "med_ops_gold_mart_last_run_ts",
        "Unix timestamp of last successful Gold mart run",
        [],
        registry=reg,
    )
    dur_gauge = Gauge(
        "med_ops_gold_mart_duration_seconds",
        "Wall-clock seconds for Gold mart",
        [],
        registry=reg,
    )

    logger.info("Gold mart build starting.")
    t_start = time.monotonic()

    silver_path = f"s3://{silver_bucket}/air_quality"
    try:
        silver_df = read_delta(silver_path, storage_opts)
    except Exception as exc:
        raise RuntimeError(f"Cannot read Silver layer: {exc}") from exc

    if silver_df.empty:
        raise RuntimeError("Silver layer is empty — Gold marts cannot be produced.")

    logger.info(
        f"Silver snapshot: {len(silver_df)} rows across {silver_df['partition_date'].nunique()} dates."
    )

    # ── daily_country_summary ─────────────────────────────────────────────────
    summary = build_daily_country_summary(silver_df)
    _summary_path = f"s3://{gold_bucket}/daily_country_summary"
    write_deltalake(
        _summary_path,
        summary,
        mode="overwrite",
        engine="rust",
        storage_options=storage_opts,
        schema_mode="overwrite",
    )
    try:
        DeltaTable(_summary_path, storage_options=storage_opts).create_checkpoint()
    except Exception as exc:
        logger.warning(
            f"Gold daily_country_summary checkpoint failed (non-fatal): {exc}"
        )
    logger.info(f"Gold daily_country_summary: {len(summary)} rows written.")

    # ── wildfire_risk_index ───────────────────────────────────────────────────
    risk = build_wildfire_risk_index(silver_df)
    _risk_path = f"s3://{gold_bucket}/wildfire_risk_index"
    write_deltalake(
        _risk_path,
        risk,
        mode="overwrite",
        engine="rust",
        storage_options=storage_opts,
        schema_mode="overwrite",
    )
    try:
        DeltaTable(_risk_path, storage_options=storage_opts).create_checkpoint()
    except Exception as exc:
        logger.warning(f"Gold wildfire_risk_index checkpoint failed (non-fatal): {exc}")
    logger.info(f"Gold wildfire_risk_index: {len(risk)} rows written.")

    total_rows = len(summary) + len(risk)
    elapsed = time.monotonic() - t_start

    rows_gauge.set(total_rows)
    fresh_gauge.set(time.time())
    dur_gauge.set(elapsed)

    try:
        push_to_gateway(pushgateway, job="med_ops_gold_mart", registry=reg)
    except Exception as exc:
        logger.warning(f"Pushgateway push failed (best-effort): {exc}")
    push_to_grafana(reg, job="med_ops_gold_mart")
    logger.success(f"Gold marts complete — {total_rows} total rows, {elapsed:.1f}s")


if __name__ == "__main__":
    run()
