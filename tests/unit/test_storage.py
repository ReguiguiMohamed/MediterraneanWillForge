import pandas as pd
from tenacity import wait_none

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


def test_read_delta_retries_transient_s3_body_error(monkeypatch):
    """B2 truncating one response body must not fail the run."""
    calls = []

    class _FakeTable:
        def __init__(self, path, storage_options=None):
            calls.append(path)

        def to_pandas(self):
            if len(calls) < 3:
                raise OSError("Generic S3 error: error decoding response body")
            return pd.DataFrame({"ok": [1]})

    monkeypatch.setattr(storage, "DeltaTable", _FakeTable)
    monkeypatch.setattr(storage.read_delta.retry, "wait", wait_none())

    frame = storage.read_delta("s3://silver/air_quality", {})

    assert len(calls) == 3
    assert frame.equals(pd.DataFrame({"ok": [1]}))
