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

    monkeypatch.setattr(marts, "_storage_options", lambda: {})
    monkeypatch.setattr(marts, "read_delta", lambda *a, **k: pd.DataFrame())

    with pytest.raises(RuntimeError, match="Silver layer is empty"):
        marts.run()
