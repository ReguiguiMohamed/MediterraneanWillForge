"""Shared object-store configuration for delta-rs readers and writers."""

from __future__ import annotations

import os

import pandas as pd
from deltalake import DeltaTable
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential


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


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=15),
    reraise=True,
    before_sleep=lambda st: logger.warning(
        f"Delta read failed (attempt {st.attempt_number}) — retrying: {st.outcome.exception()}"
    ),
)
def read_delta(
    path: str, storage_options: dict[str, str] | None = None
) -> pd.DataFrame:
    """Read a Delta table into pandas, retrying transient object-store faults.

    B2 intermittently truncates a response body mid-stream ("Generic S3 error:
    error decoding response body").  object_store only retries faults that occur
    before response headers arrive, so a mid-body failure has to be retried here.
    """
    if storage_options is None:
        storage_options = delta_storage_options()
    return DeltaTable(path, storage_options=storage_options).to_pandas()
