import pandas as pd
import pytest

import data.ingestion.gold.marts as marts


def test_run_fails_when_silver_is_unreadable(monkeypatch):
    def unreadable_table(*args, **kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(marts, "_storage_options", lambda: {})
    monkeypatch.setattr(marts, "DeltaTable", unreadable_table)

    with pytest.raises(RuntimeError, match="Cannot read Silver layer"):
        marts.run()


def test_run_fails_when_silver_is_empty(monkeypatch):
    class EmptyTable:
        def __init__(self, *args, **kwargs):
            pass

        def to_pandas(self):
            return pd.DataFrame()

    monkeypatch.setattr(marts, "_storage_options", lambda: {})
    monkeypatch.setattr(marts, "DeltaTable", EmptyTable)

    with pytest.raises(RuntimeError, match="Silver layer is empty"):
        marts.run()
