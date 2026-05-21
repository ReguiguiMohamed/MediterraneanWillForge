from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from deltalake import DeltaTable

from data.storage import delta_storage_options

EXCLUDED_COUNTRIES = {"CL", "FR", "GB", "NL", "LY"}
DEFAULT_PUBLIC_DATE_WINDOW = 45


def filter_report_countries(df: pd.DataFrame) -> pd.DataFrame:
    """Remove historical ghost country rows from pre-fix OpenAQ partitions."""
    if df.empty or "country_code" not in df.columns:
        return df.copy()
    return df[~df["country_code"].isin(EXCLUDED_COUNTRIES)].copy()


def anomaly_daily_rates(anomalies: pd.DataFrame) -> pd.DataFrame:
    """Return anomaly flags and rate per partition date."""
    if anomalies.empty:
        return pd.DataFrame(
            columns=["partition_date", "anomaly_flags", "rows", "anomaly_rate_pct"]
        )

    daily = (
        anomalies.groupby("partition_date")["is_anomaly"]
        .agg(anomaly_flags="sum", rows="count")
        .reset_index()
        .sort_values("partition_date")
    )
    daily["anomaly_rate_pct"] = (
        daily["anomaly_flags"] / daily["rows"].where(daily["rows"] > 0) * 100
    ).round(2)
    return daily


def coverage_by_date(summary: pd.DataFrame) -> pd.DataFrame:
    """Return station-day coverage and source mix per partition date."""
    if summary.empty:
        return pd.DataFrame(
            columns=[
                "partition_date",
                "station_days",
                "countries",
                "sources",
                "source_count",
            ]
        )

    grouped = summary.groupby("partition_date")
    coverage = grouped.agg(
        station_days=("station_count", "sum"),
        countries=("country_code", "nunique"),
        source_count=("source", "nunique"),
    ).reset_index()
    source_labels = grouped["source"].apply(
        lambda s: ", ".join(sorted(s.dropna().unique()))
    )
    coverage = coverage.merge(
        source_labels.rename("sources").reset_index(),
        on="partition_date",
        how="left",
    )
    return coverage.sort_values("partition_date").reset_index(drop=True)


def mark_coverage_readiness(
    coverage: pd.DataFrame,
    *,
    lookback: int = 7,
    min_ratio: float = 0.45,
    max_ratio: float = 2.25,
) -> pd.DataFrame:
    """Flag dates whose coverage is comparable to the recent reporting baseline.

    This protects public charts from a partial or source-shifted freshest
    partition without hiding the raw data. Early dates are accepted until a
    lookback baseline exists.
    """
    if coverage.empty:
        return coverage.copy()

    result = coverage.copy().sort_values("partition_date").reset_index(drop=True)
    baselines: list[float | None] = []
    ratios: list[float | None] = []
    ready: list[bool] = []

    for idx, row in result.iterrows():
        prior = result.loc[max(0, idx - lookback) : idx - 1, "station_days"]
        prior = prior[prior > 0]
        if len(prior) < min(3, lookback):
            baselines.append(None)
            ratios.append(None)
            ready.append(True)
            continue

        baseline = float(prior.median())
        ratio = float(row["station_days"] / baseline) if baseline else None
        baselines.append(round(baseline, 2))
        ratios.append(round(ratio, 3) if ratio is not None else None)
        ready.append(ratio is not None and min_ratio <= ratio <= max_ratio)

    result["coverage_baseline_station_days"] = baselines
    result["coverage_ratio"] = ratios
    result["is_reporting_ready"] = ready
    return result


def reporting_dates(summary: pd.DataFrame) -> set[str]:
    """Return partition dates suitable for public charts."""
    coverage = mark_coverage_readiness(coverage_by_date(summary))
    if coverage.empty:
        return set()
    return set(
        coverage.loc[coverage["is_reporting_ready"], "partition_date"].astype(str)
    )


def latest_reporting_dates(
    summary: pd.DataFrame,
    *,
    max_dates: int = DEFAULT_PUBLIC_DATE_WINDOW,
) -> list[str]:
    """Return the latest stable dates for dense public time-series charts.

    Full historical data remains in the lake and diagnostics. This helper only
    limits charts where every additional day adds another axis label or heatmap
    column.
    """
    if max_dates < 1:
        raise ValueError("max_dates must be at least 1")
    return sorted(reporting_dates(summary))[-max_dates:]


def read_gold_table(table: str) -> pd.DataFrame:
    """Read one Gold Delta table using the standard B2/MinIO env vars."""
    gold_bucket = os.environ.get("MINIO_BUCKET_GOLD", "med-ops-mohamed-gold")
    path = f"s3://{gold_bucket}/{table}"
    return DeltaTable(path, storage_options=delta_storage_options()).to_pandas()


def write_readiness_diagnostics(
    summary: pd.DataFrame,
    anomalies: pd.DataFrame,
    output_path: str | Path,
) -> pd.DataFrame:
    """Write a compact per-date diagnostic CSV for freshness spike analysis."""
    coverage = mark_coverage_readiness(coverage_by_date(summary))
    rates = anomaly_daily_rates(anomalies)
    diagnostics = coverage.merge(rates, on="partition_date", how="left")
    for col in ("anomaly_flags", "rows", "anomaly_rate_pct"):
        if col in diagnostics.columns:
            diagnostics[col] = diagnostics[col].fillna(0)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostics.to_csv(output_path, index=False)
    return diagnostics
