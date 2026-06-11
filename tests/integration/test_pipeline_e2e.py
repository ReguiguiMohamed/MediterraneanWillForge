"""MinIO-backed integration tests with no public API calls."""

from __future__ import annotations

import os
from datetime import date, timezone

import pandas as pd
import pytest
import requests
from deltalake import DeltaTable

from data.ingestion.bronze.copernicus_ingestor import CopernicusIngestor
from data.ingestion.bronze.openaq_ingestor import OpenAQIngestor
from data.ingestion.bronze.waqi_ingestor import WAQIIngestor
from data.storage import delta_storage_options

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://localhost:9000")
TEST_DATES = (date(2024, 1, 15), date(2024, 1, 16))

_STORAGE_OPTS = delta_storage_options(
    endpoint=MINIO_ENDPOINT,
    access_key=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
    secret_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
)


def minio_available() -> bool:
    try:
        response = requests.get(f"{MINIO_ENDPOINT}/minio/health/live", timeout=3)
        return response.status_code == 200
    except requests.RequestException:
        return False


pytestmark = pytest.mark.skipif(
    not minio_available(),
    reason="MinIO not reachable; skipping integration tests",
)


def _source_frame(source: str, target_date: date) -> pd.DataFrame:
    rows = []
    station_count = 6 if source == "openmeteo" else 2
    country_codes = ["TN", "DZ", "MA", "EG", "TR", "GR"]

    for index in range(station_count):
        row = {
            "station_id": f"{source}-station-{index}",
            "station_name": f"{source.title()} Station {index}",
            "country_code": country_codes[index],
            "latitude": 30.0 + index,
            "longitude": 5.0 + index,
            "date": target_date.isoformat(),
            "pm2_5": 10.0 + index + target_date.day,
            "pm10": 20.0 + index + target_date.day,
            "nitrogen_dioxide": 5.0 + index,
            "ozone": 40.0 + index + target_date.day,
            "source": source,
            "ingestion_ts": pd.Timestamp.now(tz=timezone.utc).isoformat(),
            "partition_date": target_date.isoformat(),
        }
        if source != "openmeteo":
            row["city"] = f"City {index}"
        rows.append(row)

    return pd.DataFrame(rows)


def _seed_bronze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        CopernicusIngestor,
        "fetch",
        lambda self, target_date: _source_frame("openmeteo", target_date),
    )
    monkeypatch.setattr(
        OpenAQIngestor,
        "fetch",
        lambda self, target_date: _source_frame("openaq", target_date),
    )
    monkeypatch.setattr(
        WAQIIngestor,
        "fetch",
        lambda self, target_date: _source_frame("waqi", target_date),
    )

    from data.ingestion.bronze.copernicus_ingestor import run as run_openmeteo
    from data.ingestion.bronze.openaq_ingestor import run as run_openaq
    from data.ingestion.bronze.waqi_ingestor import run as run_waqi

    for target_date in TEST_DATES:
        run_openmeteo(target_date)
        run_openaq(target_date)
        run_waqi(target_date)


@pytest.mark.parametrize("source", ["openmeteo", "openaq", "waqi"])
def test_bronze_sources_write_partitioned_delta_tables(monkeypatch, source):
    _seed_bronze(monkeypatch)

    table = DeltaTable(
        f"s3://bronze/{source}/air_quality",
        storage_options=_STORAGE_OPTS,
    )
    frame = table.to_pandas()

    assert set(frame["partition_date"].astype(str)) == {
        target_date.isoformat() for target_date in TEST_DATES
    }
    assert set(frame["source"]) == {source}
    assert {
        "station_id",
        "pm2_5",
        "pm10",
        "nitrogen_dioxide",
        "ozone",
    }.issubset(frame.columns)


def test_bronze_writes_are_idempotent(monkeypatch):
    _seed_bronze(monkeypatch)

    before = {}
    for source in ("openmeteo", "openaq", "waqi"):
        table = DeltaTable(
            f"s3://bronze/{source}/air_quality",
            storage_options=_STORAGE_OPTS,
        )
        before[source] = len(table.to_pandas())

    _seed_bronze(monkeypatch)

    for source, expected_rows in before.items():
        table = DeltaTable(
            f"s3://bronze/{source}/air_quality",
            storage_options=_STORAGE_OPTS,
        )
        assert len(table.to_pandas()) == expected_rows
