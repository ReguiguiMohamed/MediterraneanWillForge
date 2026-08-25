"""
data/ingestion/gold/window.py
──────────────────────────────
The rolling window that keeps the Gold stage's read cost flat.

Gold used to rebuild itself from the whole Silver layer every night. Silver is
partitioned by date, so that read costs one object fetch per partition per
source, and it grew by four fetches a day forever. By late August 2026 it was
551 fetches a night, four fifths of everything the pipeline spent against the
B2 free tier's daily transaction cap, to recompute months of rows that had not
changed since the day they landed.

So each run reads the last WINDOW_DAYS of Silver and rewrites only the last
REFRESH_DAYS of Gold, keeping everything older untouched. The gap between the
two is deliberate: heat alerts compare a day against its station's previous
thirty, so the oldest refreshed day still needs a month of lead-in behind it.

Set GOLD_WINDOW_DAYS=all for a full rebuild. Backfills need it, because a date
older than the refresh window would otherwise never reach Gold.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
from loguru import logger

from data.storage import read_delta

# Silver days read per run. Must clear REFRESH_DAYS plus the 30-day baseline
# that flag_heat_events looks back over, with room to spare.
WINDOW_DAYS = 60

# Gold days recomputed and replaced per run. Also the grace period for a late
# arrival: a partition that lands within this many days still reaches Gold on
# the next ordinary run.
REFRESH_DAYS = 14

_FULL = {"all", "full", "0"}


def _configured(name: str, default: int) -> int | None:
    """Read a day count from the environment. None means rebuild everything."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _FULL:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning(f"{name}={raw!r} is not a day count — using {default}.")
        return default


def window_days() -> int | None:
    return _configured("GOLD_WINDOW_DAYS", WINDOW_DAYS)


def refresh_days() -> int | None:
    return _configured("GOLD_REFRESH_DAYS", REFRESH_DAYS)


def _cutoff(days: int | None) -> str | None:
    return None if days is None else (date.today() - timedelta(days=days)).isoformat()


def read_silver_window(
    path: str, storage_options: dict[str, str] | None = None
) -> pd.DataFrame:
    """Read Silver from the window cutoff onwards, or all of it when unbounded."""
    cutoff = _cutoff(window_days())
    if cutoff is None:
        logger.info(f"Reading all of {path} (full rebuild).")
        return read_delta(path, storage_options)

    logger.info(f"Reading {path} from {cutoff} onwards ({window_days()}-day window).")
    return read_delta(path, storage_options, filters=[("partition_date", ">=", cutoff)])


def refresh_cutoff() -> str | None:
    """First Gold partition_date this run will replace. None replaces all."""
    return _cutoff(refresh_days())


def merge_refreshed(
    table_path: str, fresh: pd.DataFrame, storage_options
) -> pd.DataFrame:
    """Splice freshly computed rows into the existing Gold table.

    Rows older than the refresh cutoff are carried over untouched; everything
    from the cutoff onwards comes from `fresh`. A full rebuild skips the read
    and returns `fresh` whole.

    Refuses to guess when the existing table is unreadable. Writing `fresh`
    alone would silently truncate Gold to the refresh window, and losing five
    months of history to a transient read error is far worse than a failed run.
    """
    cutoff = refresh_cutoff()
    if cutoff is None:
        return fresh

    try:
        existing = read_delta(table_path, storage_options)
    except Exception as exc:
        if _is_missing_table(exc):
            logger.info(f"{table_path} does not exist yet — writing the window alone.")
            return fresh
        raise RuntimeError(
            f"Cannot read {table_path} to preserve history behind the "
            f"{refresh_days()}-day refresh window: {exc}"
        ) from exc

    if existing.empty or "partition_date" not in existing.columns:
        return fresh

    kept = existing[existing["partition_date"].astype(str) < cutoff]
    fresh = fresh[fresh["partition_date"].astype(str) >= cutoff]
    logger.info(
        f"{table_path}: keeping {len(kept)} row(s) before {cutoff}, "
        f"replacing with {len(fresh)} refreshed row(s)."
    )
    return pd.concat([kept, fresh], ignore_index=True)


def _is_missing_table(exc: Exception) -> bool:
    """True when the table has simply never been written."""
    from deltalake.exceptions import TableNotFoundError

    return (
        isinstance(exc, TableNotFoundError) or "not a delta table" in str(exc).lower()
    )
