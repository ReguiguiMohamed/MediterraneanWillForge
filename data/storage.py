"""Shared object-store configuration for delta-rs readers and writers."""

from __future__ import annotations

import os


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
        "AWS_ALLOW_HTTP": "true",
        "AWS_S3_ALLOW_UNSAFE_RENAME": "true",
    }
