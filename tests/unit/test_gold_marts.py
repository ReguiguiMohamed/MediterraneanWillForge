import pandas as pd
import pytest
from deltalake.exceptions import TableNotFoundError

import data.ingestion.gold.marts as marts


def test_run_fails_when_silver_is_unreadable(monkeypatch):
    def unreadable_table(*args, **kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(marts, "_storage_options", lambda: {})
    monkeypatch.setattr(marts, "read_silver_window", unreadable_table)

    with pytest.raises(RuntimeError, match="Cannot read Silver layer"):
        marts.run()


def test_run_fails_when_silver_is_empty(monkeypatch):

    monkeypatch.setattr(marts, "_storage_options", lambda: {})
    monkeypatch.setattr(marts, "read_silver_window", lambda *a, **k: pd.DataFrame())

    with pytest.raises(RuntimeError, match="Silver layer is empty"):
        marts.run()


def test_weather_read_failure_is_not_hidden(monkeypatch):
    def unreadable(*args, **kwargs):
        raise OSError("bad weather object")

    monkeypatch.setattr(marts, "read_silver_window", unreadable)

    with pytest.raises(RuntimeError, match="Cannot read Silver weather"):
        marts._read_silver_weather("silver", {})


def test_missing_weather_table_is_still_optional(monkeypatch):
    def missing(*args, **kwargs):
        raise TableNotFoundError("missing")

    monkeypatch.setattr(marts, "read_silver_window", missing)

    assert marts._read_silver_weather("silver", {}).empty
