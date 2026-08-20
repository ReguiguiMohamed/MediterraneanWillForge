"""
CI verification script: assert Silver Delta table has rows after transformer run.

Fails with a clear message if Silver is empty or unreadable, so the CI job fails
at this step rather than silently passing through to dbt.

Exits via os._exit. delta-rs shuts its Rust runtime down during interpreter
finalization, and that teardown intermittently aborts the process:

    Silver verification PASSED
    terminate called without an active exception
    Aborted (core dumped)          -> exit 134

The work is already done and the result already logged by then, so a passing
check was reporting failure to CI. os._exit skips interpreter cleanup entirely,
which is safe here because this is a leaf script that owns no other resources.
"""

import os
import sys

from deltalake import DeltaTable
from loguru import logger

from data.storage import delta_storage_options


def _exit(code: int) -> None:
    """Leave without running interpreter finalization.

    ponytail: os._exit skips atexit handlers and buffer flushing, so stream
    flushes are explicit above it. Correct for a leaf script, never for
    library code.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def main() -> int:
    opts = delta_storage_options()

    try:
        df = DeltaTable("s3://silver/air_quality", storage_options=opts).to_pandas()
    except Exception as exc:
        logger.error(f"Cannot read Silver Delta table: {exc}")
        return 1

    logger.info(f"Silver rows      : {len(df)}")
    logger.info(f"Columns          : {sorted(df.columns.tolist())}")

    if len(df) == 0:
        logger.error(
            "Silver is empty after transformer run — check transformer output above"
        )
        return 1

    logger.info(f"partition_date   : {sorted(df['partition_date'].unique().tolist())}")
    logger.info(f"source           : {sorted(df['source'].unique().tolist())}")
    logger.success("Silver verification PASSED")
    return 0


if __name__ == "__main__":
    _exit(main())
