"""
data/ingestion/bronze/base.py
─────────────────────────────
Abstract base for all Bronze-layer ingestors.

Every concrete ingestor must implement:
  - source_name  (str property)  — short identifier used in metrics labels
  - table_path   (str property)  — full S3 path, e.g. s3://bronze/openmeteo/air_quality
  - fetch()                      — pulls records for a given date; returns a DataFrame

The base class handles:
  - Idempotency check (skip if partition already written)
  - Delta Lake write with schema-merge
  - Prometheus metric push (rows, duration, freshness)
  - Unified logging
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import TableNotFoundError
from loguru import logger
from prometheus_client import CollectorRegistry, Counter, Gauge, push_to_gateway

from data.metrics import push_to_grafana
from data.storage import delta_storage_options

# ── Storage configuration ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class StorageConfig:
    """MinIO / S3-compatible connection parameters."""

    endpoint: str
    access_key: str
    secret_key: str
    bronze_bucket: str

    @property
    def options(self) -> dict[str, str]:
        """Delta Lake storage options for the S3A connector."""
        return delta_storage_options(self.endpoint, self.access_key, self.secret_key)

    @classmethod
    def from_env(cls) -> StorageConfig:
        return cls(
            endpoint=os.environ["MINIO_ENDPOINT"],
            access_key=os.environ["MINIO_ACCESS_KEY"],
            secret_key=os.environ["MINIO_SECRET_KEY"],
            bronze_bucket=os.environ.get("MINIO_BUCKET_BRONZE", "bronze"),
        )


# ── Base ingestor ──────────────────────────────────────────────────────────────


class BronzeIngestor(ABC):
    """
    Template-method base for Bronze-layer ingestion.

    Subclasses implement `fetch()` and two properties; everything else
    — idempotency, write, metric push — is handled here.
    """

    def __init__(self, storage: StorageConfig, pushgateway_url: str) -> None:
        self.storage = storage
        self.pushgateway_url = pushgateway_url
        self._registry = CollectorRegistry()
        self._init_metrics()

    # ── Abstract interface ────────────────────────────────────────────────────

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Short identifier used in Prometheus label values, e.g. 'openmeteo'."""

    @property
    @abstractmethod
    def table_path(self) -> str:
        """Full S3 Delta table path, e.g. 's3://bronze/openmeteo/air_quality'."""

    @abstractmethod
    def fetch(self, target_date: date) -> pd.DataFrame:
        """
        Pull raw records for target_date from the upstream source.

        Returns a DataFrame with at minimum: station_id, date, partition_date,
        ingestion_ts, and any pollutant columns. Empty frame = source had no data.
        """

    # ── Metrics ───────────────────────────────────────────────────────────────

    def _init_metrics(self) -> None:
        src = self.source_name
        self._rows_gauge = Gauge(
            f"med_ops_bronze_{src}_rows",
            "Rows written in this ingest run",
            [],
            registry=self._registry,
        )
        self._freshness_gauge = Gauge(
            f"med_ops_bronze_{src}_last_run_ts",
            "Unix timestamp of last completed run",
            [],
            registry=self._registry,
        )
        self._duration_gauge = Gauge(
            f"med_ops_bronze_{src}_duration_seconds",
            "Wall-clock seconds for this Bronze stage",
            [],
            registry=self._registry,
        )
        self._quality_counter = Counter(
            f"med_ops_bronze_{src}_quality_failures_total",
            "Number of data quality gate failures",
            [],
            registry=self._registry,
        )

    def _push_metrics(self, rows: int, elapsed: float) -> None:
        self._rows_gauge.set(rows)
        self._freshness_gauge.set(time.time())
        self._duration_gauge.set(elapsed)
        try:
            push_to_gateway(
                self.pushgateway_url,
                job=f"med_ops_bronze_{self.source_name}",
                registry=self._registry,
            )
        except Exception as exc:
            logger.warning(
                f"[{self.source_name}] Pushgateway unavailable — metrics not pushed: {exc}"
            )
        push_to_grafana(self._registry, job=f"med_ops_bronze_{self.source_name}")

    # ── Delta Lake helpers ────────────────────────────────────────────────────

    def _partition_exists(self, target_date: date) -> bool:
        """Return True if the date partition is already present in the Delta table.

        Uses dt.files() path inspection instead of to_pandas(columns=[...]).
        With engine="rust", partition columns are excluded from Parquet data
        files, so column-projection reads raise silent errors that always
        return False, breaking idempotency.
        """
        partition_value = target_date.strftime("%Y-%m-%d")
        prefix = f"partition_date={partition_value}"
        try:
            dt = DeltaTable(self.table_path, storage_options=self.storage.options)
            return any(prefix in f for f in dt.files())
        except TableNotFoundError:
            return False

    def _write(self, df: pd.DataFrame) -> None:
        """Append df to the Delta table, merging any new columns automatically."""
        write_deltalake(
            self.table_path,
            df,
            mode="append",
            partition_by=["partition_date"],
            storage_options=self.storage.options,
            schema_mode="merge",
            engine="rust",
        )
        try:
            DeltaTable(
                self.table_path, storage_options=self.storage.options
            ).create_checkpoint()
        except Exception as exc:
            logger.warning(
                f"[{self.source_name}] Delta checkpoint failed (non-fatal): {exc}"
            )

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self, target_date: date | None = None) -> None:
        """
        Execute the full ingestion cycle for target_date (defaults to yesterday).

        Idempotent: if the partition already exists the run is skipped silently.
        """
        if target_date is None:
            target_date = date.today() - timedelta(days=1)

        logger.info(f"[{self.source_name}] Bronze ingest starting — date={target_date}")

        if self._partition_exists(target_date):
            logger.info(
                f"[{self.source_name}] Partition {target_date} already present — "
                "skipping (idempotent)."
            )
            return

        t0 = time.monotonic()
        df = self.fetch(target_date)

        if df.empty:
            elapsed = time.monotonic() - t0
            self._push_metrics(0, elapsed)
            logger.warning(
                f"[{self.source_name}] fetch() returned 0 rows for {target_date}. "
                "Upstream source may have no data for this date."
            )
            return

        self._write(df)
        elapsed = time.monotonic() - t0

        self._push_metrics(len(df), elapsed)
        logger.success(
            f"[{self.source_name}] Bronze ingest complete — "
            f"{len(df)} rows written in {elapsed:.1f}s"
        )
