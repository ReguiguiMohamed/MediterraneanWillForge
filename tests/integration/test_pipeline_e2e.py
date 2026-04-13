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

_STORAGE_OPTS = {
    "endpoint_url": MINIO_ENDPOINT,
    "aws_access_key_id": os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
    "aws_secret_access_key": os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
    "aws_allow_http": "true",
    "aws_s3_allow_unsafe_rename": "true",
}


def minio_available() -> bool:
    try:
        resp = requests.get(f"{MINIO_ENDPOINT}/minio/health/live", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not minio_available(),
    reason="MinIO not reachable — skipping integration tests",
)


# ── Open-Meteo (Copernicus) ingestor ─────────────────────────────────────────


class TestCopernicusIngestor:
    """End-to-end Bronze write tests for the Open-Meteo ingestor."""

    _TABLE = "s3://bronze/openmeteo/air_quality"
    _DATE = date(2024, 1, 15)

    def test_writes_delta_table(self):
        from data.ingestion.bronze.copernicus_ingestor import run

        run(target_date=self._DATE)

        from deltalake import DeltaTable

        dt = DeltaTable(self._TABLE, storage_options=_STORAGE_OPTS)
        df = dt.to_pandas(filters=[("partition_date", "=", self._DATE.isoformat())])
        assert not df.empty, "Bronze partition must not be empty after ingest"
        assert "station_id" in df.columns
        assert "pm2_5" in df.columns

    def test_idempotent(self):
        from data.ingestion.bronze.copernicus_ingestor import run
        from deltalake import DeltaTable

        run_date = date(2024, 1, 16)
        run(target_date=run_date)
        dt = DeltaTable(self._TABLE, storage_options=_STORAGE_OPTS)
        first = len(
            dt.to_pandas(filters=[("partition_date", "=", run_date.isoformat())])
        )

        run(target_date=run_date)  # second run must be skipped
        dt2 = DeltaTable(self._TABLE, storage_options=_STORAGE_OPTS)
        second = len(
            dt2.to_pandas(filters=[("partition_date", "=", run_date.isoformat())])
        )

        assert first == second, "Idempotent ingest must not duplicate rows"


# ── OpenAQ ingestor ───────────────────────────────────────────────────────────


class TestOpenAQIngestor:
    """End-to-end Bronze write tests for the OpenAQ ingestor."""

    _TABLE = "s3://bronze/openaq/air_quality"
    _DATE = date(2024, 1, 15)

    def test_writes_delta_table(self):
        from data.ingestion.bronze.openaq_ingestor import run

        run(target_date=self._DATE)

        from deltalake import DeltaTable

        dt = DeltaTable(self._TABLE, storage_options=_STORAGE_OPTS)
        df = dt.to_pandas(filters=[("partition_date", "=", self._DATE.isoformat())])
        assert not df.empty, "OpenAQ Bronze partition must not be empty after ingest"
        assert "station_id" in df.columns
        assert "country_code" in df.columns

    def test_idempotent(self):
        from data.ingestion.bronze.openaq_ingestor import run
        from deltalake import DeltaTable

        run_date = date(2024, 1, 16)
        run(target_date=run_date)
        dt = DeltaTable(self._TABLE, storage_options=_STORAGE_OPTS)
        first = len(
            dt.to_pandas(filters=[("partition_date", "=", run_date.isoformat())])
        )

        run(target_date=run_date)
        dt2 = DeltaTable(self._TABLE, storage_options=_STORAGE_OPTS)
        second = len(
            dt2.to_pandas(filters=[("partition_date", "=", run_date.isoformat())])
        )

        assert first == second, "Idempotent ingest must not duplicate rows"
