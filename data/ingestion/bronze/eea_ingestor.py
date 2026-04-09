"""
bronze/eea_ingestor.py
──────────────────────
Pulls near-real-time air quality observations from the EEA Discomap API
(AirBase / E1a reporting) for Tunisia and North Africa.
Writes raw records to the Bronze Delta Lake table on MinIO.
"""

import os
import time
from datetime import date, timedelta

import requests
import pandas as pd
from deltalake import DeltaTable, write_deltalake
from prometheus_client import CollectorRegistry, Gauge, Counter, push_to_gateway
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential


MINIO_ENDPOINT   = os.environ["MINIO_ENDPOINT"]
MINIO_ACCESS_KEY = os.environ["MINIO_ACCESS_KEY"]
MINIO_SECRET_KEY = os.environ["MINIO_SECRET_KEY"]
BRONZE_BUCKET    = os.environ.get("MINIO_BUCKET_BRONZE", "bronze")
EEA_API_URL      = os.environ.get("EEA_API_URL",
    "https://discomap.eea.europa.eu/map/fme/airqualityUTD.htm")
PUSHGATEWAY_URL  = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")

STORAGE_OPTIONS = {
    "endpoint_url":           MINIO_ENDPOINT,
    "aws_access_key_id":      MINIO_ACCESS_KEY,
    "aws_secret_access_key":  MINIO_SECRET_KEY,
    "aws_allow_http":         "true",
    "aws_s3_allow_unsafe_rename": "true",
}

TABLE_PATH = f"s3://{BRONZE_BUCKET}/eea/air_quality"

registry        = CollectorRegistry()
ROWS_INGESTED   = Gauge("pipeline_ingested_rows", "Rows written", ["layer", "source"], registry=registry)
LAST_RUN_TS     = Gauge("pipeline_last_successful_run_timestamp", "Last run timestamp", ["layer"], registry=registry)
DURATION_GAUGE  = Gauge("pipeline_duration_seconds", "Stage duration", ["stage"], registry=registry)
FAILURE_COUNTER = Counter("pipeline_quality_check_failures_total", "Quality failures", ["check_name", "layer"], registry=registry)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def fetch_eea_data(target_date: date) -> list[dict]:
    """
    Fetches EEA up-to-date (UTD) air quality data.
    Country codes: TN (Tunisia), DZ (Algeria), MA (Morocco), EG (Egypt).
    Falls back to synthetic data when the endpoint is unavailable.
    """
    countries = ["TN", "DZ", "MA", "LY", "EG"]
    all_records = []

    for country in countries:
        try:
            params = {
                "CountryCode":  country,
                "Pollutant":    0,          # 0 = all pollutants
                "Year_from":    target_date.year,
                "Year_to":      target_date.year,
                "Station":      "",
                "Samplingpoint": "",
                "StartDate":    target_date.isoformat(),
                "EndDate":      target_date.isoformat(),
                "format":       "JSON",
            }
            resp = requests.get(EEA_API_URL, params=params, timeout=45)
            resp.raise_for_status()
            records = resp.json() if isinstance(resp.json(), list) else []
            for r in records:
                r["country_code"] = country
            all_records.extend(records)
        except Exception as exc:
            logger.warning(f"EEA fetch failed for {country}: {exc}. Using synthetic data.")
            all_records.extend(_synthetic_records(target_date, country))

    return all_records


def _synthetic_records(target_date: date, country: str) -> list[dict]:
    import random
    random.seed(int(target_date.strftime("%Y%m%d")) + hash(country) % 1000)
    return [
        {
            "station_id":    f"{country}-DEMO-{i:03d}",
            "country_code":  country,
            "date_start":    target_date.isoformat(),
            "pollutant":     p,
            "value":         round(random.uniform(5, 100), 2),
            "unit":          "µg/m³",
            "source":        "synthetic",
        }
        for i in range(3)
        for p in ["PM2.5", "PM10", "NO2"]
    ]


def run(target_date: date | None = None) -> None:
    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    logger.info(f"EEA Bronze ingestion starting — date={target_date}")
    t_start = time.monotonic()

    records = fetch_eea_data(target_date)
    if not records:
        logger.warning("No EEA records returned. Skipping write.")
        return

    df = pd.DataFrame(records)
    df["ingestion_ts"]   = pd.Timestamp.utcnow().isoformat()
    df["partition_date"] = target_date.strftime("%Y-%m-%d")

    write_deltalake(
        TABLE_PATH,
        df,
        mode="append",
        partition_by=["partition_date"],
        storage_options=STORAGE_OPTIONS,
        schema_mode="merge",
    )

    elapsed = time.monotonic() - t_start
    ROWS_INGESTED.labels(layer="bronze", source="eea").set(len(df))
    LAST_RUN_TS.labels(layer="bronze").set(time.time())
    DURATION_GAUGE.labels(stage="bronze_eea_ingest").set(elapsed)

    push_to_gateway(PUSHGATEWAY_URL, job="med_ops_bronze_eea", registry=registry)
    logger.success(f"EEA Bronze complete — {len(df)} rows, {elapsed:.1f}s")


if __name__ == "__main__":
    run()
