"""Gold layer entry points."""

from __future__ import annotations

import os

from loguru import logger

from data.ingestion.gold import anomaly, marts
from data.ingestion.gold.window import read_silver_window

__all__ = ["anomaly", "marts", "run_all"]


def run_all() -> None:
    """Build both Gold outputs from a single windowed Silver scan.

    marts.run() and anomaly.run() each used to open the full Silver table in
    their own process — two identical full-table scans per pipeline run, and
    that cost grows with every day of history retained.  Reading once here
    halves the Class B transactions and download bandwidth the Gold stage
    spends against the B2 daily cap, and reading only the recent window
    stops the remaining half growing.  See data/ingestion/gold/window.py.
    """
    silver_path = f"s3://{os.environ.get('MINIO_BUCKET_SILVER', 'silver')}/air_quality"
    try:
        silver_df = read_silver_window(silver_path)
    except Exception as exc:
        raise RuntimeError(f"Cannot read Silver layer: {exc}") from exc

    logger.info(f"Silver scanned once for both Gold stages: {len(silver_df)} rows.")
    marts.run(silver_df)
    anomaly.run(silver_df)
