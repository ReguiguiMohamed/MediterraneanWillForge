"""
Integration tests — requires a live MinIO instance.
Set MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY in environment.
Skipped automatically when MinIO is unreachable.
"""
import os
import pytest
import requests
from datetime import date


MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://localhost:9000")


def minio_available() -> bool:
    try:
        resp = requests.get(f"{MINIO_ENDPOINT}/minio/health/live", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not minio_available(),
    reason="MinIO not reachable — skipping integration tests"
)


def test_bronze_ingest_writes_delta_table():
    """Running bronze ingest for a fixed date should create a Delta table."""
    from data.ingestion.bronze.copernicus_ingestor import run
    run(target_date=date(2024, 1, 15))

    from deltalake import DeltaTable
    storage_opts = {
        "endpoint_url":           MINIO_ENDPOINT,
        "aws_access_key_id":      os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        "aws_secret_access_key":  os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
        "aws_allow_http":         "true",
        "aws_s3_allow_unsafe_rename": "true",
    }
    dt = DeltaTable("s3://bronze/copernicus/air_quality", storage_options=storage_opts)
    df = dt.to_pandas(filters=[("partition_date", "=", "2024-01-15")])
    assert not df.empty, "Bronze partition should not be empty after ingest"
    assert "station_id" in df.columns
    assert "pm2p5" in df.columns


def test_bronze_ingest_is_idempotent():
    """Running ingest twice for the same date should not duplicate rows."""
    from data.ingestion.bronze.copernicus_ingestor import run
    from deltalake import DeltaTable

    storage_opts = {
        "endpoint_url":           MINIO_ENDPOINT,
        "aws_access_key_id":      os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        "aws_secret_access_key":  os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
        "aws_allow_http":         "true",
        "aws_s3_allow_unsafe_rename": "true",
    }

    run(target_date=date(2024, 1, 16))
    dt  = DeltaTable("s3://bronze/copernicus/air_quality", storage_options=storage_opts)
    count_first = len(dt.to_pandas(filters=[("partition_date", "=", "2024-01-16")]))

    run(target_date=date(2024, 1, 16))  # second run — should skip
    dt2 = DeltaTable("s3://bronze/copernicus/air_quality", storage_options=storage_opts)
    count_second = len(dt2.to_pandas(filters=[("partition_date", "=", "2024-01-16")]))

    assert count_first == count_second, "Idempotent ingest must not duplicate rows"
