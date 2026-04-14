"""
CI verification script: assert Silver Delta table has rows after transformer run.

Fails with exit code 1 and a clear message if Silver is empty or unreadable,
so the CI job fails at this step rather than silently passing through to dbt.
"""

import os
import sys

from deltalake import DeltaTable
from loguru import logger

opts = {
    "endpoint_url": os.environ["MINIO_ENDPOINT"],
    "aws_access_key_id": os.environ["MINIO_ACCESS_KEY"],
    "aws_secret_access_key": os.environ["MINIO_SECRET_KEY"],
    "aws_allow_http": "true",
    "aws_s3_allow_unsafe_rename": "true",
}

try:
    dt = DeltaTable("s3://silver/air_quality", storage_options=opts)
    df = dt.to_pandas()
except Exception as exc:
    logger.error(f"Cannot read Silver Delta table: {exc}")
    sys.exit(1)

logger.info(f"Silver rows      : {len(df)}")
logger.info(f"Columns          : {sorted(df.columns.tolist())}")
if len(df) > 0:
    logger.info(f"partition_date   : {sorted(df['partition_date'].unique().tolist())}")
    logger.info(f"source           : {sorted(df['source'].unique().tolist())}")

if len(df) == 0:
    logger.error(
        "Silver is empty after transformer run — check transformer output above"
    )
    sys.exit(1)

logger.success("Silver verification PASSED")
