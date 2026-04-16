"""
CI verification script: assert Gold Delta tables exist and have rows.

Both gold/daily_country_summary and gold/wildfire_risk_index are verified.
Fails with exit code 1 and a clear message on any assertion failure so the
CI job fails here rather than silently passing through to downstream steps.
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

# Required columns and value checks per Gold table.
# Derived from data/ingestion/gold/marts.py output contracts.
GOLD_TABLES = {
    "daily_country_summary": {
        "path": "s3://gold/daily_country_summary",
        "required_cols": {
            "partition_date",
            "country_code",
            "source",
            "mean_pm2_5",
            "max_pm2_5",
            "mean_pm10",
            "mean_no2",
            "mean_o3",
            "station_count",
            "who_pm25_exceed_pct",
            "who_pm10_exceed_pct",
            "who_no2_exceed_pct",
            "who_o3_exceed_pct",
        },
    },
    "wildfire_risk_index": {
        "path": "s3://gold/wildfire_risk_index",
        "required_cols": {
            "partition_date",
            "source",
            "station_id",
            "risk_index",
            "risk_level",
        },
    },
}

errors = []

for table_name, spec in GOLD_TABLES.items():
    logger.info(f"Checking gold/{table_name} ...")

    try:
        dt = DeltaTable(spec["path"], storage_options=opts)
        df = dt.to_pandas()
    except Exception as exc:
        errors.append(f"gold/{table_name}: cannot read Delta table — {exc}")
        continue

    if len(df) == 0:
        errors.append(f"gold/{table_name}: table exists but has 0 rows")
        continue

    missing_cols = spec["required_cols"] - set(df.columns)
    if missing_cols:
        errors.append(f"gold/{table_name}: missing columns {sorted(missing_cols)}")
        continue

    logger.info(f"  rows   : {len(df)}")
    logger.info(f"  source : {sorted(df['source'].unique().tolist())}")

    # ── daily_country_summary value checks ─────────────────────────────────
    if table_name == "daily_country_summary":
        bad_pm25 = df[df["mean_pm2_5"].notna() & (df["mean_pm2_5"] < 0)]
        if len(bad_pm25) > 0:
            errors.append(
                f"gold/{table_name}: {len(bad_pm25)} rows with mean_pm2_5 < 0"
            )

        for pct_col in (
            "who_pm25_exceed_pct",
            "who_pm10_exceed_pct",
            "who_no2_exceed_pct",
            "who_o3_exceed_pct",
        ):
            bad_pct = df[
                df[pct_col].notna() & ((df[pct_col] < 0) | (df[pct_col] > 100))
            ]
            if len(bad_pct) > 0:
                errors.append(
                    f"gold/{table_name}: {len(bad_pct)} rows with {pct_col} outside [0, 100]"
                )

    # ── wildfire_risk_index value checks ───────────────────────────────────
    if table_name == "wildfire_risk_index":
        bad_risk = df[
            df["risk_index"].notna()
            & ((df["risk_index"] < 0) | (df["risk_index"] > 100))
        ]
        if len(bad_risk) > 0:
            errors.append(
                f"gold/{table_name}: {len(bad_risk)} rows with risk_index outside [0, 100]"
            )

        valid_levels = {"low", "moderate", "high", "extreme"}
        unknown_levels = set(df["risk_level"].dropna().unique()) - valid_levels
        if unknown_levels:
            errors.append(
                f"gold/{table_name}: unexpected risk_level values: {sorted(unknown_levels)}"
            )

    logger.success(f"  gold/{table_name}: OK")

if errors:
    for e in errors:
        logger.error(e)
    sys.exit(1)

logger.success("Gold verification PASSED")
