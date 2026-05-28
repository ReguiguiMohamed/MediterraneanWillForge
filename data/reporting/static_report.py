from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from dotenv import load_dotenv

from data.reporting.analytics import (
    anomaly_daily_rates,
    filter_anomaly_model_sources,
    filter_report_countries,
    latest_reporting_dates,
    read_gold_table,
    reporting_dates,
    write_readiness_diagnostics,
)

REPORT_DIR = Path("docs")
PALETTE = ["#2563eb", "#16a34a", "#f97316", "#7c3aed", "#dc2626"]
MAX_DATE_TICKS = 12


def _date_tick_positions(count: int, max_ticks: int = MAX_DATE_TICKS) -> list[int]:
    if count <= 0:
        return []
    stride = max(1, int(np.ceil(count / max_ticks)))
    ticks = list(range(0, count, stride))
    if ticks[-1] != count - 1:
        ticks.append(count - 1)
    return ticks


def _style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": "#d4d4d8",
            "axes.labelcolor": "#27272a",
            "axes.titlecolor": "#18181b",
            "xtick.color": "#52525b",
            "ytick.color": "#52525b",
            "font.size": 10,
            "axes.grid": True,
            "grid.color": "#e4e4e7",
            "grid.linewidth": 0.7,
        }
    )


def _load_gold() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary = filter_report_countries(read_gold_table("daily_country_summary"))
    risk = filter_report_countries(read_gold_table("wildfire_risk_index"))
    anomalies = filter_anomaly_model_sources(
        filter_report_countries(read_gold_table("anomaly_alerts"))
    )
    return summary, risk, anomalies


def plot_anomaly_detection(
    anomalies: pd.DataFrame, display_dates: list[str], output_path: Path
) -> None:
    if anomalies.empty:
        return

    public_anomalies = anomalies[anomalies["partition_date"].isin(display_dates)].copy()
    rates = anomaly_daily_rates(public_anomalies)

    fig, axes = plt.subplots(
        1,
        3,
        figsize=(16, 5),
        gridspec_kw={"width_ratios": [1.25, 1, 1]},
    )

    ax = axes[0]
    x = np.arange(len(rates))
    ax.bar(x, rates["anomaly_rate_pct"], color="#dc2626", alpha=0.82)
    ticks = _date_tick_positions(len(rates))
    ax.set_xticks(ticks)
    ax.set_xticklabels(
        [str(rates.iloc[t]["partition_date"])[-5:] for t in ticks],
        rotation=70,
        ha="right",
        fontsize=7,
    )
    ax.yaxis.set_major_formatter(mticker.PercentFormatter())
    ax.set_ylabel("Flagged readings")
    ax.set_title("Anomaly Rate by Date", fontweight="bold")
    ax.text(
        0,
        -0.27,
        "Rate is used instead of raw counts so dates with more stations do not dominate.",
        transform=ax.transAxes,
        fontsize=8,
        color="#52525b",
    )

    ax = axes[1]
    by_country = (
        public_anomalies.groupby("country_code")["is_anomaly"]
        .agg(["sum", "count"])
        .rename(columns={"sum": "anomalies", "count": "total"})
    )
    by_country["rate"] = by_country["anomalies"] / by_country["total"] * 100
    by_country = by_country.sort_values("rate")
    ax.barh(by_country.index, by_country["rate"], color="#7c3aed", alpha=0.82)
    ax.set_xlabel("Flagged readings")
    ax.set_title("Anomaly Rate by Country", fontweight="bold")
    ax.xaxis.set_major_formatter(mticker.PercentFormatter())

    ax = axes[2]
    normal = public_anomalies.loc[public_anomalies["is_anomaly"] == 0, "anomaly_score"]
    flagged = public_anomalies.loc[public_anomalies["is_anomaly"] == 1, "anomaly_score"]
    ax.hist(
        normal,
        bins=30,
        color="#16a34a",
        alpha=0.72,
        label=f"Normal ({len(normal):,})",
    )
    ax.hist(
        flagged,
        bins=15,
        color="#dc2626",
        alpha=0.85,
        label=f"Anomaly ({len(flagged):,})",
    )
    ax.set_xlabel("Isolation Forest score")
    ax.set_ylabel("Readings")
    ax.set_title("Score Distribution", fontweight="bold")
    ax.legend(fontsize=8)

    fig.suptitle(
        f"Anomaly Detection - Latest {len(display_dates)} Stable Reporting Dates",
        fontsize=13,
        fontweight="bold",
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_source_coverage(
    summary: pd.DataFrame, ready_dates: set[str], output_path: Path
) -> None:
    public_summary = summary[summary["partition_date"].isin(ready_dates)].copy()
    if public_summary.empty:
        return

    coverage = (
        public_summary.groupby(["country_code", "source"])["station_count"]
        .sum()
        .unstack("source", fill_value=0)
    )
    coverage = coverage.loc[coverage.sum(axis=1).sort_values().index]

    fig, axes = plt.subplots(1, 2, figsize=(14, max(4, len(coverage) * 0.55)))

    ax = axes[0]
    left = np.zeros(len(coverage))
    for idx, src in enumerate(coverage.columns):
        vals = coverage[src].values
        ax.barh(
            coverage.index,
            vals,
            left=left,
            label=src,
            color=PALETTE[idx % len(PALETTE)],
            alpha=0.86,
        )
        left += vals
    ax.set_xlabel("Station-days")
    ax.set_title("Station Coverage by Source", fontweight="bold")
    ax.legend(title="Source", fontsize=8)

    ax = axes[1]
    pct = coverage.div(coverage.sum(axis=1), axis=0) * 100
    left = np.zeros(len(pct))
    for idx, src in enumerate(pct.columns):
        vals = pct[src].values
        ax.barh(
            pct.index,
            vals,
            left=left,
            label=src,
            color=PALETTE[idx % len(PALETTE)],
            alpha=0.86,
        )
        left += vals
    ax.set_xlabel("Share of station-days")
    ax.set_title("Source Mix by Country", fontweight="bold")
    ax.xaxis.set_major_formatter(mticker.PercentFormatter())
    ax.legend(title="Source", fontsize=8)

    fig.suptitle(
        "Data Source Coverage - Stable Reporting Dates",
        fontsize=13,
        fontweight="bold",
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    load_dotenv()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    _style()

    summary, _, anomalies = _load_gold()
    ready_dates = reporting_dates(summary)
    display_dates = latest_reporting_dates(summary)
    diagnostics = write_readiness_diagnostics(
        summary,
        anomalies,
        REPORT_DIR / "reporting_readiness.csv",
    )

    plot_anomaly_detection(
        anomalies,
        display_dates,
        REPORT_DIR / "anomaly_detection.png",
    )
    plot_source_coverage(summary, ready_dates, REPORT_DIR / "source_coverage.png")

    latest = diagnostics.tail(5)[
        [
            "partition_date",
            "station_days",
            "coverage_ratio",
            "is_reporting_ready",
            "anomaly_flags",
            "rows",
            "anomaly_rate_pct",
        ]
    ]
    print(latest.to_string(index=False))
    print(f"Wrote static report assets to {REPORT_DIR.resolve()}")


if __name__ == "__main__":
    main()
