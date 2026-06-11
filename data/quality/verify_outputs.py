"""Validate retained Gold Delta tables against their output contracts."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import pandas as pd
from deltalake import DeltaTable
from loguru import logger

from data.quality.run_checks import parse_partition_dates
from data.storage import delta_storage_options


@dataclass(frozen=True)
class GoldTableContract:
    name: str
    required_columns: frozenset[str]


GOLD_TABLE_CONTRACTS = (
    GoldTableContract(
        name="daily_country_summary",
        required_columns=frozenset(
            {
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
            }
        ),
    ),
    GoldTableContract(
        name="wildfire_risk_index",
        required_columns=frozenset(
            {
                "partition_date",
                "source",
                "station_id",
                "risk_index",
                "risk_level",
            }
        ),
    ),
    GoldTableContract(
        name="anomaly_alerts",
        required_columns=frozenset(
            {
                "partition_date",
                "source",
                "station_id",
                "anomaly_score",
                "is_anomaly",
            }
        ),
    ),
)


def validate_gold_frame(
    contract: GoldTableContract,
    frame: pd.DataFrame,
    target_dates: list[str],
) -> list[str]:
    """Return contract violations for one Gold table frame."""
    errors = []
    prefix = f"gold/{contract.name}"

    if frame.empty:
        return [f"{prefix}: table exists but has 0 rows"]

    missing_columns = contract.required_columns - set(frame.columns)
    if missing_columns:
        return [f"{prefix}: missing columns {sorted(missing_columns)}"]

    if target_dates:
        available_dates = set(frame["partition_date"].astype(str))
        missing_dates = sorted(set(target_dates) - available_dates)
        if missing_dates:
            errors.append(f"{prefix}: missing requested partition(s) {missing_dates}")

    if contract.name == "daily_country_summary":
        errors.extend(_validate_daily_summary(frame, prefix))
    elif contract.name == "wildfire_risk_index":
        errors.extend(_validate_wildfire_risk(frame, prefix))
    elif contract.name == "anomaly_alerts":
        errors.extend(_validate_anomaly_alerts(frame, prefix))

    return errors


def _validate_daily_summary(frame: pd.DataFrame, prefix: str) -> list[str]:
    errors = []
    negative_pm25 = frame["mean_pm2_5"].notna() & (frame["mean_pm2_5"] < 0)
    if negative_pm25.any():
        errors.append(f"{prefix}: {int(negative_pm25.sum())} negative PM2.5 rows")

    percentage_columns = (
        "who_pm25_exceed_pct",
        "who_pm10_exceed_pct",
        "who_no2_exceed_pct",
        "who_o3_exceed_pct",
    )
    for column in percentage_columns:
        invalid = frame[column].notna() & ((frame[column] < 0) | (frame[column] > 100))
        if invalid.any():
            errors.append(
                f"{prefix}: {int(invalid.sum())} {column} values outside [0, 100]"
            )
    return errors


def _validate_wildfire_risk(frame: pd.DataFrame, prefix: str) -> list[str]:
    errors = []
    invalid_risk = frame["risk_index"].notna() & (
        (frame["risk_index"] < 0) | (frame["risk_index"] > 100)
    )
    if invalid_risk.any():
        errors.append(
            f"{prefix}: {int(invalid_risk.sum())} risk_index values outside [0, 100]"
        )

    valid_levels = {"low", "moderate", "high", "extreme"}
    unknown_levels = set(frame["risk_level"].dropna().unique()) - valid_levels
    if unknown_levels:
        errors.append(f"{prefix}: unexpected risk levels {sorted(unknown_levels)}")
    return errors


def _validate_anomaly_alerts(frame: pd.DataFrame, prefix: str) -> list[str]:
    errors = []
    invalid_flags = set(frame["is_anomaly"].dropna().unique()) - {
        0,
        1,
        False,
        True,
    }
    if invalid_flags:
        errors.append(f"{prefix}: unexpected anomaly flags {sorted(invalid_flags)}")

    valid_sources = {"openmeteo", "openaq"}
    invalid_sources = set(frame["source"].dropna().unique()) - valid_sources
    if invalid_sources:
        errors.append(
            f"{prefix}: concentration model contains sources "
            f"{sorted(invalid_sources)}"
        )
    return errors


def verify_gold_outputs(
    gold_bucket: str,
    target_dates: list[str],
    storage_options: dict[str, str] | None = None,
) -> list[str]:
    """Read every Gold output and return all contract violations."""
    options = storage_options or delta_storage_options()
    errors = []

    for contract in GOLD_TABLE_CONTRACTS:
        path = f"s3://{gold_bucket}/{contract.name}"
        logger.info(f"Checking {path}")
        try:
            frame = DeltaTable(path, storage_options=options).to_pandas()
        except Exception as exc:
            errors.append(f"gold/{contract.name}: cannot read Delta table — {exc}")
            continue

        table_errors = validate_gold_frame(contract, frame, target_dates)
        errors.extend(table_errors)
        if not table_errors:
            logger.success(f"gold/{contract.name}: {len(frame)} rows, contract OK")

    return errors


def main() -> int:
    gold_bucket = os.environ.get("MINIO_BUCKET_GOLD", "gold")
    target_dates = parse_partition_dates(
        os.environ.get(
            "VERIFY_PARTITION_DATES",
            os.environ.get("PIPELINE_DATES", ""),
        )
    )
    errors = verify_gold_outputs(gold_bucket, target_dates)

    if errors:
        for error in errors:
            logger.error(error)
        return 1

    logger.success("Gold output verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
