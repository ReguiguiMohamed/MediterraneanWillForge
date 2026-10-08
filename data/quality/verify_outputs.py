"""Check retained Gold Delta tables against their data contracts.

The contracts live in data/contracts as Open Data Contract Standard files and
run through datacontract-cli. Each table is read from Delta once, written to a
temporary Parquet file and checked there, so the contracts cost no extra
object-store reads.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
from datacontract.data_contract import DataContract
from datacontract.model.run import ResultEnum
from loguru import logger
from open_data_contract_standard.model import OpenDataContractStandard, Server

from data.quality.run_checks import parse_partition_dates
from data.storage import delta_storage_options, read_delta

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"
GOLD_TABLES = (
    "daily_country_summary",
    "wildfire_risk_index",
    "daily_country_weather",
    "anomaly_alerts",
)


def check_contract(name: str, frame: pd.DataFrame) -> list[str]:
    """Return the failed contract checks for one Gold table frame."""
    contract = OpenDataContractStandard.from_file(
        str(CONTRACTS_DIR / f"gold_{name}.odcs.yaml")
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        contract.servers = [
            Server(server="frame", type="local", format="parquet", path=str(path))
        ]
        run = DataContract(data_contract=contract).test()

    # Parquet type checks come back as warnings, so only failures and errors
    # break the contract.
    return [
        f"gold/{name}: {check.name}: {check.reason}"
        for check in run.checks
        if check.result in (ResultEnum.failed, ResultEnum.error)
    ]


def validate_gold_frame(
    name: str,
    frame: pd.DataFrame,
    target_dates: list[str],
) -> list[str]:
    """Return contract violations and missing requested partitions."""
    errors = check_contract(name, frame)
    if target_dates and "partition_date" in frame:
        available = set(frame["partition_date"].astype(str))
        missing = sorted(set(target_dates) - available)
        if missing:
            errors.append(f"gold/{name}: missing requested partition(s) {missing}")
    return errors


def verify_gold_outputs(
    gold_bucket: str,
    target_dates: list[str],
    storage_options: dict[str, str] | None = None,
) -> list[str]:
    """Read every Gold output and return all contract violations."""
    options = storage_options or delta_storage_options()
    errors = []

    for name in GOLD_TABLES:
        path = f"s3://{gold_bucket}/{name}"
        logger.info(f"Checking {path}")
        try:
            frame = read_delta(path, options)
        except Exception as exc:
            errors.append(f"gold/{name}: cannot read Delta table: {exc}")
            continue

        table_errors = validate_gold_frame(name, frame, target_dates)
        errors.extend(table_errors)
        if not table_errors:
            logger.success(f"gold/{name}: {len(frame)} rows, contract OK")

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
