"""Shared object-store configuration for delta-rs readers and writers."""

from __future__ import annotations

import os

import pandas as pd
from deltalake import DeltaTable


def delta_storage_options(
    endpoint: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
    region: str | None = None,
) -> dict[str, str]:
    """Return delta-rs S3-compatible storage options."""
    endpoint = endpoint or os.environ["MINIO_ENDPOINT"]
    access_key = access_key or os.environ["MINIO_ACCESS_KEY"]
    secret_key = secret_key or os.environ["MINIO_SECRET_KEY"]
    region = region or os.environ.get("AWS_REGION", "eu-central-003")

    return {
        "AWS_ENDPOINT_URL": endpoint,
        "AWS_ACCESS_KEY_ID": access_key,
        "AWS_SECRET_ACCESS_KEY": secret_key,
        "AWS_REGION": region,
        "AWS_ALLOW_HTTP": "true" if endpoint.startswith("http://") else "false",
        "AWS_S3_ALLOW_UNSAFE_RENAME": "true",
    }


def read_delta(
    path: str, storage_options: dict[str, str] | None = None
) -> pd.DataFrame:
    """Read a Delta table into pandas.

    Deliberately has no retry: the failures seen in practice are B2 daily-cap
    403s, which are not transient — retrying them only burns more of the
    Class B quota that was already exhausted.
    """
    if storage_options is None:
        storage_options = delta_storage_options()
    return DeltaTable(path, storage_options=storage_options).to_pandas()
