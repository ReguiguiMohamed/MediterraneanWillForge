"""
data/ingestion/gold/anomaly.py
──────────────────────────────
Silver → Gold anomaly detection layer.

Runs an Isolation Forest over the full Silver history to flag stations
and dates where PM2.5 or O3 readings deviate significantly from the
observed distribution. Results are written to a Gold Delta table at:

    s3://{gold_bucket}/anomaly_alerts

Output schema
─────────────
station_id      str   — unique station / grid-point identifier
station_name    str   — human-readable name
country_code    str   — ISO-3166-1 alpha-2
source          str   — openmeteo | openaq
date            str   — YYYY-MM-DD observation date
partition_date  str   — YYYY-MM-DD (Delta partition key)
pm2_5           float — µg/m³ daily mean
ozone           float — µg/m³ daily mean
nitrogen_dioxide float — µg/m³ daily mean
anomaly_score   float — Isolation Forest score; lower = more anomalous
                        Range: negative (anomaly) to positive (normal)
is_anomaly      int   — 1 if flagged anomalous, 0 otherwise
gold_ts         str   — UTC ISO-8601 write timestamp

Model details
─────────────
Features:  pm2_5, ozone, nitrogen_dioxide (non-null rows only)
Algorithm: sklearn IsolationForest, contamination=0.05
Training:  full Silver history on each run (table rebuilt with overwrite)

Minimum rows: if Silver has fewer than 10 usable rows the run is skipped
to avoid fitting a model on noise.
"""

from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd
from deltalake import DeltaTable, write_deltalake
from loguru import logger
from sklearn.ensemble import IsolationForest

from data.metrics import push_to_grafana
from data.storage import delta_storage_options

# ── Config ─────────────────────────────────────────────────────────────────────

_FEATURES = ["pm2_5", "ozone", "nitrogen_dioxide"]
_CONTAMINATION = 0.05  # expect ~5 % of readings to be anomalous
_MIN_ROWS = 10  # skip model fit if Silver has fewer rows than this

# ── Entry point ────────────────────────────────────────────────────────────────


def run() -> None:
    storage_opts = delta_storage_options()
    silver_bucket = os.environ.get("MINIO_BUCKET_SILVER", "silver")
    gold_bucket = os.environ.get("MINIO_BUCKET_GOLD", "gold")

    logger.info("Anomaly detection starting.")
    t_start = time.monotonic()

    # ── Load Silver ────────────────────────────────────────────────────────────
    silver_path = f"s3://{silver_bucket}/air_quality"
    try:
        silver_df = DeltaTable(silver_path, storage_options=storage_opts).to_pandas()
    except Exception as exc:
        logger.error(f"Cannot read Silver layer: {exc}")
        return

    if silver_df.empty:
        logger.warning("Silver layer is empty — skipping anomaly detection.")
        return

    # Keep only rows with at least one feature non-null
    feat_df = silver_df.dropna(subset=_FEATURES, how="all").copy()
    if len(feat_df) < _MIN_ROWS:
        logger.warning(
            f"Silver has only {len(feat_df)} usable rows (< {_MIN_ROWS}) — "
            "skipping anomaly detection to avoid fitting on noise."
        )
        return

    # Exclude stations seen on fewer than 2 dates — first-appearance stations
    # have no historical baseline and are systematically flagged as anomalies
    # by Isolation Forest because their pollutant profiles are isolated from
    # the existing distribution (e.g. new WAQI stations on day 1).
    station_date_counts = feat_df.groupby("station_id")["date"].nunique()
    established = station_date_counts[station_date_counts >= 2].index
    excluded = len(feat_df) - feat_df["station_id"].isin(established).sum()
    if excluded:
        logger.info(
            f"Excluding {excluded} row(s) from {(~feat_df['station_id'].isin(established)).sum()} "
            f"first-appearance station(s) — no historical baseline yet."
        )
    feat_df = feat_df[feat_df["station_id"].isin(established)].copy()

    if len(feat_df) < _MIN_ROWS:
        logger.warning(
            f"Only {len(feat_df)} established-station rows after filtering — "
            "skipping anomaly detection."
        )
        return

    logger.info(f"Silver snapshot: {len(feat_df)} rows with pollutant data.")

    # ── Feature matrix (fill NaN with column median) ───────────────────────────
    X = feat_df[_FEATURES].copy()
    for col in _FEATURES:
        X[col] = X[col].fillna(X[col].median())
    X = X.values.astype(np.float64)

    # ── Fit Isolation Forest ───────────────────────────────────────────────────
    model = IsolationForest(
        n_estimators=100,
        contamination=_CONTAMINATION,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X)

    scores = model.score_samples(X)  # lower = more anomalous
    labels = model.predict(X)  # -1 = anomaly, 1 = normal

    # ── Build output DataFrame ─────────────────────────────────────────────────
    result = feat_df[
        [
            "station_id",
            "station_name",
            "country_code",
            "source",
            "date",
            "partition_date",
            "pm2_5",
            "ozone",
            "nitrogen_dioxide",
        ]
    ].copy()

    result["anomaly_score"] = scores.round(6)
    result["is_anomaly"] = (labels == -1).astype(int)
    result["gold_ts"] = pd.Timestamp.utcnow().isoformat()

    n_anomalies = result["is_anomaly"].sum()
    logger.info(
        f"Model fit complete — {n_anomalies} anomalies flagged "
        f"({n_anomalies / len(result):.1%} of {len(result)} rows)."
    )

    # ── Write Gold anomaly_alerts ──────────────────────────────────────────────
    anomaly_path = f"s3://{gold_bucket}/anomaly_alerts"
    write_deltalake(
        anomaly_path,
        result,
        mode="overwrite",
        engine="rust",
        storage_options=storage_opts,
        schema_mode="overwrite",
    )
    try:
        DeltaTable(anomaly_path, storage_options=storage_opts).create_checkpoint()
    except Exception as exc:
        logger.warning(f"Anomaly table checkpoint failed (non-fatal): {exc}")

    elapsed = time.monotonic() - t_start
    logger.success(
        f"Anomaly detection complete — {len(result)} rows written, "
        f"{n_anomalies} anomalies, {elapsed:.1f}s"
    )

    push_to_grafana_anomaly(len(result), n_anomalies, elapsed)


def push_to_grafana_anomaly(total: int, anomalies: int, elapsed: float) -> None:
    from prometheus_client import CollectorRegistry, Gauge

    reg = CollectorRegistry()
    Gauge(
        "med_ops_gold_anomaly_rows",
        "Rows processed by anomaly detection",
        [],
        registry=reg,
    ).set(total)
    Gauge(
        "med_ops_gold_anomaly_flags",
        "Anomalous readings flagged by Isolation Forest",
        [],
        registry=reg,
    ).set(anomalies)
    Gauge(
        "med_ops_gold_anomaly_duration_seconds",
        "Wall-clock seconds for anomaly detection",
        [],
        registry=reg,
    ).set(elapsed)

    push_to_grafana(reg, job="med_ops_gold_anomaly")


if __name__ == "__main__":
    run()
