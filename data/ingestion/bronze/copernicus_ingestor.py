"""
bronze/copernicus_ingestor.py
─────────────────────────────
Pulls daily CAMS (Copernicus Atmosphere Monitoring Service) air quality
reanalysis data for the Mediterranean / North Africa region and writes
it as Delta Lake Parquet to MinIO's bronze bucket.

Discipline: incremental by date. If today's partition already exists, skip.
No fragile infrastructure: every run is idempotent.
"""

import os
import json
import time
from datetime import date, timedelta
from pathlib import Path

import requests
import pandas as pd
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from prometheus_client import CollectorRegistry, Gauge, Counter, push_to_gateway
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential


# ── Configuration ─────────────────────────────────────────────────────────────
MINIO_ENDPOINT   = os.environ["MINIO_ENDPOINT"]
MINIO_ACCESS_KEY = os.environ["MINIO_ACCESS_KEY"]
MINIO_SECRET_KEY = os.environ["MINIO_SECRET_KEY"]
BRONZE_BUCKET    = os.environ.get("MINIO_BUCKET_BRONZE", "bronze")
CAMS_API_URL     = os.environ.get("CAMS_API_URL", "https://ads.atmosphere.copernicus.eu/api/v2")
CAMS_API_KEY     = os.environ.get("CAMS_API_KEY", "")
PUSHGATEWAY_URL  = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")
PIPELINE_ENV     = os.environ.get("PIPELINE_ENV", "local")

STORAGE_OPTIONS = {
    "endpoint_url":        MINIO_ENDPOINT,
    "aws_access_key_id":   MINIO_ACCESS_KEY,
    "aws_secret_access_key": MINIO_SECRET_KEY,
    "aws_allow_http":      "true",
    "aws_s3_allow_unsafe_rename": "true",
}

TABLE_PATH = f"s3://{BRONZE_BUCKET}/copernicus/air_quality"

SCHEMA_PATH = Path(__file__).parents[3] / "schemas" / "bronze_air_quality.json"


# ── Prometheus metrics ─────────────────────────────────────────────────────────
registry = CollectorRegistry()
ROWS_INGESTED   = Gauge("pipeline_ingested_rows",
                        "Rows written in this run",
                        ["layer", "source"], registry=registry)
LAST_RUN_TS     = Gauge("pipeline_last_successful_run_timestamp",
                        "Unix timestamp of last successful run",
                        ["layer"], registry=registry)
DURATION_GAUGE  = Gauge("pipeline_duration_seconds",
                        "Wall-clock seconds for each pipeline stage",
                        ["stage"], registry=registry)
FAILURE_COUNTER = Counter("pipeline_quality_check_failures_total",
                          "Data quality failures",
                          ["check_name", "layer"], registry=registry)


# ── Helpers ───────────────────────────────────────────────────────────────────
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def fetch_cams_data(target_date: date) -> list[dict]:
    """
    Calls CAMS near-real-time surface air quality endpoint.
    Returns a list of records for the Mediterranean bounding box.
    Falls back to synthetic data if CAMS_API_KEY is not configured,
    so local development works without credentials.
    """
    if not CAMS_API_KEY:
        logger.warning("CAMS_API_KEY not set — using synthetic demo data.")
        return _synthetic_records(target_date)

    params = {
        "variable":     ["pm2p5", "pm10", "no2", "o3"],
        "date":         target_date.isoformat(),
        "area":         [40, -10, 25, 40],   # [N, W, S, E] Mediterranean box
        "format":       "json",
    }
    headers = {"Authorization": f"Bearer {CAMS_API_KEY}"}
    response = requests.get(f"{CAMS_API_URL}/resources/cams-europe-air-quality-reanalyses",
                            params=params, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json().get("records", [])


def _synthetic_records(target_date: date) -> list[dict]:
    """Generate reproducible demo records for development / CI."""
    import random
    random.seed(int(target_date.strftime("%Y%m%d")))
    stations = [
        ("Tunis",     36.82, 10.17),
        ("Algiers",   36.74,  3.06),
        ("Casablanca",33.57, -7.59),
        ("Tripoli",   32.90, 13.18),
        ("Cairo",     30.06, 31.24),
    ]
    return [
        {
            "station_id":   station,
            "station_name": station,
            "latitude":     lat,
            "longitude":    lon,
            "date":         target_date.isoformat(),
            "pm2p5":        round(random.uniform(5, 80), 2),
            "pm10":         round(random.uniform(10, 120), 2),
            "no2":          round(random.uniform(2, 60), 2),
            "o3":           round(random.uniform(30, 180), 2),
            "source":       "synthetic",
        }
        for station, lat, lon in stations
    ]


def records_to_dataframe(records: list[dict], target_date: date) -> pd.DataFrame:
    df = pd.DataFrame(records)
    df["ingestion_date"] = target_date.isoformat()
    df["ingestion_ts"]   = pd.Timestamp.utcnow().isoformat()
    df["partition_date"] = target_date.strftime("%Y-%m-%d")
    return df


def partition_exists(target_date: date) -> bool:
    try:
        dt = DeltaTable(TABLE_PATH, storage_options=STORAGE_OPTIONS)
        existing = dt.to_pandas(
            filters=[("partition_date", "=", target_date.strftime("%Y-%m-%d"))]
        )
        return not existing.empty
    except Exception:
        return False


def validate(df: pd.DataFrame) -> None:
    required_cols = {"station_id", "latitude", "longitude", "date", "pm2p5", "pm10"}
    missing = required_cols - set(df.columns)
    if missing:
        FAILURE_COUNTER.labels(check_name="required_columns", layer="bronze").inc()
        raise ValueError(f"Missing required columns: {missing}")

    null_pct = df[list(required_cols)].isnull().mean()
    for col, pct in null_pct.items():
        if pct > 0.20:
            FAILURE_COUNTER.labels(check_name=f"null_rate_{col}", layer="bronze").inc()
            logger.warning(f"Column {col} null rate {pct:.1%} exceeds 20% threshold.")


# ── Main ──────────────────────────────────────────────────────────────────────
def run(target_date: date | None = None) -> None:
    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    logger.info(f"Bronze ingestion starting — date={target_date}")
    t_start = time.monotonic()

    if partition_exists(target_date):
        logger.info(f"Partition {target_date} already exists — skipping.")
        return

    records = fetch_cams_data(target_date)
    df      = records_to_dataframe(records, target_date)
    validate(df)

    write_deltalake(
        TABLE_PATH,
        df,
        mode="append",
        partition_by=["partition_date"],
        storage_options=STORAGE_OPTIONS,
        schema_mode="merge",   # allow schema evolution
    )

    elapsed = time.monotonic() - t_start
    ROWS_INGESTED.labels(layer="bronze", source="copernicus").set(len(df))
    LAST_RUN_TS.labels(layer="bronze").set(time.time())
    DURATION_GAUGE.labels(stage="bronze_ingest").set(elapsed)

    push_to_gateway(PUSHGATEWAY_URL, job="med_ops_bronze_ingest", registry=registry)
    logger.success(f"Bronze ingestion complete — {len(df)} rows, {elapsed:.1f}s")


if __name__ == "__main__":
    run()
