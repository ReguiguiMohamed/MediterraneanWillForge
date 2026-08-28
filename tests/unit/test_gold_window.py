"""The rolling window that bounds the Gold stage's read cost."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from data.ingestion.gold import window


def _gold_rows(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"partition_date": dates, "value": range(len(dates))})


def _day(offset: int) -> str:
    return (date.today() - timedelta(days=offset)).isoformat()


# ── Configuration ──────────────────────────────────────────────────────────────


def test_defaults_leave_room_for_the_heat_baseline():
    # The oldest refreshed day still needs 30 days of lead-in behind it.
    assert window.WINDOW_DAYS - window.REFRESH_DAYS > 30


@pytest.mark.parametrize("value", ["all", "full", "0", "ALL"])
def test_full_rebuild_is_requested_by_keyword(monkeypatch, value):
    monkeypatch.setenv("GOLD_WINDOW_DAYS", value)
    assert window.window_days() is None


def test_a_day_count_overrides_the_default(monkeypatch):
    monkeypatch.setenv("GOLD_WINDOW_DAYS", "90")
    assert window.window_days() == 90


def test_nonsense_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("GOLD_WINDOW_DAYS", "last tuesday")
    assert window.window_days() == window.WINDOW_DAYS


def test_unset_uses_the_default(monkeypatch):
    monkeypatch.delenv("GOLD_WINDOW_DAYS", raising=False)
    assert window.window_days() == window.WINDOW_DAYS


# ── Merging ────────────────────────────────────────────────────────────────────


def test_history_behind_the_window_survives_a_refresh(monkeypatch):
    monkeypatch.setenv("GOLD_REFRESH_DAYS", "14")
    old, recent = _day(200), _day(3)
    monkeypatch.setattr(window, "read_delta", lambda *a, **k: _gold_rows([old, recent]))

    merged = window.merge_refreshed("s3://gold/t", _gold_rows([recent]), {})

    assert sorted(merged["partition_date"]) == sorted([old, recent])
    assert len(merged) == 2


def test_rows_inside_the_window_are_replaced_not_duplicated(monkeypatch):
    monkeypatch.setenv("GOLD_REFRESH_DAYS", "14")
    recent = _day(2)
    monkeypatch.setattr(window, "read_delta", lambda *a, **k: _gold_rows([recent]))

    fresh = pd.DataFrame({"partition_date": [recent], "value": [999]})
    merged = window.merge_refreshed("s3://gold/t", fresh, {})

    assert len(merged) == 1
    assert merged["value"].iloc[0] == 999


def test_fresh_rows_older_than_the_cutoff_are_dropped(monkeypatch):
    """Lead-in days are read to compute baselines, not to be written."""
    monkeypatch.setenv("GOLD_REFRESH_DAYS", "14")
    monkeypatch.setattr(window, "read_delta", lambda *a, **k: _gold_rows([_day(40)]))

    fresh = _gold_rows([_day(40), _day(2)])
    merged = window.merge_refreshed("s3://gold/t", fresh, {})

    # The 40-day-old row comes from the existing table, not from fresh.
    assert sorted(merged["partition_date"]) == sorted([_day(40), _day(2)])
    assert len(merged) == 2


def test_a_full_rebuild_replaces_everything_without_reading(monkeypatch):
    monkeypatch.setenv("GOLD_REFRESH_DAYS", "all")
    monkeypatch.setattr(
        window, "read_delta", lambda *a, **k: pytest.fail("must not read on a rebuild")
    )

    fresh = _gold_rows([_day(200), _day(1)])
    merged = window.merge_refreshed("s3://gold/t", fresh, {})

    assert len(merged) == 2


def test_an_unreadable_gold_table_fails_rather_than_truncating(monkeypatch):
    """Writing the window alone would silently drop months of history."""
    monkeypatch.setenv("GOLD_REFRESH_DAYS", "14")

    def capped(*args, **kwargs):
        raise OSError("403 Forbidden: transaction (Class B) cap exceeded")

    monkeypatch.setattr(window, "read_delta", capped)

    with pytest.raises(RuntimeError, match="to preserve history"):
        window.merge_refreshed("s3://gold/t", _gold_rows([_day(1)]), {})


def test_a_table_that_does_not_exist_yet_takes_the_window(monkeypatch):
    monkeypatch.setenv("GOLD_REFRESH_DAYS", "14")

    def missing(*args, **kwargs):
        raise OSError("no log files found, not a delta table")

    monkeypatch.setattr(window, "read_delta", missing)

    merged = window.merge_refreshed("s3://gold/t", _gold_rows([_day(1)]), {})

    assert len(merged) == 1


# ── Reading ────────────────────────────────────────────────────────────────────


def test_the_silver_read_is_pushed_down_as_a_partition_filter(monkeypatch):
    monkeypatch.setenv("GOLD_WINDOW_DAYS", "60")
    seen = {}

    def capture(path, storage_options=None, filters=None):
        seen["filters"] = filters
        return pd.DataFrame()

    monkeypatch.setattr(window, "read_delta", capture)
    window.read_silver_window("s3://silver/air_quality", {})

    assert seen["filters"] == [("partition_date", ">=", _day(60))]


def test_a_full_rebuild_reads_without_a_filter(monkeypatch):
    monkeypatch.setenv("GOLD_WINDOW_DAYS", "all")
    seen = {}

    def capture(path, storage_options=None, filters=None):
        seen["filters"] = filters
        return pd.DataFrame()

    monkeypatch.setattr(window, "read_delta", capture)
    window.read_silver_window("s3://silver/air_quality", {})

    assert seen["filters"] is None


def test_a_body_decode_failure_falls_back_to_sequential_partitions(monkeypatch):
    monkeypatch.setenv("GOLD_WINDOW_DAYS", "60")
    recent, older = _day(1), _day(2)
    reads = []

    def body_decode(*args, **kwargs):
        raise OSError("Generic S3 error: error decoding response body")

    class _Table:
        def __init__(self, path, storage_options=None):
            pass

        def files(self):
            return [
                f"partition_date={recent}/source=weather/recent.parquet",
                f"partition_date={older}/source=weather/older.parquet",
            ]

        def to_pandas(self, filters=None):
            partition = filters[0][2]
            reads.append(partition)
            return pd.DataFrame({"value": [partition]})

    monkeypatch.setattr(window, "read_delta", body_decode)
    monkeypatch.setattr(window, "DeltaTable", _Table)

    frame = window.read_silver_window("s3://silver/weather", {})

    assert reads == sorted([recent, older])
    assert sorted(frame["partition_date"]) == sorted([recent, older])


def test_sequential_fallback_names_the_unreadable_partition(monkeypatch):
    broken = _day(1)

    def body_decode(*args, **kwargs):
        raise OSError("Generic S3 error: error decoding response body")

    class _Table:
        def __init__(self, path, storage_options=None):
            pass

        def files(self):
            return [f"partition_date={broken}/source=weather/broken.parquet"]

        def to_pandas(self, filters=None):
            raise OSError("bad object")

    monkeypatch.setattr(window, "read_delta", body_decode)
    monkeypatch.setattr(window, "DeltaTable", _Table)

    with pytest.raises(RuntimeError, match=broken):
        window.read_silver_window("s3://silver/weather", {})
