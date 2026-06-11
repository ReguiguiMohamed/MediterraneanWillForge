from datetime import date

import pandas as pd

from data.ingestion.bronze.base import BronzeIngestor, StorageConfig


class EmptyIngestor(BronzeIngestor):
    @property
    def source_name(self) -> str:
        return "empty"

    @property
    def table_path(self) -> str:
        return "s3://bronze/empty/air_quality"

    def fetch(self, target_date: date) -> pd.DataFrame:
        return pd.DataFrame()


def test_empty_fetch_publishes_zero_rows_without_writing(monkeypatch):
    ingestor = EmptyIngestor(
        StorageConfig(
            endpoint="http://localhost:9000",
            access_key="minioadmin",
            secret_key="minioadmin",
            bronze_bucket="bronze",
        ),
        "http://localhost:9091",
    )
    pushed = []

    monkeypatch.setattr(ingestor, "_partition_exists", lambda target_date: False)
    monkeypatch.setattr(
        ingestor,
        "_write",
        lambda frame: (_ for _ in ()).throw(AssertionError("must not write")),
    )
    monkeypatch.setattr(
        ingestor,
        "_push_metrics",
        lambda rows, elapsed: pushed.append((rows, elapsed)),
    )

    ingestor.run(date(2024, 1, 15))

    assert len(pushed) == 1
    assert pushed[0][0] == 0
    assert pushed[0][1] >= 0
