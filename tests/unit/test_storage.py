import pandas as pd
import pytest

from data import storage
from data.storage import delta_storage_options


def test_delta_storage_options_use_delta_rs_aws_keys():
    opts = delta_storage_options(
        endpoint="https://s3.eu-central-003.backblazeb2.com",
        access_key="key-id",
        secret_key="app-key",
        region="eu-central-003",
    )

    assert opts["AWS_ENDPOINT_URL"] == "https://s3.eu-central-003.backblazeb2.com"
    assert opts["AWS_ACCESS_KEY_ID"] == "key-id"
    assert opts["AWS_SECRET_ACCESS_KEY"] == "app-key"
    assert opts["AWS_REGION"] == "eu-central-003"
    assert "endpoint_url" not in opts
    assert "aws_access_key_id" not in opts


def test_read_delta_does_not_retry_b2_cap_errors(monkeypatch):
    """A B2 daily-cap 403 is not transient — one attempt, no extra quota burnt."""
    calls = []

    class _CappedTable:
        def __init__(self, path, storage_options=None):
            calls.append(path)

        def to_pandas(self):
            raise OSError(
                "Generic S3 error: Client error with status 403 Forbidden: "
                "Cannot download file, download bandwidth or transaction "
                "(Class B) cap exceeded."
            )

    monkeypatch.setattr(storage, "DeltaTable", _CappedTable)

    with pytest.raises(OSError, match="cap exceeded"):
        storage.read_delta("s3://silver/air_quality", {})

    assert len(calls) == 1


def test_read_delta_passes_explicit_empty_storage_options(monkeypatch):
    """An empty dict must not silently fall back to env-derived credentials."""
    seen = {}

    class _Table:
        def __init__(self, path, storage_options=None):
            seen["opts"] = storage_options

        def to_pandas(self):
            return pd.DataFrame({"ok": [1]})

    monkeypatch.setattr(storage, "DeltaTable", _Table)
    monkeypatch.delenv("MINIO_ENDPOINT", raising=False)

    storage.read_delta("s3://silver/air_quality", {})

    assert seen["opts"] == {}
